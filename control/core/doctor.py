# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Doctor — station preflight checks (spec §6.7, M1 subset).

Each check returns ok / warn / fail / skip with an operator-facing detail
line. `run_checks()` never raises for foreseeable conditions — failures are
data, so both the CLI and (later) the GUI can render them.

M1 implements: platform sanity, dialout group, serial device presence,
PipeWire tooling, §6.6 loopback port occupancy, supervisor state hygiene
(stale instances, §6.5), profile sanity, wine-prefix presence, air-gap
switch report. Deferred: kit manifest verification, deep PipeWire graph
audit, Meshtastic VID:PID probe, per-app phone-home scan.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

try:  # package import (gd CLI, pytest)
    from . import supervisor as sup
except ImportError:  # pragma: no cover - sibling import inside control/
    import supervisor as sup  # type: ignore

PORTS = sup.PORTS

USB_SERIAL_GLOBS = (
    "/dev/ttyUSB*", "/dev/ttyACM*",
    "/dev/tty.usbserial*", "/dev/cu.usbserial*", "/dev/cu.usbmodem*",
)
WINE_PREFIX = Path("/opt/grid-down/wine-prefix")

DEFERRED = (
    "kit manifest sha256 verification (needs kit/ from a build)",
    "deep PipeWire loopback graph audit (audio module, Phase 2)",
    "Meshtastic USB VID:PID probe (cp210x/ch341)",
    "phone-home scan of running apps when air-gapped",
)


@dataclass
class CheckResult:
    id: str
    title: str
    status: str  # ok | warn | fail | skip
    detail: str


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _load_state_instances(state_dir: Path | None) -> dict:
    path = (Path(state_dir) if state_dir else sup.default_state_dir()) / "supervisor.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("instances", {})


def _check_platform() -> CheckResult:
    if sys.platform.startswith("linux"):
        return CheckResult("platform", "host platform", "ok",
                           f"linux ({os.uname().machine}) — supported target")
    return CheckResult("platform", "host platform", "warn",
                       f"{sys.platform} — not the deployment target (Fedora 45); "
                       "checks are best-effort on a dev host")


def _check_dialout() -> CheckResult:
    if not sys.platform.startswith("linux"):
        return CheckResult("dialout", "serial device group", "skip", "non-Linux host")
    import getpass
    import grp
    user = getpass.getuser()
    groups = set()
    try:
        groups.add(grp.getgrgid(os.getgid()).gr_name)
        for g in grp.getgrall():
            if user in g.gr_mem:
                groups.add(g.gr_name)
    except KeyError:
        pass
    if "dialout" in groups:
        return CheckResult("dialout", "serial device group", "ok", f"{user} is in dialout")
    return CheckResult("dialout", "serial device group", "warn",
                       f"{user} is not in the dialout group — serial rigs/TNCs will be "
                       f"unreadable (fix: sudo usermod -aG dialout {user}, then re-login)")


def _check_serial() -> CheckResult:
    usb = sorted({p for pat in USB_SERIAL_GLOBS for p in glob.glob(pat)})
    legacy = sorted(glob.glob("/dev/ttyS*")) if sys.platform.startswith("linux") else []
    if usb:
        return CheckResult("serial", "serial devices", "ok", "USB serial: " + ", ".join(usb))
    if legacy:
        return CheckResult("serial", "serial devices", "warn",
                           "no USB serial devices found (builtin only: "
                           + ", ".join(legacy[:4]) + ")")
    return CheckResult("serial", "serial devices", "warn",
                       "no serial devices found — attach the rig/TNC, or ignore on a bench host")


def _check_audio() -> CheckResult:
    if not sys.platform.startswith("linux"):
        return CheckResult("audio", "PipeWire audio", "skip", "non-Linux host")
    if shutil.which("pw-cli") or shutil.which("pactl"):
        return CheckResult("audio", "PipeWire audio", "ok",
                           "audio tooling present (pw-cli/pactl); loopback graph audit "
                           "lands with the §6.6 audio module (Phase 2)")
    return CheckResult("audio", "PipeWire audio", "warn",
                       "no PipeWire tooling on PATH (Fedora 45 ships pipewire + pipewire-pulse)")


def _check_ports(ports: dict[str, int], instances: dict) -> list[CheckResult]:
    results = []
    for app_id in sorted(ports):
        port = ports[app_id]
        if not _port_open(port):
            results.append(CheckResult(f"port-{port}", f"loopback port {port} ({app_id})",
                                       "ok", "free"))
        elif app_id in instances:
            results.append(CheckResult(f"port-{port}", f"loopback port {port} ({app_id})",
                                       "ok", f"in use by suite instance {app_id}"))
        else:
            results.append(CheckResult(f"port-{port}", f"loopback port {port} ({app_id})",
                                       "warn", "occupied by an unknown listener — the suite "
                                               f"{app_id} may fail to bind (spec §6.6)"))
    return results


def _check_state(state_dir: Path | None) -> CheckResult:
    instances = _load_state_instances(state_dir)
    if not instances:
        return CheckResult("state", "supervisor state", "ok", "no instances recorded")
    stale = sorted(a for a, e in instances.items() if not sup.pid_alive(e.get("pid", -1)))
    if stale:
        return CheckResult("state", "supervisor state", "warn",
                           "stale entries (process gone): " + ", ".join(stale)
                           + " — clean with: gd stop <id>")
    return CheckResult("state", "supervisor state", "ok",
                       f"{len(instances)} instance(s) alive")


def _check_profile(profile_data: dict | None) -> list[CheckResult]:
    if profile_data is None:
        return [CheckResult("profile", "station profile", "skip", "no profile loaded")]
    out: list[CheckResult] = []
    p = profile_data.get("profile", {})
    missing = [k for k in ("callsign", "grid") if not p.get(k)]
    if missing:
        out.append(CheckResult("profile", "station profile", "warn",
                               "empty fields: " + ", ".join(missing)
                               + " — fill in ~/.config/grid-down/profiles/ or $GRID_DOWN_PROFILE"))
    else:
        out.append(CheckResult("profile", "station profile", "ok",
                               f"id={p.get('id')} callsign={p.get('callsign')} grid={p.get('grid')}"))
    path = str(profile_data.get("rig", {}).get("path") or "")
    if path and os.path.exists(path):
        out.append(CheckResult("rig-path", "rig serial path", "ok", path))
    else:
        out.append(CheckResult("rig-path", "rig serial path", "warn",
                               f"{path or '(unset)'} not present — rigctld will fail until "
                               "the rig is attached"))
    if profile_data.get("internet", {}).get("present") is False:
        out.append(CheckResult("air-gap", "air-gap master switch", "ok",
                               "internet.present=false — suite stays fully offline (G2); "
                               "per-app phone-home scan lands with adapters"))
    else:
        out.append(CheckResult("air-gap", "air-gap master switch", "warn",
                               "internet.present is not false — set it for emergency operation"))
    return out


def _check_wine() -> CheckResult:
    if WINE_PREFIX.is_dir():
        return CheckResult("wine", "VARA wine prefix", "ok", str(WINE_PREFIX))
    return CheckResult("wine", "VARA wine prefix", "skip",
                       f"{WINE_PREFIX} not installed yet (Phase 3 wine hub)")


def _check_deferred() -> CheckResult:
    return CheckResult("deferred", "checks deferred to later phases", "skip",
                       "; ".join(DEFERRED))


def run_checks(state_dir: Path | None = None, profile_data: dict | None = None,
               ports: dict[str, int] | None = None) -> list[CheckResult]:
    """Run every M1 check; returns results in stable order. Never raises."""
    ports = PORTS if ports is None else ports
    instances = _load_state_instances(state_dir)
    results = [_check_platform(), _check_dialout(), _check_serial(), _check_audio()]
    results += _check_ports(ports, instances)
    results.append(_check_state(state_dir))
    results += _check_profile(profile_data)
    results.append(_check_wine())
    results.append(_check_deferred())
    return results


def all_ok(results: list[CheckResult]) -> bool:
    return not any(r.status == "fail" for r in results)


def format_text(results: list[CheckResult]) -> str:
    glyph = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "skip": "skip"}
    lines = [f"[{glyph[r.status]}] {r.title}: {r.detail}" for r in results]
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = ", ".join(f"{counts.get(k, 0)} {k}" for k in ("ok", "warn", "fail", "skip"))
    lines.append("")
    tail = "" if all_ok(results) else " — fix FAIL items before field use"
    lines.append(f"doctor: {summary}{tail}")
    return "\n".join(lines)


def to_json(results: list[CheckResult]) -> str:
    return json.dumps({"ok": all_ok(results), "checks": [asdict(r) for r in results]}, indent=2)