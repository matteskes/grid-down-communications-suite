#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""gd — Grid Down command-line control interface (M2).

Implemented: version, apps list, bundles list, profile show, rig, audio
(plan/up/down/status), launch, stop, status, doctor, units install/uninstall/
list (spec §6.4/§6.6/§6.7). M2 added profile-driven rigctld (rig_argv) and
the suite-owned PipeWire endpoint namespace (core/audio.py). M3 adds the
Control Center (`gd gui`, spec §7) sharing core/supervisor + core/audio +
core/doctor with this CLI in-process. M4a adds the systemd user-unit backend
(core/systemd.py): launch/stop/status grow --backend {auto,direct,systemd},
`gd units` manages the unit set; the Supervisor API stays backend-neutral.
M5 adds the wine hub (`gd winehub`, core/winehub.py: status/start/stop/COM
map over the shared VARA prefix) and install-state probes
(`gd apps list --state`, core/install_state.py). Later phases add the
remaining config apply (adapters) and per-app install recipes.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import audio, doctor, install_state, profile, registry, systemd, winehub  # noqa: E402
from core.supervisor import PORTS, Supervisor, SupervisorError  # noqa: E402

VERSION = "0.6.0-m5"
ROOT = Path(__file__).resolve().parents[2]


def _fail(message: str) -> int:
    print(f"gd: error: {message}", file=sys.stderr)
    return 1


def _build_supervisor(profile_data: dict | None = None, backend: str | None = None) -> Supervisor:
    """Backend-neutral factory: --backend flag > profile supervisor.backend >
    auto (systemd iff grid-down units are installed and a user bus is
    reachable, else direct — spec §6.5)."""
    apps = registry.load_registry(ROOT / "control" / "registry" / "apps")
    bundles = registry.load_bundles(ROOT / "control" / "registry" / "bundles.yaml")
    prof = profile.load_profile() if profile_data is None else profile_data
    kind = systemd.resolve_backend(prof, backend)
    cls = systemd.SystemdSupervisor if kind == "systemd" else Supervisor
    return cls(apps, bundles, profile=prof)


def cmd_gui(args) -> int:
    """Launch the Control Center (spec §7). Lazy Qt import: the CLI stays
    fully usable on headless/air-gap boxes without PySide6 installed."""
    try:
        from gui.app import main as gui_main
    except ImportError as exc:
        return _fail(
            "PySide6 is not available "
            "(Fedora 45: sudo dnf install python3-pyside6; "
            f"dev venv: pip install PySide6-Essentials) — {exc}"
        )
    return gui_main(["--selfcheck"] if args.selfcheck else [])


def cmd_version(_args) -> int:
    print(f"gd {VERSION}")
    return 0


def cmd_apps(args) -> int:
    apps = registry.load_registry(ROOT / "control" / "registry" / "apps")
    states = install_state.app_states(apps) if args.state else None
    if args.json:
        if states is not None:
            apps = [dict(a, install_state=states.get(a["id"], {})) for a in apps]
        print(json.dumps(apps, indent=2))
        return 0
    head = f"{'ID':<16} {'CATEGORY':<12} {'STRATEGY':<12}"
    row = "{id:<16} {category:<12} {strategy:<12}"
    if states is not None:
        head += f" {'STATE':<10}"
        row += " {state:<10}"
    head += " NAME"
    row += " {name}"
    print(head)
    for a in apps:
        st = (states or {}).get(a["id"], {})
        print(
            row.format(
                id=a["id"],
                category=a["category"],
                strategy=a["install"]["strategy"],
                state=st.get("state", "?"),
                name=a["name"],
            )
        )
    print(f"\n{len(apps)} apps registered")
    if states is not None:
        tally: dict[str, int] = {}
        for st in states.values():
            tally[st["state"]] = tally.get(st["state"], 0) + 1
        print("install state: " + ", ".join(f"{v} {k}" for k, v in sorted(tally.items())))
    return 0


def cmd_bundles(args) -> int:
    bundles = registry.load_bundles(ROOT / "control" / "registry" / "bundles.yaml")
    if args.json:
        print(json.dumps(bundles, indent=2))
        return 0
    for name, b in bundles.items():
        print(f"{name:<10} {b.get('description', '')}")
        print(f"{'':<10} members: {', '.join(b.get('members', []))}")
    return 0


def cmd_profile(_args) -> int:
    path = profile.profile_path()
    data = profile.load_profile()
    print(f"profile file: {path}")
    print(json.dumps(data, indent=2))
    return 0


def cmd_rig(args) -> int:
    prof = profile.load_profile()
    argv, notes = _build_supervisor(prof).rig_argv()
    rig = prof.get("rig", {}) or {}
    if args.json:
        print(
            json.dumps(
                {
                    "argv": argv,
                    "notes": notes,
                    "endpoint": f"127.0.0.1:{PORTS['hamlib']}",
                    "profile_rig": rig,
                },
                indent=2,
            )
        )
        return 0
    print("rigctld argv: " + shlex.join(argv))
    print(f"endpoint: 127.0.0.1:{PORTS['hamlib']} — suite-owned singleton (spec §6.4/G4)")
    print(
        f"profile rig: model={rig.get('model') or '(unset → Dummy)'} "
        f"path={rig.get('path') or '(unset)'} baud={rig.get('baud') or '(unset)'}"
    )
    for note in notes:
        print(f"note: {note}")
    return 0


def cmd_audio(args) -> int:
    prof = profile.load_profile()
    action = args.audio_cmd
    if action == "plan":
        steps = audio.plan(prof)
        print(f"{'ENDPOINT':<26} {'KIND':<7} STATE")
        for st in steps:
            print(f"{st['endpoint']:<26} {st['kind']:<7} {st['state']}")
        rig_capture = (prof.get("audio", {}) or {}).get("rig_capture") or ""
        pending = (
            f"rig-interface loopback from '{rig_capture}'"
            if rig_capture
            else "rig-interface loopback (set audio.rig_capture)"
        )
        print(f"\npending (Phase 3 adapters): {pending}")
        defaults = ", ".join(
            f"{a}.{k}={v}" for a, pv in audio.DEFAULT_PRESETS.items() for k, v in pv.items()
        )
        print(f"launch-arg presets (profile audio.presets overrides): {defaults}")
        return 0
    if action == "status":
        st = audio.status()
        print("present: " + (", ".join(st["present"]) or "(none)"))
        print("missing: " + (", ".join(st["missing"]) or "(none)"))
        return 0
    if action == "up":
        out = audio.up(prof)
        for c in out["created"]:
            print(f"created: {c['endpoint']} (pactl module {c['module']})")
        for name in out["skipped"]:
            print(f"exists:  {name}")
        if not out["created"] and not out["skipped"]:
            print("nothing to do")
        return 0
    out = audio.down(prof)  # down
    print("unloaded modules: " + (", ".join(out["unloaded"]) or "(none)"))
    return 0


def cmd_doctor(args) -> int:
    results = doctor.run_checks(profile_data=profile.load_profile())
    if args.json:
        print(doctor.to_json(results))
    else:
        print(doctor.format_text(results))
    return 0 if doctor.all_ok(results) else 1


def cmd_status(args) -> int:
    try:
        snap = _build_supervisor(backend=getattr(args, "backend", None)).status()
    except SupervisorError as exc:  # e.g. --backend systemd without units installed
        return _fail(str(exc))
    if args.json:
        print(json.dumps(snap, indent=2))
        return 0
    rows = snap["apps"]
    if not rows:
        print("no instances recorded (nothing launched yet)")
        return 0
    print(f"{'ID':<16} {'STATE':<9} {'PID':>7}  {'VIA':<16} BUNDLE")
    for r in rows:
        pid = str(r["pid"]) if r["pid"] else "-"
        print(f"{r['id']:<16} {r['state']:<9} {pid:>7}  {r['via']:<16} {r['bundle'] or '-'}")
    running = sum(1 for r in rows if r["state"] == "running")
    stale = sum(1 for r in rows if r["state"] == "stale")
    failed = sum(1 for r in rows if r["state"] == "failed")
    extra = f", {failed} failed" if failed else ""
    print(
        f"\n{running} running, {stale} stale{extra} " f"(backend: {snap.get('backend', 'direct')})"
    )
    for name, b in snap["bundles"].items():
        if b["running"]:
            print(f"bundle {name}: {', '.join(b['running'])} ({len(b['running'])}/{b['total']})")
    return 0


def cmd_launch(args) -> int:
    s = _build_supervisor(backend=getattr(args, "backend", None))
    target = args.target
    if target == "all":
        return _fail(
            "'all' is not a launch target — name an app or bundle "
            "(see: gd apps list / gd bundles list)"
        )
    try:
        if target in s.bundles:
            out = s.launch_bundle(target)
            for note in out["notes"]:
                print(f"  {note}")
            return 0
        if target in s.apps:
            res = s.launch(target)
            line = f"{res['state']}: {target}"
            if res.get("pid"):
                line += f" (pid {res['pid']})"
            if res.get("log"):
                line += f" — log: {res['log']}"
            print(line)
            return 0
    except SupervisorError as exc:
        return _fail(str(exc))
    return _fail(f"unknown target '{target}' — see 'gd apps list' and 'gd bundles list'")


def cmd_stop(args) -> int:
    s = _build_supervisor(backend=getattr(args, "backend", None))
    target = args.target
    try:
        if target == "all":
            out = s.stop_all(force=args.force)
        elif target in s.bundles:
            out = s.stop_bundle(target, force=args.force)
        elif target in s.apps:
            res = s.stop(target, force=args.force)
            print(f"{res['state']}: {target}")
            return 0
        else:
            return _fail(f"unknown target '{target}' — see 'gd apps list' and 'gd bundles list'")
    except SupervisorError as exc:
        return _fail(str(exc))
    for note in out["notes"]:
        print(f"  {note}")
    if not out["notes"]:
        print("nothing to stop")
    return 0


def cmd_units(args) -> int:
    """systemd user-unit management (spec §6.5, M4a): render + install the
    grid-down-* unit set, remove it, or list installed-vs-expected drift."""
    sup = _build_supervisor(backend="direct")  # rendering is backend-independent
    if args.units_cmd == "install":
        out = systemd.install_units(sup)
        if args.json:
            print(json.dumps(out, indent=2))
            return 0
        n_targets = sum(1 for name in out["written"] if name.endswith(".target"))
        print(f"unit dir:   {out['unit_dir']}")
        print(f"services:   {len(out['installed'])} installed")
        print(f"targets:    {n_targets} installed")
        if out["skipped"]:
            print(
                f"skipped:    {', '.join(out['skipped'])} "
                "(wine-hub/cli-tool are not daemonized, spec §6.5)"
            )
        if out["unresolved"]:
            print(
                f"unresolved: {len(out['unresolved'])} (no unit — app missing "
                "or placeholder unresolvable; fix and re-run)"
            )
            for app_id, why in out["unresolved"].items():
                print(f"  {app_id}: {why}")
        if out["reloaded"]:
            print("daemon-reload: ok")
        else:
            print(
                "daemon-reload: skipped (no systemd user session) — "
                "run `systemctl --user daemon-reload` after login"
            )
        return 0
    if args.units_cmd == "uninstall":
        removed = systemd.uninstall_units()
        print(f"removed {len(removed)} grid-down unit file(s) " f"from {systemd.user_unit_dir()}")
        return 0
    # list — installed vs freshly rendered expectation
    d = systemd.user_unit_dir()
    installed = {p.name: p.read_text() for p in systemd.installed_unit_paths()}
    expected = systemd.expected_units(sup)
    if args.json:
        units = [
            {
                "unit": name,
                "expected": True,
                "installed": name in installed,
                "drifted": name in installed and installed[name] != text,
            }
            for name, text in sorted(expected.items())
        ]
        units += [
            {"unit": name, "expected": False, "installed": True, "drifted": False}
            for name in sorted(installed)
            if name not in expected
        ]
        print(json.dumps({"unit_dir": str(d), "units": units}, indent=2))
        return 0
    print(f"{'UNIT':<42} STATE")
    for name, text in sorted(expected.items()):
        if name not in installed:
            state = "not installed"
        elif installed[name] != text:
            state = "installed, DRIFTED (re-run: gd units install)"
        else:
            state = "installed, current"
        print(f"{name:<42} {state}")
    for name in sorted(name for name in installed if name not in expected):
        print(f"{name:<42} installed, stale (not in registry/bundles anymore)")
    print(f"\nunit dir: {d}")
    return 0


def cmd_winehub(args) -> int:
    """VARA wine hub (spec §6.4, M5): status/start/stop + COM mapping.

    Lifecycle lives in core/winehub.py — one shared prefix, pid table under
    the state dir, clean refusals when the prefix or wine is missing."""
    hub = winehub.WineHub(registry.load_registry(ROOT / "control" / "registry" / "apps"))
    action = args.winehub_cmd
    if action == "status":
        st = hub.status()
        if args.json:
            print(json.dumps(st, indent=2))
            return 0
        print(f"prefix: {'present' if st['prefix'] else 'ABSENT'} ({st['prefix_path']})")
        print(f"wine:   {st['wine'] or 'not on PATH'}")
        if st["com"]:
            for c, d in sorted(st["com"].items()):
                print(f"com:    {c} -> {d}")
        else:
            print("com:    (none mapped — gd winehub map <n> <device>)")
        if not st["apps"]:
            print("apps:   (no wine-hub apps registered)")
        for a in st["apps"]:
            mark = f"running (pid {a['pid']})" if a["running"] else "stopped"
            print(f"app:    {a['id']:<16} {mark}  [{a['exe'] or 'no .exe in launch.cmd'}]")
        return 0
    if action == "map":
        out = hub.map_serial(f"com{args.com}", args.device)
        print(f"{out['state']}: {out['com']} -> {out['device']}")
        return 0
    if action == "unmap":
        out = hub.unmap(f"com{args.com}")
        print(f"{out['state']}: {out['com']} (was {out['device']})")
        return 0
    out = hub.start(args.app) if action == "start" else hub.stop(args.app)
    line = f"{out['state']}: {out['id']}"
    if out.get("pid"):
        line += f" (pid {out['pid']})"
    if out.get("log"):
        line += f" — log: {out['log']}"
    print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gd", description="Grid Down control CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="print version").set_defaults(func=cmd_version)

    p_apps = sub.add_parser("apps", help="app registry operations")
    p_apps_sub = p_apps.add_subparsers(dest="apps_cmd", required=True)
    p_list = p_apps_sub.add_parser("list", help="list registered apps")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument(
        "--state",
        action="store_true",
        help="probe install state per app (rpm query / PATH / docroot / wine prefix)",
    )
    p_list.set_defaults(func=cmd_apps)

    p_b = sub.add_parser("bundles", help="session bundle operations")
    p_b_sub = p_b.add_subparsers(dest="bundles_cmd", required=True)
    p_b_list = p_b_sub.add_parser("list", help="list session bundles")
    p_b_list.add_argument("--json", action="store_true")
    p_b_list.set_defaults(func=cmd_bundles)

    sub.add_parser("profile", help="show active station profile").set_defaults(func=cmd_profile)

    p_doctor = sub.add_parser("doctor", help="station preflight checks (spec §6.7, M1 subset)")
    p_doctor.add_argument("--json", action="store_true", help="machine-readable report")
    p_doctor.set_defaults(func=cmd_doctor)

    p_launch = sub.add_parser("launch", help="launch an app or a bundle session")
    p_launch.add_argument("target", help="app id or bundle name")
    p_launch.add_argument(
        "--backend",
        choices=("auto", "direct", "systemd"),
        default=None,
        help="supervisor backend override " "(default: profile supervisor.backend, else auto)",
    )
    p_launch.set_defaults(func=cmd_launch)

    p_stop = sub.add_parser("stop", help="stop an app, a bundle, or 'all'")
    p_stop.add_argument("target", help="app id, bundle name, or 'all'")
    p_stop.add_argument(
        "--force",
        action="store_true",
        help="override safety refusals (e.g. stopping rigctld with dependents up)",
    )
    p_stop.add_argument(
        "--backend",
        choices=("auto", "direct", "systemd"),
        default=None,
        help="supervisor backend override",
    )
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="supervisor status (running/stale instances)")
    p_status.add_argument("--json", action="store_true", help="machine-readable snapshot")
    p_status.add_argument(
        "--backend",
        choices=("auto", "direct", "systemd"),
        default=None,
        help="supervisor backend override",
    )
    p_status.set_defaults(func=cmd_status)

    p_units = sub.add_parser("units", help="systemd user-unit backend (spec §6.5, M4)")
    p_units_sub = p_units.add_subparsers(dest="units_cmd", required=True)
    p_u_i = p_units_sub.add_parser("install", help="render + write the grid-down-* unit set")
    p_u_i.add_argument("--json", action="store_true")
    p_u_i.set_defaults(func=cmd_units)
    p_units_sub.add_parser(
        "uninstall", help="remove all grid-down-* units (never others)"
    ).set_defaults(func=cmd_units)
    p_u_l = p_units_sub.add_parser("list", help="installed units vs registry expectation (drift)")
    p_u_l.add_argument("--json", action="store_true")
    p_u_l.set_defaults(func=cmd_units)

    p_wh = sub.add_parser(
        "winehub", help="VARA wine hub: shared prefix lifecycle + COM map (spec §6.4, M5)"
    )
    p_wh_sub = p_wh.add_subparsers(dest="winehub_cmd", required=True)
    p_wh_s = p_wh_sub.add_parser("status", help="prefix, wine binary, COM map, running apps")
    p_wh_s.add_argument("--json", action="store_true", help="machine-readable report")
    p_wh_s.set_defaults(func=cmd_winehub)
    p_wh_x = p_wh_sub.add_parser("start", help="start a wine-hub app (VARA family)")
    p_wh_x.add_argument("app", help="wine-hub app id (see: gd apps list)")
    p_wh_x.set_defaults(func=cmd_winehub)
    p_wh_t = p_wh_sub.add_parser("stop", help="stop a running wine-hub app")
    p_wh_t.add_argument("app", help="wine-hub app id")
    p_wh_t.set_defaults(func=cmd_winehub)
    p_wh_m = p_wh_sub.add_parser(
        "map", help="map COMn to a serial device (dosdevices symlink, §6.4)"
    )
    p_wh_m.add_argument("com", type=int, help="COM port number, 1-32 (e.g. 5 -> COM5)")
    p_wh_m.add_argument("device", help="serial device node (e.g. /dev/ttyUSB0)")
    p_wh_m.set_defaults(func=cmd_winehub)
    p_wh_u = p_wh_sub.add_parser("unmap", help="remove a COM mapping")
    p_wh_u.add_argument("com", type=int, help="COM port number, 1-32")
    p_wh_u.set_defaults(func=cmd_winehub)

    p_rig = sub.add_parser("rig", help="show profile-resolved rigctld command (spec §6.4)")
    p_rig.add_argument("--json", action="store_true", help="machine-readable report")
    p_rig.set_defaults(func=cmd_rig)

    p_a = sub.add_parser("audio", help="suite-owned PipeWire endpoints (spec §6.6, G4)")
    p_a_sub = p_a.add_subparsers(dest="audio_cmd", required=True)
    for name, hlp in (
        ("plan", "print the planned owned endpoint graph"),
        ("up", "create missing grid_down_* endpoints (idempotent)"),
        ("down", "unload grid_down_* modules (never others)"),
        ("status", "list present/missing owned endpoints"),
    ):
        p = p_a_sub.add_parser(name, help=hlp)
        p.set_defaults(func=cmd_audio, audio_cmd=name)

    p_gui = sub.add_parser("gui", help="launch the Control Center GUI (spec §7)")
    p_gui.add_argument(
        "--selfcheck", action="store_true", help="build the window, apply one snapshot, exit"
    )
    p_gui.set_defaults(func=cmd_gui)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (
        SupervisorError,
        winehub.WinehubError,
        registry.RegistryError,
        profile.ProfileError,
        audio.AudioError,
    ) as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
