# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Wine hub tests (M5): status/start/stop/COM map over a tmp prefix."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core import winehub  # noqa: E402
from core.winehub import WineHub, WinehubError, exe_for  # noqa: E402

APPS = [
    {
        "id": "vara-hf",
        "install": {"strategy": "wine"},
        "launch": {"via": "wine-hub", "cmd": "VARA.exe (via gd winehub)"},
    },
    {
        "id": "varac",
        "install": {"strategy": "wine"},
        "launch": {"via": "wine-hub", "cmd": "VarAC.exe"},
    },
    {
        "id": "hamlib",
        "install": {"strategy": "dnf", "packages": ["hamlib"]},
        "launch": {"via": "managed-service", "cmd": "rigctld"},
    },
]


@pytest.fixture
def hub(tmp_path) -> WineHub:
    return WineHub(APPS, state_dir=tmp_path / "state", prefix=tmp_path / "prefix")


def _bind_path(tmp_path: Path, monkeypatch, scripts: dict[str, str]) -> Path:
    """Empty PATH + generated executables — deterministic which() results."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in scripts.items():
        p = bindir / name
        p.write_text(f"#!/bin/sh\n{body}\n")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    return bindir


def test_status_reports_prefix_wine_com_and_apps(hub, tmp_path, monkeypatch):
    st = hub.status()
    assert st["prefix"] is False and st["prefix_path"] == str(hub.prefix)
    assert st["wine"] is None and st["com"] == {}
    assert [a["id"] for a in st["apps"]] == ["vara-hf", "varac"]
    assert all(a["running"] is False for a in st["apps"])

    (hub.prefix / "dosdevices").mkdir(parents=True)
    (hub.prefix / "dosdevices" / "com5").symlink_to("/dev/ttyUSB0")
    _bind_path(tmp_path, monkeypatch, {"wine": 'exec "$@"'})
    st = hub.status()
    assert st["prefix"] is True and st["wine"] is not None
    assert st["com"] == {"com5": "/dev/ttyUSB0"}


def test_exe_for_drops_prose_and_refuses_non_exe():
    assert exe_for(APPS[0]) == "VARA.exe"
    with pytest.raises(WinehubError, match="no Windows executable"):
        exe_for({"id": "hamlib", "launch": {"cmd": "rigctld"}})


def test_start_refuses_without_prefix(hub, tmp_path, monkeypatch):
    _bind_path(tmp_path, monkeypatch, {"wine": 'exec "$@"'})
    with pytest.raises(WinehubError, match="not installed yet"):
        hub.start("vara-hf")


def test_start_refuses_without_wine(hub):
    hub.prefix.mkdir()
    with pytest.raises(WinehubError, match="wine binary not found"):
        hub.start("vara-hf")


def test_start_refuses_non_wine_and_unknown_apps(hub):
    with pytest.raises(WinehubError, match="not a wine-hub app"):
        hub.start("hamlib")
    with pytest.raises(WinehubError, match="unknown app id"):
        hub.start("nope")


def test_start_stop_round_trip(hub, tmp_path, monkeypatch):
    hub.prefix.mkdir()
    _bind_path(tmp_path, monkeypatch, {"wine": "exec sleep 30"})
    out = hub.start("vara-hf")
    assert out["state"] == "running" and out["pid"] > 0
    assert Path(out["log"]).exists()

    row = next(a for a in hub.status()["apps"] if a["id"] == "vara-hf")
    assert row["running"] is True and row["pid"] == out["pid"]

    table = json.loads((hub.state_dir / "winehub.json").read_text())
    assert table["instances"]["vara-hf"]["pid"] == out["pid"]

    # idempotent start reports already-running, no second spawn
    again = hub.start("vara-hf")
    assert again["state"] == "already-running" and again["pid"] == out["pid"]

    stopped = hub.stop("vara-hf")
    assert stopped["state"] == "stopped"
    table = json.loads((hub.state_dir / "winehub.json").read_text())
    assert "vara-hf" not in table["instances"]
    row = next(a for a in hub.status()["apps"] if a["id"] == "vara-hf")
    assert row["running"] is False


def test_stop_removes_stale_entry_and_refuses_when_not_running(hub):
    with pytest.raises(WinehubError, match="not running under the wine hub"):
        hub.stop("vara-hf")
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()  # pid is guaranteed dead now
    hub.state_dir.mkdir(parents=True)
    (hub.state_dir / "winehub.json").write_text(
        json.dumps({"version": 1, "instances": {"vara-hf": {"pid": proc.pid}}})
    )
    assert hub.stop("vara-hf")["state"] == "stopped (stale entry removed)"
    assert hub.status()["apps"][0]["running"] is False


def test_map_and_unmap_round_trip(hub):
    hub.prefix.mkdir()
    device = hub.prefix / "fake-tty"
    device.write_text("")
    assert hub.map_serial("COM5", str(device))["state"] == "mapped"
    assert hub.com_map() == {"com5": str(device)}
    assert hub.map_serial("com5", str(device))["state"] == "already-mapped"

    other = hub.prefix / "other"
    other.write_text("")
    with pytest.raises(WinehubError, match="unmap it first"):
        hub.map_serial("com5", str(other))

    assert hub.unmap("com5")["state"] == "unmapped"
    assert hub.com_map() == {}
    with pytest.raises(WinehubError, match="not mapped"):
        hub.unmap("com5")


def test_map_validates_name_device_and_prefix(hub, tmp_path):
    with pytest.raises(WinehubError, match="bad COM name"):
        hub.map_serial("com99", "/dev/null")
    with pytest.raises(WinehubError, match="not installed yet"):
        hub.map_serial("com5", "/dev/null")
    hub.prefix.mkdir()
    with pytest.raises(WinehubError, match="does not exist"):
        hub.map_serial("com5", str(tmp_path / "absent"))
    with pytest.raises(WinehubError, match="bad COM name"):
        hub.unmap("com0")


def test_module_wine_prefix_is_the_single_source(tmp_path, monkeypatch):
    """doctor + install_state read winehub.WINE_PREFIX — moving it moves both."""
    fake = tmp_path / "prefix"
    fake.mkdir()
    monkeypatch.setattr(winehub, "WINE_PREFIX", fake)
    assert winehub.WineHub([]).status()["prefix"] is True


# ------------------------------------------- P3: install probes + init/install

FAKE_WINE = (
    'PATH="/usr/bin:/bin"   # _bind_path restricts PATH to the fake bin dir\n'
    'for a in "$@"; do echo "ARG: $a" >> "$FAKE_WINE_LOG"; done\n'
    'case "$*" in\n'
    '  *wineboot*) mkdir -p "$WINEPREFIX/drive_c" ;;\n'
    "  *setup*.exe*)\n"
    '    mkdir -p "$WINEPREFIX/drive_c/VARA HF"\n'
    '    : > "$WINEPREFIX/drive_c/VARA HF/VARA.exe"\n'
    "    ;;\n"
    "esac"
)


def _fake_wine(hub: WineHub, tmp_path: Path, monkeypatch, body: str = FAKE_WINE) -> Path:
    """Fake wine/wineboot on PATH + an initialized prefix; returns its call log."""
    log = tmp_path / "wine-calls.log"
    monkeypatch.setenv("FAKE_WINE_LOG", str(log))
    _bind_path(tmp_path, monkeypatch, {"wine": body})
    (hub.prefix / "drive_c").mkdir(parents=True)
    return log


def test_status_includes_per_app_install_probe(hub):
    rows = {a["id"]: a for a in hub.status()["apps"]}
    assert rows["vara-hf"]["installed"] is False and rows["vara-hf"]["exe_path"] is None
    exe = hub.prefix / "drive_c" / "VARA HF" / "VARA.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    rows = {a["id"]: a for a in hub.status()["apps"]}
    assert rows["vara-hf"]["installed"] is True and rows["vara-hf"]["exe_path"] == str(exe)
    assert rows["varac"]["installed"] is False


def test_probe_prefers_recipe_candidates_and_dedups(hub, tmp_path):
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "vara-hf.yaml").write_text(
        "recipe:\n  id: vara-hf\n  strategy: wine\n  wine:\n    exes: [NEWNAME.exe, VARA.exe]\n"
    )
    hub.recipes_dir = recipes
    dir_ = hub.prefix / "drive_c" / "somewhere"
    dir_.mkdir(parents=True)
    (dir_ / "VARA.exe").write_bytes(b"MZ")
    pr = hub.probe("vara-hf")
    assert pr["installed"] is True and pr["exe_path"].endswith("VARA.exe")
    assert pr["candidates"] == ["NEWNAME.exe", "VARA.exe"]  # launch.cmd token deduped


def test_find_exe_is_case_insensitive_across_drive_c(hub):
    dir_ = hub.prefix / "drive_c" / "Deep" / "Dir"
    dir_.mkdir(parents=True)
    (dir_ / "VARA.EXE").write_bytes(b"MZ")
    assert [p.name for p in winehub.find_exe(hub.prefix, "vara.exe")] == ["VARA.EXE"]
    assert winehub.find_exe(hub.prefix, "absent.exe") == []


def test_resolve_installer_explicit_path(hub, tmp_path):
    direct = tmp_path / "elsewhere" / "VARA HF setup.exe"
    direct.parent.mkdir(parents=True)
    direct.write_bytes(b"x")
    assert hub.resolve_installer("vara-hf", direct) == direct
    assert hub.resolve_installer("vara-hf", str(direct)) == direct
    with pytest.raises(WinehubError, match="does not exist"):
        hub.resolve_installer("vara-hf", tmp_path / "nope.exe")


def test_resolve_installer_glob_needs_exactly_one_match(hub, tmp_path):
    hub.kit_import = tmp_path / "absent"
    hub.recipes_dir = tmp_path / "recipes"
    hub.recipes_dir.mkdir()
    (hub.recipes_dir / "vara-hf.yaml").write_text(
        'recipe:\n  id: vara-hf\n  strategy: wine\n  wine:\n    installer_glob: "VARA HF*"\n'
    )
    with pytest.raises(WinehubError, match="staging dir"):
        hub.resolve_installer("vara-hf")
    hub.kit_import = tmp_path / "kit-import"
    hub.kit_import.mkdir()
    with pytest.raises(WinehubError, match="nothing matching"):
        hub.resolve_installer("vara-hf")
    (hub.kit_import / "VARA HF setup.exe").write_bytes(b"x")
    (hub.kit_import / "VARA HF setup 2.exe").write_bytes(b"x")
    with pytest.raises(WinehubError, match="2 files match"):
        hub.resolve_installer("vara-hf")
    (hub.kit_import / "VARA HF setup 2.exe").unlink()
    assert hub.resolve_installer("vara-hf").name == "VARA HF setup.exe"


def test_resolve_installer_without_glob_names_the_recipe(hub, tmp_path):
    hub.kit_import = tmp_path / "kit-import"
    hub.kit_import.mkdir()
    hub.recipes_dir = tmp_path / "recipes"  # no recipe file for vara-hf here
    hub.recipes_dir.mkdir()
    with pytest.raises(WinehubError, match="no installer_glob"):
        hub.resolve_installer("vara-hf")


def test_init_prefix_runs_wineboot_and_is_idempotent(hub, tmp_path, monkeypatch):
    log = tmp_path / "wine-calls.log"
    monkeypatch.setenv("FAKE_WINE_LOG", str(log))
    _bind_path(tmp_path, monkeypatch, {"wine": FAKE_WINE})
    out = hub.init_prefix()
    assert out["state"] == "initialized" and (hub.prefix / "drive_c").is_dir()
    assert "ARG: wineboot" in log.read_text()
    assert hub.init_prefix()["state"] == "already-initialized"


def test_init_prefix_failure_names_the_log(hub, tmp_path, monkeypatch):
    _bind_path(tmp_path, monkeypatch, {"wine": "exit 3"})
    with pytest.raises(WinehubError, match="rc 3"):
        hub.init_prefix()


def test_install_runs_silent_installer_and_probes(hub, tmp_path, monkeypatch):
    log = _fake_wine(hub, tmp_path, monkeypatch)
    kit = tmp_path / "kit-import"
    kit.mkdir()
    setup = kit / "VARA HF setup.exe"
    setup.write_bytes(b"MZ")
    out = hub.install("vara-hf", setup)
    assert out["state"] == "installed" and out["exe_path"].endswith("VARA.exe")
    text = log.read_text()
    assert "ARG: /VERYSILENT" in text and "ARG: /SUPPRESSMSGBOXES" in text
    assert "ARG: /NORESTART" in text
    assert "install vara-hf" in Path(out["log"]).read_text()
    calls = len(log.read_text().splitlines())
    again = hub.install("vara-hf", setup)  # idempotent: no second wine run
    assert again["state"] == "already-installed"
    assert len(log.read_text().splitlines()) == calls


def test_install_from_zip_extracts_runs_and_cleans_up(hub, tmp_path, monkeypatch):
    log = _fake_wine(hub, tmp_path, monkeypatch)
    hub.kit_import = tmp_path / "kit-import"
    hub.kit_import.mkdir()
    zip_path = hub.kit_import / "VARA HF v4.7.2.zip"  # matches the real recipe's installer_glob
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("VARA HF setup (Run as Administrator).exe", b"MZ")
    out = hub.install("vara-hf")  # no explicit path: resolved from kit-import/
    assert out["state"] == "installed"
    extracted = [
        line[5:]
        for line in log.read_text().splitlines()
        if line.startswith("ARG: ") and "gd-installer-" in line
    ]
    assert extracted and not Path(extracted[0]).exists()  # temp extraction dir cleaned


def test_install_reports_silent_failure_when_no_exe_appears(hub, tmp_path, monkeypatch):
    _fake_wine(
        hub,
        tmp_path,
        monkeypatch,
        body='for a in "$@"; do echo "ARG: $a" >> "$FAKE_WINE_LOG"; done',
    )
    kit = tmp_path / "kit-import"
    kit.mkdir()
    setup = kit / "VARA HF setup.exe"
    setup.write_bytes(b"MZ")
    with pytest.raises(WinehubError, match="no candidate exe appeared"):
        hub.install("vara-hf", setup)
    install_log = hub.state_dir / "logs" / "winehub" / "install-vara-hf.log"
    assert "install vara-hf" in install_log.read_text()


def test_install_refuses_non_wine_apps(hub, tmp_path, monkeypatch):
    _bind_path(tmp_path, monkeypatch, {"wine": 'exec "$@"'})
    with pytest.raises(WinehubError, match="not a wine-hub app"):
        hub.install("hamlib")
