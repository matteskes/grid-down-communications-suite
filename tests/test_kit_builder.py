# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Kit-builder resolver tests (M6c).

Run: python3 -m pytest tests/ -q   (skips if PyYAML is absent)
"""

from __future__ import annotations

import hashlib
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


# --- wine kit-import staging (M11b, spec G6) ----------------------------------
# Every wine test uses a tmp mirror (recipes/ + kit-import/) so the suite never
# depends on operator-staged artifacts being present on this machine.


def _fake_artifact(staging: Path, name: str) -> str:
    content = f"fake user-supplied installer: {name}\n".encode()
    (staging / name).write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _wine_env(
    tmp_path: Path,
    recipe: str = "vara-hf",
    wine_overrides: dict | None = None,
    artifacts: tuple[str, ...] = ("fake-installer.zip",),
) -> Path:
    """tmp mirror of the repo layout: <tmp>/recipes/<recipe>.yaml — glob and
    sha256 pin rewritten to the fake staged artifact — beside <tmp>/kit-import/.
    wine_overrides values of None remove the key (pin/install_dir absence)."""
    rec_dir = tmp_path / "recipes"
    staging = tmp_path / "kit-import"
    rec_dir.mkdir()
    staging.mkdir()
    pins = {name: _fake_artifact(staging, name) for name in artifacts}
    rec = yaml.safe_load((REC_DIR / f"{recipe}.yaml").read_text())
    w = rec["recipe"]["wine"]
    w["installer_glob"] = "fake-installer*"
    w["installer_sha256"] = next(iter(pins.values()), "0" * 64)
    for key, val in (wine_overrides or {}).items():
        if val is None:
            w.pop(key, None)
        else:
            w[key] = val
    (rec_dir / f"{recipe}.yaml").write_text(yaml.safe_dump(rec))
    return rec_dir


def _run_resolver_on(rec_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(RESOLVER), str(rec_dir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_resolver_routes_wine_apps(tmp_path):
    """A wine app with exactly one pinned staged artifact resolves into a
    plan['wine'] row, pulls wine into the kit dnf set (M11b: the same stack
    builds the container prefix and runs it air-gap), and keeps its
    declarative wine-* postinst rows."""
    rec_dir = _wine_env(tmp_path)
    p = _run_resolver_on(rec_dir, "--apps", "vara-hf")
    assert p.returncode == 0, p.stderr
    plan = json.loads(p.stdout)
    pin = hashlib.sha256(b"fake user-supplied installer: fake-installer.zip\n").hexdigest()
    assert plan["wine"] == [
        {
            "id": "vara-hf",
            "file": "fake-installer.zip",
            "glob": "fake-installer*",
            "mode": "setup",
            "install_dir": "",
            "sha256": pin,
        }
    ]
    assert "wine" in plan["pkgs_fedora"]
    row = next(r for r in plan["postinst"] if r["id"] == "vara-hf")
    assert (row["prefix"], row["install"]) == ("1", "1")


def test_resolver_carries_wine_portable_mode(tmp_path):
    """A portable installer (VarAC-style zip) carries its install_dir pin."""
    rec_dir = _wine_env(tmp_path, recipe="varac")
    p = _run_resolver_on(rec_dir, "--apps", "varac")
    assert p.returncode == 0, p.stderr
    row = json.loads(p.stdout)["wine"][0]
    assert (row["mode"], row["install_dir"]) == ("portable", "C:\\VarAC")


def test_resolver_all_dnf_excludes_wine_apps():
    """--all-dnf selects dnf-strategy apps only — wine kits are an explicit
    --apps choice (and never trip the wine packing machinery)."""
    p = run_resolver("--all-dnf")
    assert p.returncode == 0, p.stderr
    assert "vara-hf" not in json.loads(p.stdout)["apps"]


def test_resolver_wine_missing_staged_artifact(tmp_path):
    """G6 fail-closed: no staged artifact matching the glob -> exit 2."""
    rec_dir = _wine_env(tmp_path, artifacts=())
    p = _run_resolver_on(rec_dir, "--apps", "vara-hf")
    assert p.returncode == 2
    assert "nothing matching" in p.stderr


def test_resolver_wine_ambiguous_glob(tmp_path):
    """G6 fail-closed: more than one staged match -> exit 2 naming them."""
    rec_dir = _wine_env(tmp_path, artifacts=("fake-installer.zip", "fake-installer-2.zip"))
    p = _run_resolver_on(rec_dir, "--apps", "vara-hf")
    assert p.returncode == 2
    assert "keep exactly one" in p.stderr


def test_resolver_wine_sha256_pin_mismatch(tmp_path):
    """Supply-chain guard: staged artifact drifting from the pin fails."""
    rec_dir = _wine_env(tmp_path, wine_overrides={"installer_sha256": "b" * 64})
    p = _run_resolver_on(rec_dir, "--apps", "vara-hf")
    assert p.returncode == 2
    assert "sha256" in p.stderr


def test_resolver_wine_unpinned_sha256(tmp_path):
    rec_dir = _wine_env(tmp_path, wine_overrides={"installer_sha256": None})
    p = _run_resolver_on(rec_dir, "--apps", "vara-hf")
    assert p.returncode == 2
    assert "64-hex" in p.stderr


def test_resolver_wine_portable_needs_install_dir(tmp_path):
    rec_dir = _wine_env(tmp_path, recipe="varac", wine_overrides={"install_dir": None})
    p = _run_resolver_on(rec_dir, "--apps", "varac")
    assert p.returncode == 2
    assert "needs wine.install_dir" in p.stderr


def test_resolver_wine_arch_guard_on_aarch64(tmp_path):
    """Arch policy fires before any staging work (M11b moved the guard up)."""
    rec_dir = _wine_env(tmp_path)
    p = _run_resolver_on(rec_dir, "--apps", "vara-hf", "--arch", "aarch64")
    assert p.returncode == 2
    assert "Arch policy" in p.stderr


# --- postinst plan rows + vocabulary guards (M11a, spec §5) -------------------


def test_resolver_emits_postinst_rows():
    """Flipped recipes resolve into wide plan["postinst"] rows; apps without
    steps carry no row. The builder renders kit/postinst.tsv from these."""
    p = run_resolver("--apps", "direwolf,fldigi,hamlib")
    assert p.returncode == 0, p.stderr
    plan = json.loads(p.stdout)
    rows = {r["id"]: r for r in plan["postinst"]}
    assert set(rows) == {"direwolf", "fldigi", "hamlib"}
    for app_id, row in rows.items():
        assert row["units"] == "install"
        assert row["dialout"] == "1"
        assert row["udev"] == "" and row["prefix"] == "" and row["install"] == ""
    # step-less selection: empty list, no rows
    p = run_resolver("--apps", "pat")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["postinst"] == []


def test_resolver_refuses_unknown_postinst_step(tmp_path):
    rec = yaml.safe_load((REC_DIR / "direwolf.yaml").read_text())
    rec["recipe"]["postinst"] = ["rm -rf /"]
    (tmp_path / "direwolf.yaml").write_text(yaml.safe_dump(rec))
    p = subprocess.run(
        [PY, str(RESOLVER), str(tmp_path), "--apps", "direwolf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 2
    assert "unknown postinst step" in p.stderr


def test_resolver_refuses_wine_step_off_wine_recipe(tmp_path):
    rec = yaml.safe_load((REC_DIR / "direwolf.yaml").read_text())
    rec["recipe"]["postinst"] = ["wine-prefix"]
    (tmp_path / "direwolf.yaml").write_text(yaml.safe_dump(rec))
    p = subprocess.run(
        [PY, str(RESOLVER), str(tmp_path), "--apps", "direwolf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 2
    assert "only valid on wine-strategy" in p.stderr


def test_resolver_refuses_postinst_udev_path_outside_kit_layout(tmp_path):
    rec = yaml.safe_load((REC_DIR / "direwolf.yaml").read_text())
    rec["recipe"]["postinst"] = [{"udev": "../secrets/x.rules"}]
    (tmp_path / "direwolf.yaml").write_text(yaml.safe_dump(rec))
    p = subprocess.run(
        [PY, str(RESOLVER), str(tmp_path), "--apps", "direwolf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 2
    assert "udev/<name>.rules" in p.stderr


def test_resolver_refuses_postinst_udev_rule_missing_from_repo(tmp_path):
    """A well-shaped udev path with no file under install/udev/ fails the plan."""
    rec_dir = tmp_path / "recipes"  # mirror the repo layout: <root>/recipes + <root>/udev
    rec_dir.mkdir()
    (tmp_path / "udev").mkdir()
    rec = yaml.safe_load((REC_DIR / "direwolf.yaml").read_text())
    rec["recipe"]["postinst"] = [{"udev": "udev/99-grid-down-meshtastic.rules"}]
    (rec_dir / "direwolf.yaml").write_text(yaml.safe_dump(rec))
    p = subprocess.run(
        [PY, str(RESOLVER), str(rec_dir), "--apps", "direwolf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 2
    assert "missing from install/udev/" in p.stderr


def test_resolver_carries_udev_row_kit_relative(tmp_path):
    """A present rule file resolves into a udev row whose value is the
    KIT-relative path (install/udev/…), joinable against the kit root."""
    rec_dir = tmp_path / "recipes"
    rec_dir.mkdir()
    (tmp_path / "udev").mkdir()
    (tmp_path / "udev" / "99-grid-down-test.rules").write_text("# rule\n")
    rec = yaml.safe_load((REC_DIR / "direwolf.yaml").read_text())
    rec["recipe"]["postinst"] = [{"udev": "udev/99-grid-down-test.rules"}]
    (rec_dir / "direwolf.yaml").write_text(yaml.safe_dump(rec))
    p = subprocess.run(
        [PY, str(RESOLVER), str(rec_dir), "--apps", "direwolf"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)["postinst"]
    assert rows == [
        {
            "id": "direwolf",
            "units": "",
            "udev": "install/udev/99-grid-down-test.rules",
            "dialout": "",
            "prefix": "",
            "install": "",
        }
    ]
