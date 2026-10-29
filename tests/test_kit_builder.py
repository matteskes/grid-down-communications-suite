# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Kit-builder resolver tests (M6c).

Run: python3 -m pytest tests/ -q   (skips if PyYAML is absent)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
yaml = pytest.importorskip("yaml")

RESOLVER = ROOT / "install" / "bundle-builder" / "resolve-kit.py"
REC_DIR = ROOT / "install" / "recipes"
PY = sys.executable


def run_resolver(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(RESOLVER), str(REC_DIR), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_resolver_routes_every_strategy():
    """One app per strategy resolves into the right plan rows."""
    p = run_resolver("--apps", "direwolf,ardop,pat,meshtastic,meshtastic-web")
    assert p.returncode == 0, p.stderr
    plan = json.loads(p.stdout)
    assert plan["apps"] == ["ardop", "direwolf", "meshtastic", "meshtastic-web", "pat"]
    assert [r["id"] for r in plan["build"]] == ["ardop"]
    assert plan["build"][0]["build_cmd"] and plan["build"][0]["sha256"]
    assert [r["id"] for r in plan["tarball"]] == ["pat"]
    assert plan["tarball"][0]["arm64_url"], "pat pin carries the arm64 row"
    assert [r["id"] for r in plan["pip"]] == ["meshtastic"]
    assert plan["pip"][0]["package"] == "meshtastic==2.7.11"
    assert [r["id"] for r in plan["static"]] == ["meshtastic-web"]
    # build recipes' runtime dnf packages merge into the download set
    assert "alsa-lib" in plan["pkgs_fedora"]


def test_resolver_arch_guard_blocks_full_kits_on_aarch64():
    """--arch aarch64 + any non-dnf app -> exit 2 pointing at the arch policy."""
    p = run_resolver("--apps", "pat", "--arch", "aarch64")
    assert p.returncode == 2
    assert "Arch policy" in p.stderr


def test_resolver_allows_aarch64_dnf_only_subset():
    p = run_resolver("--apps", "direwolf,hamlib", "--arch", "aarch64")
    assert p.returncode == 0, p.stderr
    plan = json.loads(p.stdout)
    assert plan["apps"] == ["direwolf", "hamlib"]


def test_resolver_refuses_unpinned_recipes(tmp_path):
    """A PIN-ME recipe in the set fails the plan loudly."""
    rec = yaml.safe_load((REC_DIR / "pat.yaml").read_text())
    rec["recipe"]["tarball"]["url"] = "PIN-ME"
    (tmp_path / "pat.yaml").write_text(yaml.safe_dump(rec))
    p = subprocess.run(
        [PY, str(RESOLVER), str(tmp_path), "--apps", "pat"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 2
    assert "PIN-ME" in p.stderr


def test_resolver_refuses_unknown_apps():
    p = run_resolver("--apps", "nosuchapp")
    assert p.returncode == 2
    assert "unknown apps" in p.stderr


def test_resolver_refuses_wine_apps():
    """Wine apps are kit-import only (G6) — never kit-built."""
    p = run_resolver("--apps", "vara-hf")
    assert p.returncode == 2
    assert "kit-import" in p.stderr
