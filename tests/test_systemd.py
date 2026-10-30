# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""systemd backend tests (M4a).

Hermetic, no real systemd: a fake `systemctl` shim is placed first on PATH and
records every argv it receives, answering from a per-test response table
(GD_SHIM_RESPONSES json: argv-joined-with-| -> {rc, out, err}). Unit *text*
is tested pure; `SystemdSupervisor` lifecycle is driven through the shim;
backend resolution is a matrix over flag/profile/units/bus presence.
Real `systemctl --user` acceptance is the P2 Fedora GUI VM pass (spec §10).
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core.supervisor import SupervisorError  # noqa: E402
from core.systemd import (  # noqa: E402
    RIG_ID,
    SystemdSupervisor,
    install_units,
    resolve_backend,
    service_name,
    service_unit_text,
    target_members,
    target_unit_text,
    uninstall_units,
    units_current,
)

SHIM = """#!/usr/bin/env python3
import json, os, sys
args = [a for a in sys.argv[1:] if a != "--user"]
with open(os.environ["GD_SHIM_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
table = json.load(open(os.environ["GD_SHIM_RESPONSES"]))
resp = table.get("|".join(args)) or {"rc": 0, "out": "", "err": ""}
sys.stdout.write(resp.get("out", ""))
sys.stderr.write(resp.get("err", ""))
sys.exit(resp.get("rc", 0))
"""

SLEEPER = f"{sys.executable} -c 'import time; time.sleep(60)'"


def _apps() -> list[dict]:
    return [
        {
            "id": "hamlib",
            "name": "hamlib (rigctld)",
            "launch": {"via": "managed-service", "cmd": "rigctld-fake -T 127.0.0.1"},
        },
        {"id": "term", "name": "Terminal", "launch": {"via": "desktop-app", "cmd": SLEEPER}},
        {"id": "dep", "name": "Dependent", "launch": {"via": "desktop-app", "cmd": SLEEPER}},
        {
            "id": "bad",
            "name": "Broken",
            "launch": {"via": "desktop-app", "cmd": "ardopc 8515 <capture> <playback>"},
        },
        {
            "id": "vara",
            "name": "VARA HF",
            "launch": {"via": "wine-hub", "cmd": "VARA.exe (via gd winehub)"},
        },
        {"id": "tool", "name": "Mesh CLI", "launch": {"via": "cli-tool", "cmd": "meshtastic"}},
    ]


BUNDLES = {
    "wb": {"members": ["hamlib", "term"], "description": "test bundle one"},
    "wb2": {"members": ["hamlib", "dep"], "description": "test bundle two"},
    "mixed": {"members": ["hamlib", "tool", "vara", "term"], "description": "mixed bundle"},
}


@pytest.fixture()
def sd(tmp_path, monkeypatch):
    """systemctl shim on PATH + isolated XDG/GRID_DOWN env. Returns a
    namespace with tmp paths, the response-table file, and the log file."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "systemctl"
    shim.write_text(SHIM)
    shim.chmod(0o755)
    (bindir / "rigctld-fake").write_text("#!/bin/sh\nexec sleep 60\n")
    (bindir / "rigctld-fake").chmod(0o755)
    logf = tmp_path / "shim.log"
    respf = tmp_path / "shim.json"
    respf.write_text("{}")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("GRID_DOWN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GD_SHIM_LOG", str(logf))
    monkeypatch.setenv("GD_SHIM_RESPONSES", str(respf))
    return SimpleNamespace(
        tmp=tmp_path, log=logf, responses=respf, unit_dir=tmp_path / "config" / "systemd" / "user"
    )


def _sup(sd) -> SystemdSupervisor:
    return SystemdSupervisor(_apps(), BUNDLES, state_dir=sd.tmp / "state", app_root=sd.tmp / "opt")


def _calls(sd) -> list[list[str]]:
    if not sd.log.exists():
        return []
    return [json.loads(line) for line in sd.log.read_text().splitlines()]


def _respond(sd, table: dict) -> None:
    sd.responses.write_text(json.dumps(table))


def _units_row(unit: str, active: str = "active", sub: str = "running") -> str:
    return f"{unit} loaded {active} {sub} Grid Down unit"


# ------------------------------------------------------------- unit text
def test_service_text_binds_all_bundles(sd):
    sup = _sup(sd)
    app = sup.apps["term"]  # member of wb AND mixed -> bound to both targets
    argv, _cwd = sup._resolve_cmd(app)
    text = service_unit_text(app, sup.bundles, sup.state_dir, argv)
    assert text.count("BindsTo=grid-down-wb.target") == 1
    assert text.count("BindsTo=grid-down-mixed.target") == 1
    # one-directional ordering: the target is After= its members, so the
    # service must NOT be After= its target (ordering cycle, verify-rejected)
    assert "After=" not in text
    assert "Type=simple" in text
    assert "Restart=no" in text
    assert f"StandardOutput=append:{sup.state_dir}/logs/term.log" in text
    assert f"StandardError=append:{sup.state_dir}/logs/term.log" in text
    assert text.startswith("[Unit]")
    assert "ExecStart=" + argv[0] in text  # resolved argv, not the raw cmd string
    assert "[Install]" not in text  # units are started via API/targets, never enabled


def test_hamlib_unit_is_unbound(sd):
    sup = _sup(sd)
    app = sup.apps[RIG_ID]
    argv, _ = sup._resolve_cmd(app)
    text = service_unit_text(app, sup.bundles, sup.state_dir, argv)
    assert "BindsTo=" not in text  # singleton: reference-counted, never target-bound
    assert "After=grid-down-" not in text


def test_ardop_unit_execstart_uses_args_file(sd):
    """M8/M7 contract: the rendered ExecStart carries the args file's launch
    args (units render from _resolve_cmd, so both backends agree). Doctor's
    re-render then flags drift when the file changes post-install."""
    sup = _sup(sd)
    args_dir = sd.tmp / "state" / "app-args"
    args_dir.mkdir(parents=True)
    (args_dir / "ardop.args").write_text("8515\nhw:CARD=rig\nhw:CARD=spk\n")
    app = {
        "id": "ardop",
        "launch": {
            "via": "managed-service",
            "cmd": f"{sys.executable} -c pass 8515 <capture> <playback>",
        },
    }
    argv, _ = sup._resolve_cmd(app)
    text = service_unit_text(app, sup.bundles, sup.state_dir, argv)
    exec_line = next(ln for ln in text.splitlines() if ln.startswith("ExecStart="))
    assert exec_line == f"ExecStart={shlex.join(argv)}"


def test_target_text_wants_and_orders_members():
    text = target_unit_text("wb", BUNDLES["wb"], ["hamlib", "term"])
    assert "Description=Grid Down session bundle wb" in text
    assert "# test bundle one" in text
    assert "Wants=grid-down-hamlib.service grid-down-term.service" in text
    assert "After=grid-down-hamlib.service grid-down-term.service" in text


def test_target_members_skip_wine_and_cli():
    apps = {a["id"]: a for a in _apps()}
    assert target_members(["hamlib", "tool", "vara", "term"], apps) == ["hamlib", "term"]


# ------------------------------------------------------- install/uninstall
def test_install_layout_and_skips(sd):
    sup = _sup(sd)
    out = install_units(sup)
    assert out["unit_dir"] == str(sd.unit_dir)
    assert out["skipped"] == ["tool", "vara"]
    assert set(out["unresolved"]) == {"bad"}  # placeholder cmd: no unit, reported
    assert sorted(out["installed"]) == ["dep", "hamlib", "term"]
    written = {Path(name).name for name in out["written"]}
    assert written == {
        "grid-down-hamlib.service",
        "grid-down-term.service",
        "grid-down-dep.service",
        "grid-down-wb.target",
        "grid-down-wb2.target",
        "grid-down-mixed.target",
    }
    assert (sd.unit_dir / "grid-down-wb.target").is_file()
    assert not (sd.unit_dir / "grid-down-vara.service").exists()
    assert out["reloaded"] is True
    assert ["daemon-reload"] in _calls(sd)
    assert units_current(sup) is True  # deterministic render


def test_units_current_detects_drift_and_extras(sd):
    sup = _sup(sd)
    install_units(sup)
    unit = sd.unit_dir / service_name("term")
    unit.write_text(unit.read_text().replace("Restart=no", "Restart=on-failure"))
    assert units_current(sup) is False  # content drift
    install_units(sup)
    (sd.unit_dir / "grid-down-ghost.service").write_text("[Unit]\n")
    assert units_current(sup) is False  # stale extra unit


def test_uninstall_removes_only_grid_down_units(sd):
    install_units(_sup(sd))
    decoy = sd.unit_dir / "user-other.service"
    decoy.write_text("[Unit]\nDescription=not ours\n")
    removed = uninstall_units()
    assert len(removed) == 6
    assert sorted(p.name for p in sd.unit_dir.glob("*")) == ["user-other.service"]
    assert ["daemon-reload"] in _calls(sd)


# ------------------------------------------------------------ lifecycle
def test_launch_and_stop_lifecycle(sd):
    sup = _sup(sd)
    install_units(sup)
    _respond(
        sd, {"show|grid-down-term.service|--property=MainPID|--value": {"rc": 0, "out": "4242\n"}}
    )
    res = sup.launch("term")
    assert res["state"] == "running"
    assert res["pid"] == 4242
    assert res["log"].endswith("logs/term.log")
    assert ["start", "grid-down-term.service"] in _calls(sd)
    assert not (sup.state_dir / "supervisor.json").exists()  # systemd owns truth

    # second launch with the unit active -> already-running, no new start call
    _respond(
        sd,
        {
            "show|grid-down-term.service|--property=MainPID|--value": {"rc": 0, "out": "4242\n"},
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-term.service") + "\n",
            },
        },
    )
    starts = sum(1 for c in _calls(sd) if c[:2] == ["start", "grid-down-term.service"])
    res2 = sup.launch("term")
    assert res2["state"] == "already-running"
    assert sum(1 for c in _calls(sd) if c[:2] == ["start", "grid-down-term.service"]) == starts

    # stop clears the unit; stopping again refuses (unit now inactive)
    assert sup.stop("term") == {"id": "term", "state": "stopped", "notes": []}
    assert ["stop", "grid-down-term.service"] in _calls(sd)
    _respond(
        sd, {"show|grid-down-term.service|--property=MainPID|--value": {"rc": 0, "out": "0\n"}}
    )  # unit inactive: gone from list-units
    with pytest.raises(SupervisorError, match="not running"):
        sup.stop("term")


def test_stop_clears_failed_unit(sd):
    sup = _sup(sd)
    install_units(sup)
    _respond(
        sd,
        {
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-term.service", active="failed", sub="failed") + "\n",
            }
        },
    )
    res = sup.stop("term")
    assert res["state"] == "failed-cleared"  # stale-entry parity with direct backend


def test_launch_refuses_unresolvable_cmd(sd):
    sup = _sup(sd)
    install_units(sup)
    with pytest.raises(SupervisorError):  # placeholder cmd (<capture>) unresolved
        sup.launch("bad")
    assert not any(c[:1] == ["start"] for c in _calls(sd))


def test_launch_bundle_starts_rig_then_target_once(sd):
    sup = _sup(sd)
    install_units(sup)
    # phase 1: nothing running -> rig first, then one target start
    _respond(
        sd, {"show|grid-down-hamlib.service|--property=MainPID|--value": {"rc": 0, "out": "1111\n"}}
    )
    out = sup.launch_bundle("wb")
    calls = _calls(sd)
    rig_start = calls.index(["start", "grid-down-hamlib.service"])
    target_start = calls.index(["start", "grid-down-wb.target"])
    assert rig_start < target_start
    assert sum(1 for c in calls if c == ["start", "grid-down-wb.target"]) == 1
    assert "running: hamlib" in out["notes"]
    assert any("target grid-down-wb.target active" in n for n in out["notes"])

    # phase 2: rig already up -> "already-running" note, target start again
    _respond(
        sd,
        {
            "show|grid-down-hamlib.service|--property=MainPID|--value": {"rc": 0, "out": "1111\n"},
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-hamlib.service") + "\n",
            },
        },
    )
    out2 = sup.launch_bundle("wb")
    assert "already-running: hamlib" in out2["notes"]


def test_launch_bundle_notes_skips(sd):
    sup = _sup(sd)
    install_units(sup)
    _respond(
        sd,
        {
            "show|grid-down-hamlib.service|--property=MainPID|--value": {"rc": 0, "out": "1111\n"},
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-hamlib.service") + "\n",
            },
        },
    )
    out = sup.launch_bundle("mixed")
    assert "skip tool (cli-tool: managed elsewhere / interactive)" in out["notes"]
    assert "skip vara (wine-hub: managed elsewhere / interactive)" in out["notes"]


def test_stop_bundle_keeps_rig_used_elsewhere(sd):
    sup = _sup(sd)
    install_units(sup)
    # hamlib (wb+wb2), term (wb), dep (wb2) all running: stopping wb keeps rig
    _respond(
        sd,
        {
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": "\n".join(
                    [
                        _units_row("grid-down-hamlib.service"),
                        _units_row("grid-down-term.service"),
                        _units_row("grid-down-dep.service"),
                    ]
                )
                + "\n",
            }
        },
    )
    out = sup.stop_bundle("wb")
    assert ["stop", "grid-down-wb.target"] in _calls(sd)
    assert not any(c == ["stop", "grid-down-hamlib.service"] for c in _calls(sd))
    assert out["notes"][0] == "target grid-down-wb.target stopped"
    assert "hamlib left running (still used by: dep)" in out["notes"]


def test_stop_bundle_stops_rig_when_last_user(sd):
    sup = _sup(sd)
    install_units(sup)
    _respond(
        sd,
        {
            "show|grid-down-hamlib.service|--property=MainPID|--value": {"rc": 0, "out": "1111\n"},
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-hamlib.service") + "\n",
            },
        },
    )
    out = sup.stop_bundle("wb2")  # only hamlib is running (dep not up)
    assert ["stop", "grid-down-hamlib.service"] in _calls(sd)
    assert "stopped: hamlib" in out["notes"]


def test_rig_guard_preserved(sd):
    sup = _sup(sd)
    install_units(sup)
    _respond(
        sd,
        {
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": "\n".join(
                    [_units_row("grid-down-hamlib.service"), _units_row("grid-down-term.service")]
                )
                + "\n",
            }
        },
    )
    with pytest.raises(SupervisorError, match="refusing to stop hamlib"):
        sup.stop(RIG_ID)
    res = sup.stop(RIG_ID, force=True)
    assert res["state"] == "stopped"
    assert ["stop", "grid-down-hamlib.service"] in _calls(sd)


def test_status_reports_backend_and_failed(sd):
    sup = _sup(sd)
    install_units(sup)
    _respond(
        sd,
        {
            "show|grid-down-term.service|--property=MainPID|--value": {"rc": 0, "out": "4242\n"},
            "show|grid-down-hamlib.service|--property=MainPID|--value": {"rc": 0, "out": "1111\n"},
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": "\n".join(
                    [
                        _units_row("grid-down-hamlib.service"),
                        _units_row("grid-down-term.service"),
                        _units_row("grid-down-dep.service", active="failed", sub="failed"),
                    ]
                )
                + "\n",
            },
        },
    )
    snap = sup.status()
    assert snap["backend"] == "systemd"
    rows = {r["id"]: r for r in snap["apps"]}
    assert set(rows) == {"dep", "hamlib", "term"}  # stopped units filtered out
    assert rows["hamlib"]["state"] == "running" and rows["hamlib"]["pid"] == 1111
    assert rows["term"]["state"] == "running"
    assert rows["dep"]["state"] == "failed"
    assert snap["bundles"]["wb"] == {"running": ["hamlib", "term"], "total": 2}
    assert snap["bundles"]["wb2"] == {"running": ["hamlib"], "total": 2}
    snap_all = sup.status(include_stopped=True)
    assert len(snap_all["apps"]) == 6


def test_stale_ids_are_failed_units(sd):
    sup = _sup(sd)
    install_units(sup)
    _respond(
        sd,
        {
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-dep.service", active="failed", sub="failed") + "\n",
            }
        },
    )
    assert sup.stale_ids() == ["dep"]


def test_stop_all_leaves_nothing_running(sd):
    sup = _sup(sd)
    install_units(sup)
    _respond(
        sd,
        {
            "show|grid-down-hamlib.service|--property=MainPID|--value": {"rc": 0, "out": "1111\n"},
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": "\n".join(
                    [_units_row("grid-down-hamlib.service"), _units_row("grid-down-term.service")]
                )
                + "\n",
            },
        },
    )
    # force=True: the shim's list-units answer is static (real systemd would
    # report term inactive by the time the rig is stopped, so the guard cannot
    # trip in production — stop_all stops dependents first).
    out = sup.stop_all(force=True)
    ids = [c[1] for c in _calls(sd) if c[:1] == ["stop"]]
    assert set(ids) == {"grid-down-term.service", "grid-down-hamlib.service"}
    assert ids.index("grid-down-hamlib.service") > ids.index("grid-down-term.service")
    assert out == {"bundle": "all", "notes": ["stopped: term", "stopped: hamlib"]}


# ----------------------------------------------------- backend resolution
def test_resolve_backend_matrix(sd, monkeypatch):
    # explicit direct always wins, regardless of units
    assert resolve_backend(None, "direct") == "direct"
    assert resolve_backend({"supervisor": {"backend": "direct"}}, None) == "direct"
    # explicit systemd without a usable bus -> error naming the reason
    monkeypatch.setenv("XDG_RUNTIME_DIR", "")
    with pytest.raises(SupervisorError, match="XDG_RUNTIME_DIR"):
        resolve_backend(None, "systemd")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(sd.tmp / "run"))
    with pytest.raises(SupervisorError, match=r"gd units install"):
        resolve_backend(None, "systemd")  # bus ok, but no units installed
    # explicit systemd after install -> systemd
    install_units(_sup(sd))
    assert resolve_backend(None, "systemd") == "systemd"
    # auto: units + bus -> systemd; profile key still overrides
    assert resolve_backend(None, None) == "systemd"
    assert resolve_backend({"supervisor": {"backend": "direct"}}, None) == "direct"
    # no systemctl on PATH at all -> auto falls back to direct
    emptybin = sd.tmp / "emptybin"
    emptybin.mkdir()
    monkeypatch.setenv("PATH", str(emptybin))
    assert resolve_backend(None, None) == "direct"
    # invalid values are refused (flag and profile)
    with pytest.raises(SupervisorError, match="unknown supervisor backend"):
        resolve_backend(None, "supervisorctl")
    with pytest.raises(SupervisorError, match="unknown supervisor backend"):
        resolve_backend({"supervisor": {"backend": "circus"}}, None)


# ------------------------------------------------------------------- CLI
def test_cli_units_install_list_uninstall(sd, capsys, monkeypatch):
    sys.path.insert(0, str(ROOT / "control" / "cli"))
    import gd

    monkeypatch.setattr(gd.registry, "load_registry", lambda *_a, **_k: _apps())
    monkeypatch.setattr(gd.registry, "load_bundles", lambda *_a, **_k: dict(BUNDLES))
    assert gd.main(["units", "install"]) == 0
    text = capsys.readouterr().out
    assert "services:   3 installed" in text
    assert "targets:    3 installed" in text
    assert "skipped:    tool, vara" in text
    assert "daemon-reload: ok" in text
    assert gd.main(["units", "list"]) == 0
    text = capsys.readouterr().out
    assert "grid-down-wb.target" in text and "installed, current" in text
    assert gd.main(["units", "uninstall"]) == 0
    text = capsys.readouterr().out
    assert "removed 6 grid-down unit file(s)" in text


def test_cli_status_backend_flag_with_shim(sd, capsys, monkeypatch):
    sys.path.insert(0, str(ROOT / "control" / "cli"))
    import gd

    monkeypatch.setattr(gd, "ROOT", ROOT)  # registry paths are repo-relative
    install_units(_sup(sd))
    _respond(
        sd,
        {
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-hamlib.service") + "\n",
            },
            "show|grid-down-hamlib.service|--property=MainPID|--value": {"rc": 0, "out": "1111\n"},
        },
    )
    assert gd.main(["status", "--backend", "systemd"]) == 0
    text = capsys.readouterr().out
    assert "backend: systemd" in text
    assert "hamlib" in text
    # explicit systemd without units is refused even via the CLI flag
    uninstall_units()
    assert gd.main(["status", "--backend", "systemd"]) == 1
    assert "gd units install" in capsys.readouterr().err


# ---------------------------------------------------------------- doctor
def _doctor_units_result(sd, monkeypatch):
    from core import doctor

    monkeypatch.setattr(doctor.registry, "load_registry", lambda *_a, **_k: _apps())
    monkeypatch.setattr(doctor.registry, "load_bundles", lambda *_a, **_k: dict(BUNDLES))
    results = doctor.run_checks(state_dir=sd.tmp / "state", profile_data={})
    return results, next(r for r in results if r.id == "systemd")


def test_doctor_units_check_skip_and_count(sd, monkeypatch):
    results, unit_check = _doctor_units_result(sd, monkeypatch)
    assert len(results) == 20  # 18 before M9 (+3 mesh) = 21; M10 removed the
    # 8091/js8spotter port check with the php-service contract correction
    assert unit_check.status == "skip"
    assert "gd units install" in unit_check.detail


def test_doctor_units_ok_then_drift_warn(sd, monkeypatch):
    install_units(_sup(sd))
    _r, unit_check = _doctor_units_result(sd, monkeypatch)
    assert unit_check.status == "ok"
    assert "current with registry/profile" in unit_check.detail
    unit = sd.unit_dir / service_name("term")
    unit.write_text(unit.read_text().replace("Restart=no", "Restart=on-failure"))
    _r, unit_check = _doctor_units_result(sd, monkeypatch)
    assert unit_check.status == "warn"
    assert "drifted" in unit_check.detail


def test_doctor_units_outliving_target_fails(sd, monkeypatch):
    install_units(_sup(sd))
    _respond(
        sd,
        {
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-term.service") + "\n",
            },
            "list-units|--all|grid-down-*.target|--no-legend|--plain": {"rc": 0, "out": ""},
        },
    )  # every target inactive
    _r, unit_check = _doctor_units_result(sd, monkeypatch)
    assert unit_check.status == "fail"
    assert "grid-down-term.service" in unit_check.detail
    assert "outliving" in unit_check.detail


def test_doctor_units_bound_target_running_is_clean(sd, monkeypatch):
    install_units(_sup(sd))
    _respond(
        sd,
        {
            "list-units|--all|grid-down-*.service|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-term.service") + "\n",
            },
            "list-units|--all|grid-down-*.target|--no-legend|--plain": {
                "rc": 0,
                "out": _units_row("grid-down-wb.target") + "\n",
            },
        },
    )
    _r, unit_check = _doctor_units_result(sd, monkeypatch)
    assert unit_check.status == "ok"
    assert "runtime clean" in unit_check.detail
