# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Wine hub tests (M5): status/start/stop/COM map over a tmp prefix."""

from __future__ import annotations

import json
import subprocess
import sys
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
