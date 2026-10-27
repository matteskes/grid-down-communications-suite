# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Registry + recipe consistency tests (Phase 0).

Run: python3 -m pytest tests/ -q   (skips if PyYAML is absent)
"""

from __future__ import annotations

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
