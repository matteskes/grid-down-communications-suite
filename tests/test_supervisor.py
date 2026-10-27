# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Supervisor lifecycle tests (M1).

Hermetic: a fake in-memory registry, a tmp state dir, and python sleeper
processes — no real radio apps, no /opt, no network.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core.supervisor import Supervisor, SupervisorError, pid_alive  # noqa: E402


def _sleeper(secs: float = 60) -> str:
    return shlex.join([sys.executable, "-c", f"import time; time.sleep({secs})"])


def _rig_stub(tmp_path: Path) -> str:
    """Flag-swallowing stand-in for the rigctld binary (M2: argv0 comes from
    the registry entry; all flags are profile-built and this stub ignores them)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    rig = bindir / "rigctld-fake"
    rig.write_text("#!/bin/sh\nexec sleep 60\n")
    rig.chmod(0o755)
    return str(rig)


def _apps(rig_cmd: str = "rigctld") -> list[dict]:
    return [
        {"id": "hamlib", "launch": {"via": "managed-service", "cmd": rig_cmd}},
        {"id": "term", "launch": {"via": "desktop-app", "cmd": _sleeper()}},
        {"id": "dep", "launch": {"via": "desktop-app", "cmd": _sleeper()}},
        {"id": "dep2", "launch": {"via": "desktop-app", "cmd": _sleeper()}},
        {
            "id": "bad",
            "launch": {"via": "managed-service", "cmd": "ardopc 8515 <capture> <playback>"},
        },
        {"id": "vara", "launch": {"via": "wine-hub", "cmd": "VARA.exe (via gd winehub)"}},
        {"id": "tool", "launch": {"via": "cli-tool", "cmd": "meshtastic"}},
        {
            "id": "commstatone",
            "launch": {
                "via": "local-service",
                "cmd": "python3 -m http.server 8090 (served by suite)",
            },
        },
        {
            "id": "js8spotter",
            "launch": {"via": "local-service", "cmd": "php -S 127.0.0.1:8091 (served by suite)"},
        },
    ]


BUNDLES = {
    "wb": {"members": ["hamlib", "dep"]},
    "wb2": {"members": ["hamlib", "dep2"]},
    "mixed": {"members": ["hamlib", "tool", "vara", "term"]},
}


@pytest.fixture()
def sup(tmp_path):
    s = Supervisor(
        _apps(_rig_stub(tmp_path)),
        bundles=BUNDLES,
        state_dir=tmp_path / "state",
        app_root=tmp_path / "opt",
    )
    yield s
    for entry in list(s.state["instances"].values()):
        try:
            os.killpg(entry["pgid"], signal.SIGKILL)
        except OSError:
            pass


def _wait_gone(pid: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_launch_status_stop_lifecycle(sup):
    res = sup.launch("term")
    assert res["state"] == "running"
    assert pid_alive(res["pid"])
    assert sup.is_running("term")
    assert Path(res["log"]).exists()
    row = next(r for r in sup.status()["apps"] if r["id"] == "term")
    assert row["state"] == "running" and row["pid"] == res["pid"]
    out = sup.stop("term")
    assert out["state"] == "stopped"
    assert not sup.is_running("term")
    assert _wait_gone(res["pid"])


def test_rig_singleton_autostart_and_guard(sup):
    sup.launch("dep", bundle="wb")
    assert sup.is_running("hamlib")  # auto-started before the radio app (spec §6.4)
    with pytest.raises(SupervisorError, match="rig-dependent"):
        sup.stop("hamlib")
    sup.stop("dep")
    sup.stop("hamlib")  # last dependent is down -> allowed now
    assert not sup.is_running("hamlib")


def test_stop_bundle_keeps_rig_while_other_dependents_alive(sup):
    sup.launch("dep", bundle="wb")
    sup.launch("dep2", bundle="wb2")
    out = sup.stop_bundle("wb")
    assert not sup.is_running("dep")
    assert sup.is_running("hamlib")  # still needed by dep2 (other bundle)
    assert any("left running" in n for n in out["notes"])
    sup.stop("dep2")
    sup.stop("hamlib")
    assert sup.running_ids() == []


def test_launch_bundle_skips_wine_and_cli(sup):
    out = sup.launch_bundle("mixed")
    assert sup.is_running("term") and sup.is_running("hamlib")
    assert any("tool" in n for n in out["notes"])
    assert any("vara" in n for n in out["notes"])


def test_stop_all_teardown(sup):
    sup.launch("dep", bundle="wb")
    sup.launch("term")
    sup.stop_all()
    assert sup.running_ids() == []
    assert sup.state["instances"] == {}


def test_placeholder_cmd_refused(sup):
    with pytest.raises(SupervisorError, match="preset unresolved"):
        sup.launch("bad")
    assert "bad" not in sup.state["instances"]


def test_wine_and_cli_tool_refused(sup):
    with pytest.raises(SupervisorError, match="winehub"):
        sup.launch("vara")
    with pytest.raises(SupervisorError, match="interactive"):
        sup.launch("tool")


def test_unknown_app_and_bundle(sup):
    with pytest.raises(SupervisorError, match="unknown app"):
        sup.launch("nope")
    with pytest.raises(SupervisorError, match="unknown bundle"):
        sup.stop_bundle("nope")


def test_stop_when_not_running(sup):
    with pytest.raises(SupervisorError, match="not running"):
        sup.stop("term")


def test_stale_detection_and_clear(sup):
    res = sup.launch("term")
    os.kill(res["pid"], signal.SIGKILL)
    os.waitpid(res["pid"], 0)  # reap so the pid is definitively gone
    assert not sup.is_running("term")
    assert "term" in sup.stale_ids()
    out = sup.stop("term")
    assert out["state"] == "stale-cleared"
    assert sup.stale_ids() == []


def test_state_persists_across_instances(tmp_path):
    s1 = Supervisor(
        _apps(_rig_stub(tmp_path)),
        bundles=BUNDLES,
        state_dir=tmp_path / "state",
        app_root=tmp_path / "opt",
    )
    s1.launch("term")
    s2 = Supervisor(
        _apps(_rig_stub(tmp_path)),
        bundles=BUNDLES,
        state_dir=tmp_path / "state",
        app_root=tmp_path / "opt",
    )
    assert s2.is_running("term")  # second process sees the same state file
    s2.stop("term")
    assert not s1.is_running("term")
    assert (tmp_path / "state" / "supervisor.json").exists()


def test_local_service_missing_docroot_refused(sup):
    with pytest.raises(SupervisorError, match="not installed"):
        sup.launch("commstatone")


def test_local_service_cmd_builder(sup, tmp_path):
    docroot = tmp_path / "opt" / "commstatone"
    docroot.mkdir(parents=True)
    argv, cwd = sup._local_service_cmd(sup.app("commstatone"))
    assert argv[1:3] == ["-m", "http.server"]
    assert "8090" in argv and "--bind" in argv and "127.0.0.1" in argv
    assert "--directory" in argv and cwd == docroot


def test_js8spotter_needs_php_or_errors(sup, tmp_path):
    (tmp_path / "opt" / "js8spotter").mkdir(parents=True)
    if shutil.which("php"):
        argv, _ = sup._local_service_cmd(sup.app("js8spotter"))
        assert "php" in argv[0] and "127.0.0.1:8091" in argv
    else:
        with pytest.raises(SupervisorError, match="php-cli"):
            sup._local_service_cmd(sup.app("js8spotter"))


# ------------------------------------------------------- M2: rig from profile
def _sup_with_profile(tmp_path, prof):
    return Supervisor(
        _apps(_rig_stub(tmp_path)),
        bundles=BUNDLES,
        state_dir=tmp_path / "state",
        app_root=tmp_path / "opt",
        profile=prof,
    )


def _kill_all(s):
    for entry in list(s.state["instances"].values()):
        try:
            os.killpg(entry["pgid"], signal.SIGKILL)
        except OSError:
            pass


def test_rig_argv_from_profile(tmp_path):
    prof = {"rig": {"model": "3073", "path": "/dev/ttyUSB0", "baud": 38400}}
    argv, notes = _sup_with_profile(tmp_path, prof).rig_argv()
    assert notes == []
    assert argv[argv.index("-m") + 1] == "3073"
    assert argv[argv.index("-r") + 1] == "/dev/ttyUSB0"
    assert argv[argv.index("-s") + 1] == "38400"
    assert argv[argv.index("-T") + 1] == "127.0.0.1"
    assert argv[argv.index("-t") + 1] == "4532"


def test_rig_argv_dummy_when_model_unset(sup):
    argv, notes = sup.rig_argv()
    assert argv[argv.index("-m") + 1] == "1"
    assert "Dummy" in notes[0]


def test_rig_argv_refuses_non_numeric_model(tmp_path):
    s = _sup_with_profile(tmp_path, {"rig": {"model": "TS-480"}})
    with pytest.raises(SupervisorError, match="rigctl -l"):
        s.rig_argv()


def test_hamlib_launch_uses_profile_rig(tmp_path):
    prof = {"rig": {"model": "3073", "path": "/dev/ttyUSB0", "baud": 38400}}
    s = _sup_with_profile(tmp_path, prof)
    try:
        s.launch("dep", bundle="wb")  # auto-starts the rig singleton
        argv = s.state["instances"]["hamlib"]["argv"]
        assert argv[argv.index("-m") + 1] == "3073"
        assert argv[argv.index("-t") + 1] == "4532"
    finally:
        _kill_all(s)


# -------------------------------------------------- M2: audio preset binding
def test_placeholder_resolved_via_default_preset(sup):
    app = {
        "id": "ardop",
        "launch": {
            "via": "managed-service",
            "cmd": f"{sys.executable} -c pass 8515 " f"<capture> <playback>",
        },
    }
    argv, _ = sup._resolve_cmd(app)
    # capture token resolves to the live monitor of the owned 'out' endpoint
    assert argv[-2:] == ["grid_down_hf_out.monitor", "grid_down_hf_in"]


def test_placeholder_partial_profile_override(sup):
    sup.profile["audio"] = {"presets": {"bad2": {"capture": "hw:CARD=rig"}}}
    app = {"id": "bad2", "launch": {"via": "managed-service", "cmd": "x <capture> <playback>"}}
    with pytest.raises(SupervisorError, match=r"presets\.bad2\.playback"):
        sup._resolve_cmd(app)  # capture resolved; playback still missing
