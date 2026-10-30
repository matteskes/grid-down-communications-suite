# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Doctor check tests (M1). Run: python3 -m pytest tests/test_doctor.py -q"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core import doctor  # noqa: E402  (after sys.path insert)


def _dead_pid() -> int:
    """A pid we know is dead: spawn, wait, return (child reaped -> not running)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_baseline_report_has_no_failures(tmp_path):
    results = doctor.run_checks(state_dir=tmp_path, profile_data={})
    assert results, "expected at least one check"
    assert set(r.status for r in results) <= {"ok", "warn", "fail", "skip"}
    assert doctor.all_ok(results), [r for r in results if r.status == "fail"]


def test_state_hygiene_flags_stale_instance(tmp_path):
    (tmp_path / "supervisor.json").write_text(
        json.dumps(
            {
                "version": 1,
                "instances": {"ghost": {"pid": _dead_pid(), "pgid": 1, "via": "desktop-app"}},
            }
        )
    )
    results = doctor.run_checks(state_dir=tmp_path, profile_data=None)
    state = next(r for r in results if r.id == "state")
    assert state.status == "warn"
    assert "ghost" in state.detail and "gd stop" in state.detail


def test_port_occupied_reports_warn(tmp_path):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        results = doctor.run_checks(state_dir=tmp_path, profile_data=None, ports={"testsvc": port})
    finally:
        blocker.close()
    check = next(r for r in results if r.id == f"port-{port}")
    assert check.status == "warn"
    assert "unknown listener" in check.detail


def test_port_free_reports_ok(tmp_path):
    results = doctor.run_checks(state_dir=tmp_path, profile_data=None, ports={"testsvc": 59999})
    check = next(r for r in results if r.id == "port-59999")
    assert check.status == "ok" and check.detail == "free"


def test_text_and_json_rendering(tmp_path):
    profile_data = {
        "profile": {"id": "d", "callsign": "W1AW", "grid": "FN31"},
        "rig": {"path": "/dev/ttyUSB0"},
        "internet": {"present": False},
    }
    results = doctor.run_checks(state_dir=tmp_path, profile_data=profile_data)
    text = doctor.format_text(results)
    assert "doctor:" in text and "air-gap master switch" in text
    payload = json.loads(doctor.to_json(results))
    assert payload["ok"] is True
    assert payload["checks"][0]["id"] == "platform"
    assert all(set(c) == {"id", "title", "status", "detail"} for c in payload["checks"])


# -- mesh checks (M9, spec §6.11) ----------------------------------------------


def test_mesh_checks_skipped_on_unconfigured_profile(tmp_path):
    results = doctor.run_checks(state_dir=tmp_path, profile_data={})
    for cid in ("mesh-handset", "mesh-node", "mesh-mqtt"):
        check = next(r for r in results if r.id == cid)
        assert check.status == "skip", (cid, check.detail)
    assert doctor.all_ok(results)


def test_deferred_list_narrowed_after_m9():
    assert all("VID:PID" not in d for d in doctor.DEFERRED)
    assert any("MQTT" in d for d in doctor.DEFERRED)


def _mesh_profile(dev: dict, node: dict | None = None, daemon: dict | None = None) -> dict:
    return {
        "profile": {"id": "d", "callsign": "W1AW", "grid": "FN31"},
        "mesh": {
            "device": dev,
            "node": node or {},
            "daemon": daemon if daemon is not None else {"enabled": False},
        },
    }


def _check(run_results, cid):
    return next(r for r in run_results if r.id == cid)


def test_mesh_handset_present_missing_and_mispointed(tmp_path, monkeypatch):
    byid = tmp_path / "by-id"
    byid.mkdir()
    handset = byid / "usb-Silicon_Labs_CP2102N_if00-port0"
    handset.write_text("")
    monkeypatch.setattr(doctor, "MESH_USB_GLOBS", (str(byid / "*"),))
    prof = _mesh_profile({"type": "serial", "path": str(handset), "host": None})

    ok = _check(doctor.run_checks(state_dir=tmp_path, profile_data=prof), "mesh-handset")
    assert ok.status == "ok"
    assert "present" in ok.detail and "handset detected" in ok.detail

    prof["mesh"]["device"]["path"] = "/dev/ttyUSB9"  # profile points elsewhere
    warn = _check(doctor.run_checks(state_dir=tmp_path, profile_data=prof), "mesh-handset")
    assert warn.status == "warn"
    assert "point mesh.device.path at it" in warn.detail and "CP2102N" in warn.detail

    monkeypatch.setattr(doctor, "MESH_USB_GLOBS", (str(tmp_path / "nothing*"),))
    warn2 = _check(doctor.run_checks(state_dir=tmp_path, profile_data=prof), "mesh-handset")
    assert warn2.status == "warn"
    assert "attach the handset" in warn2.detail and "10c4:ea60" in warn2.detail


def test_mesh_handset_tcp_ble_and_daemon(tmp_path):
    tcp = doctor._check_mesh_handset(
        _mesh_profile({"type": "tcp", "path": None, "host": "meshtastic.lan:4403"})
    )
    assert tcp.status == "ok" and "tcp node" in tcp.detail
    tcp_bad = doctor._check_mesh_handset(_mesh_profile({"type": "tcp", "path": None, "host": None}))
    assert tcp_bad.status == "warn" and "host is empty" in tcp_bad.detail
    ble = doctor._check_mesh_handset(_mesh_profile({"type": "ble", "path": None, "host": None}))
    assert ble.status == "ok" and "discovers" in ble.detail
    bad = doctor._check_mesh_handset(_mesh_profile({"type": "smoke", "path": "", "host": None}))
    assert bad.status == "warn" and "not serial|tcp|ble" in bad.detail
    daemon = doctor._check_mesh_handset(
        _mesh_profile(
            {"type": "serial", "path": "/dev/ttyUSB1", "host": None}, daemon={"enabled": True}
        )
    )
    assert daemon.status == "skip" and "meshtasticd owns the radio" in daemon.detail


def test_mesh_node_summary_conflict_and_masking(tmp_path):
    node = {
        "long_name": "Hilltop",
        "short_name": "HILL",
        "region": "US",
        "channel_url": "https://meshtastic.org/e/#PSK",
    }
    ok = _check(
        doctor.run_checks(
            state_dir=tmp_path,
            profile_data=_mesh_profile({"type": "ble", "path": None, "host": None}, node),
        ),
        "mesh-node",
    )
    assert ok.status == "ok"
    assert "long_name=Hilltop" in ok.detail and "region=US" in ok.detail
    assert "channel_url set (PSK masked)" in ok.detail
    assert "#PSK" not in ok.detail, "channel URL must not surface in doctor output"

    conflict = _check(
        doctor.run_checks(
            state_dir=tmp_path,
            profile_data=_mesh_profile(
                {"type": "serial", "path": "/dev/ttyUSB1", "host": None}, node, {"enabled": True}
            ),
        ),
        "mesh-node",
    )
    assert conflict.status == "warn" and "pick one" in conflict.detail


def test_mesh_mqtt_air_gap_scan(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(doctor, "MESHTASTICD_CONFIG_FILES", (cfg,))

    def run(prof):
        return _check(doctor.run_checks(state_dir=tmp_path, profile_data=prof), "mesh-mqtt")

    airgapped = {"internet": {"present": False}, "profile": {"callsign": "W1AW", "grid": "FN31"}}

    # daemon not installed → skip (no MQTT surface on the station)
    assert run(airgapped).status == "skip"
    assert "not installed" in run(airgapped).detail

    # MQTT enabled while air-gapped → warn, naming the key and file
    cfg.write_text("mqtt:\n  enabled: true\n  address: mqtt.example.com\n")
    warn = run(airgapped)
    assert warn.status == "warn"
    assert "mqtt.enabled=True" in warn.detail and str(cfg) in warn.detail

    # MQTT disabled → ok
    cfg.write_text("mqtt:\n  enabled: false\n")
    ok = run(airgapped)
    assert ok.status == "ok" and "no MQTT uplink" in ok.detail

    # not air-gapped → the scan does not apply
    online = {"internet": {"present": True}, "profile": {"callsign": "W1AW", "grid": "FN31"}}
    skip = run(online)
    assert skip.status == "skip" and "not false" in skip.detail


# ------------------------------------------------------- M2: rig + audio checks
def test_rig_model_check(tmp_path):
    results = doctor.run_checks(state_dir=tmp_path, profile_data={})
    chk = next(r for r in results if r.id == "rig-model")
    assert chk.status == "warn" and "Dummy" in chk.detail

    results = doctor.run_checks(state_dir=tmp_path, profile_data={"rig": {"model": "TS-480"}})
    chk = next(r for r in results if r.id == "rig-model")
    assert chk.status == "warn" and "numeric" in chk.detail

    results = doctor.run_checks(state_dir=tmp_path, profile_data={"rig": {"model": "3073"}})
    chk = next(r for r in results if r.id == "rig-model")
    assert chk.status == "ok" and "4532" in chk.detail


def test_audio_check_and_deferred_no_pipewire(tmp_path):
    results = doctor.run_checks(state_dir=tmp_path, profile_data={})
    audio_chk = next(r for r in results if r.id == "audio")
    if sys.platform.startswith("linux"):
        assert audio_chk.status in ("ok", "warn")
    else:
        assert audio_chk.status == "skip"
    deferred = next(r for r in results if r.id == "deferred")
    assert "PipeWire" not in deferred.detail  # graph audit implemented in M2


def test_systemd_check_matches_profiled_render(tmp_path, monkeypatch):
    """P2 VM-pass regression: `gd units install` renders through a Supervisor
    built with profile.load_profile() (the rig section shapes hamlib's
    ExecStart), so the drift check must render with the same profile — a bare
    Supervisor flagged every profiled install as 'drifted' on the real Fedora
    45 VM bus."""
    xdg = tmp_path / "xdg"
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    from core import registry, supervisor, systemd

    apps = registry.load_registry(ROOT / "control" / "registry" / "apps")
    bundles = registry.load_bundles(ROOT / "control" / "registry" / "bundles.yaml")
    prof = {"rig": {"path": "/dev/ttyUSB0", "baud": 38400}, "audio": {"presets": {}}}
    systemd.install_units(
        supervisor.Supervisor(apps, bundles, profile=prof),
        unit_dir=xdg / "systemd" / "user",
    )
    # state_dir stays None so both sides render the same (default) log paths —
    # the only input under test is the profile
    results = doctor.run_checks(profile_data=prof)
    row = next(r for r in results if r.id == "systemd")
    assert row.status == "ok", row.detail
    assert "current" in row.detail
