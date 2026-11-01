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
    wine      [{id, file, glob, mode, install_dir, sha256}] — M11b: the single
              kit-import installer that rides the kit per wine app (G6)
    postinst  [{id, units, udev, dialout, prefix, install}] — declarative M11a steps
    pkgs_fedora / pkgs_rpmfusion   merged, de-duplicated download lists

Guards (fail loudly, exit 2):
    - unknown --apps ids
    - any selected recipe still carrying PIN-ME / TBD-VERIFY-AT-PIN
    - postinst vocabulary violations (M11a, spec §5): unknown step, wine-*
      steps off a wine recipe, udev path outside the kit layout or a rule
      file missing from install/udev/
    - wine kit-import staging (M11b, spec G6): wine.installer_glob matching
      nothing or more than one file in install/kit-import/, a missing or
      mismatched wine.installer_sha256 pin, installer mode not
      setup|portable|innoextract,
      or a portable installer without wine.install_dir
    - --arch aarch64 with any non-dnf app selected — full kits are x86_64-only
      in P4 (docs/phase4-plan.md §Arch policy); dnf-only subsets keep the S4
      --forcearch path.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
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


# M11a postinst vocabulary (spec §5): what a recipe may declare, and the exact
# shapes the air-gap executor (install/lib/kit.sh gd_kit_postinst) applies.
# Bare steps are exact literals; `udev` carries a kit-relative rule path.
# Nothing else is accepted — recipes are data, never shell: the air-gap side
# has no interpreter for step contents.
POSTINST_BARE = {"units", "dialout", "wine-prefix", "wine-install"}
POSTINST_WINE_ONLY = {"wine-prefix", "wine-install"}
POSTINST_UDEV_RE = re.compile(r"udev/[A-Za-z0-9][A-Za-z0-9._-]*\.rules")


def _validate_postinst(r: dict, udev_dir: Path) -> list[str]:
    """Validate one recipe's postinst steps (M11a, spec §5) and return the
    normalized tokens: bare step names, or "install/udev/<name>.rules" for a
    udev step (already kit-relative)."""
    steps = r.get("postinst") or []
    if not isinstance(steps, list):
        raise PlanError(f"{r['id']}: postinst must be a list of steps")
    out: list[str] = []
    for step in steps:
        if isinstance(step, str):
            if step not in POSTINST_BARE:
                known = ", ".join(sorted(POSTINST_BARE))
                raise PlanError(
                    f"{r['id']}: unknown postinst step {step!r} "
                    f"(known steps: {known}; or a single-key udev: <path> map)"
                )
            name, arg = step, ""
        elif isinstance(step, dict) and set(step) == {"udev"}:
            arg = str(step["udev"])
            if not POSTINST_UDEV_RE.fullmatch(arg):
                raise PlanError(
                    f"{r['id']}: postinst.udev must be a kit-relative "
                    f"udev/<name>.rules path, got {arg!r}"
                )
            if not (udev_dir / Path(arg).name).is_file():
                raise PlanError(f"{r['id']}: postinst.udev rule missing from install/udev/: {arg}")
            name = "udev"
        else:
            raise PlanError(
                f"{r['id']}: malformed postinst step {step!r} "
                "(a bare step name, or a single-key {udev: <kit-relative path>} map)"
            )
        if name in POSTINST_WINE_ONLY and r.get("strategy") != "wine":
            raise PlanError(f"{r['id']}: postinst.{name} is only valid on wine-strategy recipes")
        # Recipe vocabulary is installer-relative (udev/<name>.rules under
        # install/); the plan/TSV carry the KIT-relative path (install/udev/…)
        # so the air-gap executor joins it against the kit root directly.
        out.append(f"install/{arg}" if arg else name)
    return out


WINE_SHA256_RE = re.compile(r"[0-9a-f]{64}")
WINE_MODES = ("setup", "portable", "innoextract")


def _resolve_wine_import(app_id: str, r: dict, install_root: Path) -> dict:
    """Resolve one wine recipe's staged kit-import artifact (M11b, spec G6).

    Returns the plan row the builder packs into kit/wine/<id>/ and kit/wine.tsv.
    Fails closed (PlanError) when the recipe is unpinned, the glob matches
    nothing or more than one staged file, or the artifact's sha256 drifts from
    the recipe's pin — same everything-pinned discipline as M6 tarballs.
    """
    w = r.get("wine") or {}
    pattern = str(w.get("installer_glob") or "").strip()
    if not pattern:
        raise PlanError(
            f"{app_id}: wine recipe missing wine.installer_glob "
            "(the user-supplied artifact pattern staged in install/kit-import/)"
        )
    pin = str(w.get("installer_sha256") or "").strip().lower()
    if not WINE_SHA256_RE.fullmatch(pin):
        raise PlanError(
            f"{app_id}: wine.installer_sha256 must be a 64-hex pin of the staged "
            "artifact (M6 discipline: the kit carries exactly what was verified)"
        )
    mode = str(w.get("installer") or "setup").strip()
    if mode not in WINE_MODES:
        raise PlanError(
            f"{app_id}: wine.installer must be one of {'|'.join(WINE_MODES)}, got {mode!r}"
        )
    install_dir = str(w.get("install_dir") or "").strip()
    if mode in ("portable", "innoextract") and not install_dir:
        raise PlanError(
            f"{app_id}: wine.installer={mode} needs wine.install_dir "
            "(the drive_c target the files unpack into)"
        )

    import_dir = install_root / "kit-import"
    matches = sorted(
        p
        for p in (import_dir.iterdir() if import_dir.is_dir() else [])
        if p.is_file() and fnmatch.fnmatch(p.name.lower(), pattern.lower())
    )
    if not matches:
        raise PlanError(
            f"{app_id}: nothing matching '{pattern}' in install/kit-import/ — drop the "
            f"user-supplied download there (G6: it rides the kit, never the repo), or "
            "drop the app from --apps"
        )
    if len(matches) > 1:
        listing = ", ".join(p.name for p in matches)
        raise PlanError(
            f"{app_id}: {len(matches)} files match '{pattern}' in install/kit-import/ — "
            f"keep exactly one (move the rest out) and retry: {listing}"
        )
    artifact = matches[0]
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if digest != pin:
        raise PlanError(
            f"{app_id}: staged {artifact.name} sha256 {digest} != recipe pin {pin} — "
            "re-stage the artifact or re-pin the recipe (supply-chain guard)"
        )
    return {
        "id": app_id,
        "file": artifact.name,
        "glob": pattern,
        "mode": mode,
        "install_dir": install_dir,
        "sha256": pin,
    }


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

    # Arch policy first (M11b: fail before any strategy work — the wine branch
    # hashes staged artifacts, and a wrong-arch plan must not get that far).
    non_dnf = sorted(i for i in sorted(set(ids)) if recs[i][0]["strategy"] != "dnf")
    if arch == "aarch64" and non_dnf:
        raise PlanError(
            "--arch aarch64 with non-dnf apps selected: full kits are x86_64-only in P4 "
            "(docs/phase4-plan.md §Arch policy — VARA/wine is x86_64-only upstream, "
            "artemis ships x86_64 only). Use a dnf-only --apps subset for aarch64 (S4)."
        )

    plan: dict = {
        "apps": [],
        "dnf": [],
        "build": [],
        "tarball": [],
        "pip": [],
        "static": [],
        "wine": [],
        "postinst": [],
    }
    udev_dir = recipes.parent / "udev"  # kit-relative udev rule targets live here
    pkgs_fed: list[str] = []
    pkgs_rf: list[str] = []
    # Installer tooling rides the kit (the spec §8 dnf class with createrepo_c /
    # python3-pip): the air-gap side extracts tarball/deb payloads with bsdtar
    # (gd_kit_extract_artifacts) and the kit repo is the only enabled repo
    # there — M10 container E2E caught its absence from the install set.
    # util-linux rides because gd_postinst_units/wine_install drop privileges
    # with runuser, which f45's util-linux-core does NOT ship (only the full
    # package does) — the M12 round-trip gate's first delegation run caught it.
    # python3-yaml rides because the delegated suite imports yaml on the system
    # interpreter (the sweep of gd.py + core/* + adapters/* found no other
    # third-party import) — M10 provisioned it on the station image; the kit
    # now carries it, so delegation works on a bare station. Same gate, same
    # first run: ModuleNotFoundError: No module named 'yaml'.
    pkgs_fed.append("bsdtar")
    pkgs_fed.append("util-linux")
    pkgs_fed.append("python3-yaml")

    for app_id in sorted(set(ids)):
        r, path = recs[app_id]
        _require_pinned(r, path)
        strat = r["strategy"]
        plan["apps"].append(app_id)

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
            plan["pip"].append(
                {
                    "id": app_id,
                    "package": f"{pkg}=={ver}" if ver else pkg,
                    "python": p.get("python") or "python3",
                }
            )

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
            # M11b (spec G6): the user-supplied installer is never built or
            # downloaded by the kit — it is staged by the operator in
            # install/kit-import/ and RIDES THE KIT to be run at the
            # operator's own station only. Fail closed unless the glob picks
            # exactly one staged artifact and its sha256 matches the pin.
            plan["wine"].append(_resolve_wine_import(app_id, r, recipes.parent))

        else:
            raise PlanError(f"{app_id}: unknown strategy {strat!r}")

        # A recipe's dnf row is honored for EVERY strategy (M10 container E2E:
        # tarball js8spotter's php-cli and pip meshtastic's python3.14 were
        # silently dropped when only the dnf/build branches read it). The
        # dnf/build branches add their own row above — don't double-append.
        if strat not in ("dnf", "build"):
            d = r.get("dnf") or {}
            if d.get("packages"):
                plan["dnf"].append(
                    {
                        "id": app_id,
                        "repo": d.get("repo", "fedora"),
                        "packages": d["packages"],
                    }
                )

        # M11a (spec §5): validate declarative postinst steps and carry them
        # to the plan — container-build.sh renders kit/postinst.tsv from these
        # rows and the air-gap executor reads only the TSV (never YAML).
        steps = _validate_postinst(r, udev_dir)
        if steps:
            row = {
                "id": app_id,
                "units": "",
                "udev": "",
                "dialout": "",
                "prefix": "",
                "install": "",
            }
            for step in steps:
                if step == "units":
                    row["units"] = "install"
                elif step == "dialout":
                    row["dialout"] = "1"
                elif step == "wine-prefix":
                    row["prefix"] = "1"
                elif step == "wine-install":
                    row["install"] = "1"
                else:  # kit-relative udev rule path ("install/udev/<name>.rules")
                    row["udev"] = step
            plan["postinst"].append(row)

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
