#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""gd — Grid Down command-line control interface (Phase 0 stub).

Implemented: version, apps list, bundles list, profile show, doctor (stub).
Later phases add: launch/stop (supervisor), config apply (adapters), install
progress, winehub, bundle sessions.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import profile, registry  # noqa: E402

VERSION = "0.1.0-phase0"
ROOT = Path(__file__).resolve().parents[2]


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


def cmd_doctor(_args) -> int:
    print("doctor: not implemented until Phase 1 (udev/dialout, PipeWire graph, "
          "rigctld ports, Wine prefix health, air-gap violations)")
    return 2


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
    sub.add_parser("doctor", help="system preflight checks (stub)").set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
