#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Kit plan resolver (M6c): turn selected recipes into a JSON build plan.

Used by install/bundle-builder/build-kit.sh (host side) and by
tests/test_kit_builder.py. The plan is a single JSON object passed to
container-build.sh as GD_IN_PLAN:

    apps      selected app ids (sorted)
    dnf       [{id, repo, packages}]  — dnf recipes PLUS build recipes' runtime dnf.packages
    build     [{id, git, tag, archive_url, sha256, version, build_cmd, artifacts, build_deps}]
    tarball   [{id, url, sha256, dest, arch, format, version, arm64_url, arm64_sha256}]
    pip       [{id, package}]         — "name==version" when the recipe pins one
    static    [{id, src, serve}]
    pkgs_fedora / pkgs_rpmfusion   merged, de-duplicated download lists

Guards (fail loudly, exit 2):
    - unknown --apps ids
    - any selected recipe still carrying PIN-ME / TBD-VERIFY-AT-PIN
    - --arch aarch64 with any non-dnf app selected — full kits are x86_64-only
      in P4 (docs/phase4-plan.md §Arch policy); dnf-only subsets keep the S4
      --forcearch path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


class PlanError(Exception):
    """A guard or pin contract failed — the message goes to the operator."""


def _recipe(path: Path) -> dict:
    return yaml.safe_load(path.read_text())["recipe"]


def _require_pinned(r: dict, path: Path) -> None:
    text = path.read_text()
    if "PIN-ME" in text:
        raise PlanError(f"{r['id']}: recipe still has PIN-ME markers — pin it first (M6)")
    if "TBD-VERIFY-AT-PIN" in text:
        raise PlanError(f"{r['id']}: license still TBD-VERIFY-AT-PIN — resolve it first (M6)")


def resolve(recipes: Path, select: list[str] | None, all_dnf: bool, arch: str) -> dict:
    recs: dict[str, tuple[dict, Path]] = {}
    for p in sorted(recipes.glob("*.yaml")):
        r = _recipe(p)
        recs[r["id"]] = (r, p)

    if all_dnf:
        ids = sorted(
            i
            for i, (r, _) in recs.items()
            if r["strategy"] == "dnf" and (r.get("dnf") or {}).get("packages")
        )
    elif select:
        ids = select
    else:
        ids = ["direwolf", "fldigi", "hamlib"]  # P1 acceptance set
    unknown = [i for i in ids if i not in recs]
    if unknown:
        raise PlanError(f"unknown apps: {unknown}")

    plan: dict = {"apps": [], "dnf": [], "build": [], "tarball": [], "pip": [], "static": []}
    strategy_by_id: dict[str, str] = {}
    pkgs_fed: list[str] = []
    pkgs_rf: list[str] = []

    for app_id in sorted(set(ids)):
        r, path = recs[app_id]
        _require_pinned(r, path)
        strat = r["strategy"]
        plan["apps"].append(app_id)
        strategy_by_id[app_id] = strat

        if strat == "dnf":
            d = r.get("dnf") or {}
            plan["dnf"].append(
                {
                    "id": app_id,
                    "repo": d.get("repo", "fedora"),
                    "packages": d.get("packages") or [],
                }
            )

        elif strat == "build":
            s = r.get("source") or {}
            row = {
                "id": app_id,
                "git": s.get("git") or "",
                "tag": s.get("tag") or "",
                "archive_url": s.get("archive_url") or "",
                "sha256": s.get("sha256") or "",
                "version": s.get("version") or "",
                "build_cmd": s.get("build_cmd") or "",
                "artifacts": s.get("artifacts") or [],
                "build_deps": s.get("build_deps") or [],
            }
            if not row["sha256"] or not (row["git"] or row["archive_url"]):
                raise PlanError(f"{app_id}: build recipe missing source pin")
            if not row["build_cmd"]:
                raise PlanError(f"{app_id}: build recipe missing source.build_cmd")
            if not row["build_deps"]:
                raise PlanError(f"{app_id}: build recipe missing source.build_deps")
            plan["build"].append(row)
            d = r.get("dnf") or {}  # runtime deps of the built binary (not -devel)
            if d.get("packages"):
                plan["dnf"].append(
                    {
                        "id": app_id,
                        "repo": d.get("repo", "fedora"),
                        "packages": d["packages"],
                    }
                )

        elif strat == "tarball":
            t = r.get("tarball") or {}
            arch_urls = t.get("arch_urls") or {}
            row = {
                "id": app_id,
                "url": t.get("url") or "",
                "sha256": t.get("sha256") or "",
                "dest": t.get("dest") or "",
                "arch": t.get("arch") or "any",
                "format": t.get("format") or "",
                "version": t.get("version") or "",
                "arm64_url": arch_urls.get("arm64") or "",
                "arm64_sha256": arch_urls.get("arm64_sha256") or "",
            }
            if not (row["url"] and row["sha256"] and row["dest"] and row["format"]):
                raise PlanError(f"{app_id}: tarball recipe missing url/sha256/dest/format")
            plan["tarball"].append(row)

        elif strat in ("pip", "pip-vendored"):
            p = r.get("pip") or {}
            pkg = p.get("package") or app_id
            ver = p.get("version") or ""
            plan["pip"].append({
                "id": app_id,
                "package": f"{pkg}=={ver}" if ver else pkg,
                "python": p.get("python") or "python3",
            })

        elif strat == "static":
            s = r.get("static") or {}
            plan["static"].append(
                {
                    "id": app_id,
                    "src": s.get("src") or f"kit/static/{app_id}/",
                    "serve": s.get("serve") or "localhost",
                }
            )

        elif strat == "wine":
            raise PlanError(
                f"{app_id}: wine apps are kit-import (user-supplied installers), "
                "never kit-built — spec G6"
            )
        else:
            raise PlanError(f"{app_id}: unknown strategy {strat!r}")

    non_dnf = sorted(i for i in plan["apps"] if strategy_by_id[i] != "dnf")
    if arch == "aarch64" and non_dnf:
        raise PlanError(
            "--arch aarch64 with non-dnf apps selected: full kits are x86_64-only in P4 "
            "(docs/phase4-plan.md §Arch policy — VARA/wine is x86_64-only upstream, "
            "artemis ships x86_64 only). Use a dnf-only --apps subset for aarch64 (S4)."
        )

    for row in plan["dnf"]:
        (pkgs_rf if row["repo"] == "rpmfusion-free" else pkgs_fed).extend(row["packages"])
    plan["pkgs_fedora"] = sorted(dict.fromkeys(pkgs_fed))
    plan["pkgs_rpmfusion"] = sorted(dict.fromkeys(pkgs_rf))
    plan["apps"] = sorted(set(plan["apps"]))
    if not plan["apps"]:
        raise PlanError("no apps selected")
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description="resolve recipes into a kit build plan (JSON)")
    ap.add_argument("recipes", type=Path, help="install/recipes directory")
    ap.add_argument("--apps", default="", help="comma-separated app ids")
    ap.add_argument("--all-dnf", action="store_true", help="every dnf-strategy app")
    ap.add_argument("--arch", choices=["x86_64", "aarch64"], default="x86_64")
    args = ap.parse_args()
    select = [s.strip() for s in args.apps.split(",") if s.strip()] or None
    try:
        plan = resolve(args.recipes, select, args.all_dnf, args.arch)
    except PlanError as e:
        print(f"build-kit: {e}", file=sys.stderr)
        return 2
    print(json.dumps(plan, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
