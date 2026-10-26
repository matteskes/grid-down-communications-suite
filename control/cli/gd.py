#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""gd — Grid Down command-line control interface (M1).

Implemented: version, apps list, bundles list, profile show, launch, stop,
status, doctor (spec §6.7 M1 subset).
Later phases add: config apply (adapters), install progress, winehub, and the
Control Center GUI (which shares core/supervisor + core/doctor with this CLI).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import doctor, profile, registry  # noqa: E402
from core.supervisor import Supervisor, SupervisorError  # noqa: E402

VERSION = "0.2.0-m1"
ROOT = Path(__file__).resolve().parents[2]


def _fail(message: str) -> int:
    print(f"gd: error: {message}", file=sys.stderr)
    return 1


def _build_supervisor() -> Supervisor:
    apps = registry.load_registry(ROOT / "control" / "registry" / "apps")
    bundles = registry.load_bundles(ROOT / "control" / "registry" / "bundles.yaml")
    return Supervisor(apps, bundles)


def cmd_version(_args) -> int:
    print(f"gd {VERSION}")
    return 0


def cmd_apps(args) -> int:
    apps = registry.load_registry(ROOT / "control" / "registry" / "apps")
    if args.json:
        print(json.dumps(apps, indent=2))
        return 0
    print(f"{'ID':<16} {'CATEGORY':<12} {'STRATEGY':<12} NAME")
    for a in apps:
        print(f"{a['id']:<16} {a['category']:<12} {a['install']['strategy']:<12} {a['name']}")
    print(f"\n{len(apps)} apps registered")
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


def cmd_doctor(args) -> int:
    results = doctor.run_checks(profile_data=profile.load_profile())
    if args.json:
        print(doctor.to_json(results))
    else:
        print(doctor.format_text(results))
    return 0 if doctor.all_ok(results) else 1


def cmd_status(args) -> int:
    snap = _build_supervisor().status()
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
    print(f"\n{running} running, {stale} stale")
    for name, b in snap["bundles"].items():
        if b["running"]:
            print(f"bundle {name}: {', '.join(b['running'])} ({len(b['running'])}/{b['total']})")
    return 0


def cmd_launch(args) -> int:
    s = _build_supervisor()
    target = args.target
    if target == "all":
        return _fail("'all' is not a launch target — name an app or bundle "
                     "(see: gd apps list / gd bundles list)")
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
    s = _build_supervisor()
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gd", description="Grid Down control CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="print version").set_defaults(func=cmd_version)

    p_apps = sub.add_parser("apps", help="app registry operations")
    p_apps_sub = p_apps.add_subparsers(dest="apps_cmd", required=True)
    p_list = p_apps_sub.add_parser("list", help="list registered apps")
    p_list.add_argument("--json", action="store_true")
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
    p_launch.set_defaults(func=cmd_launch)

    p_stop = sub.add_parser("stop", help="stop an app, a bundle, or 'all'")
    p_stop.add_argument("target", help="app id, bundle name, or 'all'")
    p_stop.add_argument("--force", action="store_true",
                        help="override safety refusals (e.g. stopping rigctld with dependents up)")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="supervisor status (running/stale instances)")
    p_status.add_argument("--json", action="store_true", help="machine-readable snapshot")
    p_status.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (SupervisorError, registry.RegistryError, profile.ProfileError) as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
