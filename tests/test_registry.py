# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Registry + recipe consistency tests (Phase 0).

Run: python3 -m pytest tests/ -q   (skips if PyYAML is absent)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

yaml = pytest.importorskip("yaml")

from core import registry  # noqa: E402  (after sys.path insert)

REG_DIR = ROOT / "control" / "registry" / "apps"
REC_DIR = ROOT / "install" / "recipes"
VALID_CATEGORIES = {
    "core",
    "hf",
    "winlink",
    "packet",
    "aprs",
    "voice_sstv",
    "map_weather",
    "reference",
    "monitor",
    "mesh",
    "av",
}


@pytest.fixture(scope="module")
def apps() -> list[dict]:
    return registry.load_registry(REG_DIR)


@pytest.fixture(scope="module")
def bundles() -> dict:
    return registry.load_bundles(ROOT / "control" / "registry" / "bundles.yaml")


def test_catalog_is_populated(apps):
    assert len(apps) >= 40, f"expected >=40 apps, found {len(apps)}"


def test_ids_unique_and_match_files(apps):
    ids = [a["id"] for a in apps]
    assert len(ids) == len(set(ids))
    files = {p.stem for p in REG_DIR.glob("*.yaml")}
    assert files == set(ids)


def test_categories_valid(apps):
    bad = [(a["id"], a["category"]) for a in apps if a["category"] not in VALID_CATEGORIES]
    assert bad == []


def test_launch_shape(apps):
    for a in apps:
        assert a["launch"]["via"] in {
            "managed-service",
            "desktop-app",
            "local-service",
            "cli-tool",
            "wine-hub",
        }, a["id"]
        assert isinstance(a["launch"]["cmd"], str) and a["launch"]["cmd"], a["id"]


def test_bundles_reference_known_apps(apps, bundles):
    ids = {a["id"] for a in apps}
    for name, b in bundles.items():
        unknown = set(b.get("members", [])) - ids
        assert unknown == set(), f"bundle {name} references unknown apps: {unknown}"
    assert "mesh" in bundles
    assert "echolink" not in bundles  # excluded by design (spec §3)


def test_recipes_exist_and_match_strategy(apps):
    for a in apps:
        rpath = REC_DIR / f"{a['id']}.yaml"
        assert rpath.exists(), f"missing recipe for {a['id']}"
        r = yaml.safe_load(rpath.read_text())["recipe"]
        assert r["id"] == a["id"], rpath
        assert (
            r["strategy"] == a["install"]["strategy"]
        ), f"{a['id']}: recipe strategy {r['strategy']} != registry {a['install']['strategy']}"


def test_wine_recipes_never_embed_download_urls():
    for rpath in REC_DIR.glob("*.yaml"):
        r = yaml.safe_load(rpath.read_text())["recipe"]
        if r["strategy"] == "wine":
            blob = rpath.read_text().lower()
            assert (
                "http://" not in blob and "https://" not in blob
            ), f"{rpath.name}: wine recipes must not embed download URLs (G6)"


def test_dnf_recipes_declare_repo(apps):
    """Every dnf recipe names its source repo (kit splits repos at build time)."""
    for a in apps:
        if a["install"]["strategy"] != "dnf":
            continue
        r = yaml.safe_load((REC_DIR / f"{a['id']}.yaml").read_text())["recipe"]
        repo = (r.get("dnf") or {}).get("repo")
        assert repo in {"fedora", "rpmfusion-free"}, f"{a['id']}: bad dnf.repo: {repo!r}"
    # ffmpeg comes from RPM Fusion (audit decision — docs/audit/f45-package-audit.md)
    ff = yaml.safe_load((REC_DIR / "ffmpeg.yaml").read_text())["recipe"]
    assert ff["dnf"]["repo"] == "rpmfusion-free"


def test_recipes_declare_license(apps):
    """M0 (spec §13): every recipe records a license for attribution/manifest.

    `wine` recipes carry `wine.license: user-supplied` instead; `TBD-VERIFY-AT-PIN`
    is an allowed value until the recipe's PIN-ME fields are resolved.
    """
    for a in apps:
        r = yaml.safe_load((REC_DIR / f"{a['id']}.yaml").read_text())["recipe"]
        if r["strategy"] == "wine":
            assert r.get("wine", {}).get("license") == "user-supplied", a["id"]
        else:
            lic = r.get("license")
            assert isinstance(lic, str) and lic.strip(), f"{a['id']}: recipe missing license"


def test_build_recipes_are_pinned(apps):
    """M6a pin contract: every build-strategy recipe carries a fully resolved
    source pin (repo+tag or archive_url, 64-hex sha256, version, verified
    build_deps). Phase4-plan docs/phase4-plan.md "Pin format".
    """
    for a in apps:
        rpath = REC_DIR / f"{a['id']}.yaml"
        r = yaml.safe_load(rpath.read_text())["recipe"]
        if r["strategy"] != "build":
            continue
        assert "PIN-ME" not in rpath.read_text(), f"{a['id']}: build recipe still has PIN-ME"
        s = r.get("source") or {}
        assert re.fullmatch(
            r"[0-9a-f]{64}", s.get("sha256") or ""
        ), f"{a['id']}: source.sha256 must be 64 hex"
        assert s.get("version"), f"{a['id']}: missing source.version"
        assert s.get("git") or s.get(
            "archive_url"
        ), f"{a['id']}: build source needs git(+tag) or archive_url"
        if s.get("git"):
            assert s.get("tag"), f"{a['id']}: git pin needs source.tag"
        deps = s.get("build_deps")
        assert (
            isinstance(deps, list) and deps and all(isinstance(d, str) and d for d in deps)
        ), f"{a['id']}: source.build_deps must be a non-empty list (M6a pre-pass)"
        assert s.get("build_container"), f"{a['id']}: missing build_container"
        assert (
            r.get("license") != "TBD-VERIFY-AT-PIN"
        ), f"{a['id']}: license must be resolved after pin"


def test_tarball_recipes_are_pinned(apps):
    """M6b pin contract: every tarball-strategy recipe carries a fully resolved
    artifact pin (https url, 64-hex sha256, dest, arch, format)."""
    formats = {"tar.gz", "deb", "rpm", "zip", "npm"}
    for a in apps:
        rpath = REC_DIR / f"{a['id']}.yaml"
        r = yaml.safe_load(rpath.read_text())["recipe"]
        if r["strategy"] != "tarball":
            continue
        assert "PIN-ME" not in rpath.read_text(), f"{a['id']}: tarball recipe still has PIN-ME"
        t = r.get("tarball") or {}
        assert str(t.get("url", "")).startswith("https://"), f"{a['id']}: tarball.url must be https"
        assert re.fullmatch(
            r"[0-9a-f]{64}", t.get("sha256") or ""
        ), f"{a['id']}: tarball.sha256 must be 64 hex"
        assert str(t.get("dest", "")).startswith(
            "/opt/grid-down/"
        ), f"{a['id']}: tarball.dest must live under /opt/grid-down/"
        assert t.get("arch") in {"x86_64", "any"}, f"{a['id']}: tarball.arch must be x86_64|any"
        assert t.get("format") in formats, f"{a['id']}: bad tarball.format"
        assert (
            r.get("license") != "TBD-VERIFY-AT-PIN"
        ), f"{a['id']}: license must be resolved after pin"


def test_no_pin_me_markers(apps):
    """M6 gate: no recipe carries unresolved PIN-ME / TBD-VERIFY-AT-PIN markers."""
    for a in apps:
        text = (REC_DIR / f"{a['id']}.yaml").read_text()
        assert "PIN-ME" not in text, f"{a['id']}: PIN-ME marker remains"
        assert "TBD-VERIFY-AT-PIN" not in text, f"{a['id']}: TBD-VERIFY-AT-PIN remains"


# --- postinst contract (M11a, spec §5) ----------------------------------------
POSTINST_BARE = {"units", "dialout", "wine-prefix", "wine-install"}
POSTINST_WINE_ONLY = {"wine-prefix", "wine-install"}
POSTINST_UDEV_RE = re.compile(r"udev/[A-Za-z0-9][A-Za-z0-9._-]*\.rules")


def _postinst_names(r: dict) -> set[str]:
    """Step names of a recipe's postinst list (map form contributes its key)."""
    return {s if isinstance(s, str) else next(iter(s)) for s in r.get("postinst") or []}


def _postinst_text(r: dict) -> str:
    """Steps rendered as plain text — map form contributes its VALUE (the rule
    path), not its repr (which carries braces/quotes of its own)."""
    parts: list[str] = []
    for s in r.get("postinst") or []:
        if isinstance(s, dict):
            parts.extend(str(v) for v in s.values())
        else:
            parts.append(str(s))
    return " ".join(parts)


def test_postinst_known_steps_only(apps):
    """M11a vocabulary: every step is a known bare literal or a single-key
    `udev:` map with a kit-relative rule path. Recipes are data, never shell."""
    for a in apps:
        r = yaml.safe_load((REC_DIR / f"{a['id']}.yaml").read_text())["recipe"]
        for step in r.get("postinst") or []:
            if isinstance(step, str):
                assert step in POSTINST_BARE, f"{a['id']}: unknown postinst step {step!r}"
            elif isinstance(step, dict):
                assert set(step) == {"udev"}, f"{a['id']}: postinst map must be udev-only: {step!r}"
                assert POSTINST_UDEV_RE.fullmatch(str(step["udev"])), (
                    f"{a['id']}: postinst.udev must be udev/<name>.rules "
                    f"(kit-relative), got {step['udev']!r}"
                )
            else:
                raise AssertionError(f"{a['id']}: malformed postinst step {step!r}")


def test_postinst_declarative_only_no_shell(apps):
    """No command strings or metacharacters anywhere in postinst — step text
    is exact literals and rule paths only; the executor has no interpreter
    for step contents by design."""
    for a in apps:
        r = yaml.safe_load((REC_DIR / f"{a['id']}.yaml").read_text())["recipe"]
        blob = _postinst_text(r)
        assert not re.search(
            r"[;|&$`><\\'\"(){}!\[\]]", blob
        ), f"{a['id']}: postinst carries shell/metacharacters: {blob!r}"


def test_postinst_wine_steps_only_on_wine_recipes(apps):
    for a in apps:
        r = yaml.safe_load((REC_DIR / f"{a['id']}.yaml").read_text())["recipe"]
        if r["strategy"] != "wine":
            stray = _postinst_names(r) & POSTINST_WINE_ONLY
            assert (
                not stray
            ), f"{a['id']}: wine-only postinst steps on {r['strategy']} recipe: {sorted(stray)}"


def test_postinst_udev_targets_exist_in_kit_layout(apps):
    """A declared udev step ships its rule file under install/udev/ (the kit
    carries it verbatim; the executor installs it to /etc/udev/rules.d)."""
    for a in apps:
        r = yaml.safe_load((REC_DIR / f"{a['id']}.yaml").read_text())["recipe"]
        for step in r.get("postinst") or []:
            if isinstance(step, dict) and "udev" in step:
                rule = ROOT / "install" / "udev" / Path(str(step["udev"])).name
                assert rule.is_file(), f"{a['id']}: missing rule file install/{step['udev']}"


def test_postinst_m11a_flips_declared():
    """The M11a recipe flips: the P1 acceptance apps get units+dialout; the
    VARA family gets dialout+wine-prefix+wine-install (kit-import artifacts
    and the prefix tarball land at M11b). meshtasticd deliberately stays []."""
    expected = {
        "direwolf": {"units", "dialout"},
        "fldigi": {"units", "dialout"},
        "hamlib": {"units", "dialout"},
        "vara-hf": {"dialout", "wine-prefix", "wine-install"},
        "vara-fm": {"dialout", "wine-prefix", "wine-install"},
        "varac": {"dialout", "wine-prefix", "wine-install"},
        "meshtasticd": set(),
    }
    for app_id, names in expected.items():
        r = yaml.safe_load((REC_DIR / f"{app_id}.yaml").read_text())["recipe"]
        got = _postinst_names(r)
        assert got == names, f"{app_id}: postinst {sorted(got)} != {sorted(names)}"
