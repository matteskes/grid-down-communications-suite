# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Doctor — station preflight checks (spec §6.7, M1 subset).

Each check returns ok / warn / fail / skip with an operator-facing detail
line. `run_checks()` never raises for foreseeable conditions — failures are
data, so both the CLI and (later) the GUI can render them.

M1 implements: platform sanity, dialout group, serial device presence,
PipeWire tooling, §6.6 loopback port occupancy, supervisor state hygiene
(stale instances, §6.5), profile sanity, wine-prefix presence, air-gap
switch report. M4 adds the systemd user-unit check (§6.5): unit-set drift
vs a fresh render, and — with a user bus reachable — services outliving
every bound target. M5 upgrades the wine check to the wine hub (§6.4):
prefix + wine binary + COM mapping report via core/winehub. M9 (§6.11)
adds the mesh checks: handset VID:PID-family sweep (cp210x/ch341,
injectable globs), node payload sanity (incl. the daemon/handset
conflict), and the MQTT air-gap scan of the meshtasticd config files
(device-side MQTT unscannable — stated limitation). Deferred: kit
manifest verification, deep PipeWire graph audit, per-app phone-home
scan beyond the meshtasticd MQTT surface.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import socket
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

try:  # package import (gd CLI, pytest)
    from . import audio, registry
    from . import supervisor as sup
    from . import systemd, winehub
except ImportError:  # pragma: no cover - sibling import inside control/
    import audio
    import registry
    import supervisor as sup  # type: ignore
    import systemd
    import winehub  # type: ignore

PORTS = sup.PORTS

USB_SERIAL_GLOBS = (
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
    "/dev/tty.usbserial*",
    "/dev/cu.usbserial*",
    "/dev/cu.usbmodem*",
)
# Wine prefix lives in core/winehub.py (single source, M5) — doctor and the
# install-state probe both read it from there.

# M9 mesh checks: the VID:PID-family sweep globs USB serial devices (by-id
# names carry the cp210x/ch341 chip families) and the MQTT scan reads the
# meshtasticd config files read-only (upstream config.yaml is never touched).
# Both constants are monkeypatched by tests — a bench/VM host has neither the
# handset nor /etc/meshtasticd.
MESH_USB_GLOBS = USB_SERIAL_GLOBS + ("/dev/serial/by-id/*",)
MESHTASTICD_CONFIG_FILES = (
    Path("/etc/meshtasticd/config.yaml"),  # upstream-managed default (read-only here)
    Path("/etc/meshtasticd/config.d/99-grid-down.yaml"),  # suite-owned drop-in
)

DEFERRED = (
    "kit manifest sha256 verification (needs kit/ from a build)",
    "per-app phone-home scan beyond the meshtasticd MQTT surface (M9)",
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
    except OSError, json.JSONDecodeError:
        return {}
    return data.get("instances", {})


def _check_platform() -> CheckResult:
    if sys.platform.startswith("linux"):
        return CheckResult(
            "platform", "host platform", "ok", f"linux ({os.uname().machine}) — supported target"
        )
    return CheckResult(
        "platform",
        "host platform",
        "warn",
        f"{sys.platform} — not the deployment target (Fedora 45); "
        "checks are best-effort on a dev host",
    )


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
    return CheckResult(
        "dialout",
        "serial device group",
        "warn",
        f"{user} is not in the dialout group — serial rigs/TNCs will be "
        f"unreadable (fix: sudo usermod -aG dialout {user}, then re-login)",
    )


def _check_serial() -> CheckResult:
    usb = sorted({p for pat in USB_SERIAL_GLOBS for p in glob.glob(pat)})
    legacy = sorted(glob.glob("/dev/ttyS*")) if sys.platform.startswith("linux") else []
    if usb:
        return CheckResult("serial", "serial devices", "ok", "USB serial: " + ", ".join(usb))
    if legacy:
        return CheckResult(
            "serial",
            "serial devices",
            "warn",
            "no USB serial devices found (builtin only: " + ", ".join(legacy[:4]) + ")",
        )
    return CheckResult(
        "serial",
        "serial devices",
        "warn",
        "no serial devices found — attach the rig/TNC, or ignore on a bench host",
    )


def _check_audio() -> CheckResult:
    if not sys.platform.startswith("linux"):
        return CheckResult(
            "audio",
            "PipeWire audio",
            "skip",
            "non-Linux host — owned endpoints (`gd audio up`) need PipeWire",
        )
    if not shutil.which("pactl"):
        return CheckResult(
            "audio",
            "PipeWire audio",
            "warn",
            "pactl not found (pipewire-pulse) — the suite cannot own its "
            "audio endpoints (dnf install pipewire-pulse)",
        )
    try:
        st = audio.status()
    except audio.AudioError as exc:
        return CheckResult("audio", "PipeWire audio", "warn", f"no live PipeWire session ({exc})")
    if st["missing"]:
        return CheckResult(
            "audio",
            "PipeWire audio",
            "warn",
            "owned endpoints missing: " + ", ".join(st["missing"]) + " — create with: gd audio up",
        )
    return CheckResult(
        "audio", "PipeWire audio", "ok", "owned endpoints present: " + ", ".join(st["present"])
    )


def _check_rig_model(profile_data: dict | None) -> CheckResult:
    rig = (profile_data or {}).get("rig", {}) or {}
    model = str(rig.get("model") or "").strip()
    if not model:
        return CheckResult(
            "rig-model",
            "rig model (hamlib)",
            "warn",
            "rig.model unset — rigctld starts on the Dummy backend "
            "(safe loopback, no radio); set the numeric id from `rigctl -l`",
        )
    if not model.isdigit():
        return CheckResult(
            "rig-model",
            "rig model (hamlib)",
            "warn",
            f"rig.model {model!r} is not numeric — rigctld needs the " "id column of `rigctl -l`",
        )
    return CheckResult(
        "rig-model",
        "rig model (hamlib)",
        "ok",
        f"model {model} → rigctld -m {model} at 127.0.0.1:{PORTS['hamlib']}",
    )


def _check_ports(ports: dict[str, int], instances: dict) -> list[CheckResult]:
    results = []
    for app_id in sorted(ports):
        port = ports[app_id]
        if not _port_open(port):
            results.append(
                CheckResult(f"port-{port}", f"loopback port {port} ({app_id})", "ok", "free")
            )
        elif app_id in instances:
            results.append(
                CheckResult(
                    f"port-{port}",
                    f"loopback port {port} ({app_id})",
                    "ok",
                    f"in use by suite instance {app_id}",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"port-{port}",
                    f"loopback port {port} ({app_id})",
                    "warn",
                    "occupied by an unknown listener — the suite "
                    f"{app_id} may fail to bind (spec §6.6)",
                )
            )
    return results


def _check_state(state_dir: Path | None) -> CheckResult:
    instances = _load_state_instances(state_dir)
    if not instances:
        return CheckResult("state", "supervisor state", "ok", "no instances recorded")
    stale = sorted(a for a, e in instances.items() if not sup.pid_alive(e.get("pid", -1)))
    if stale:
        return CheckResult(
            "state",
            "supervisor state",
            "warn",
            "stale entries (process gone): " + ", ".join(stale) + " — clean with: gd stop <id>",
        )
    return CheckResult("state", "supervisor state", "ok", f"{len(instances)} instance(s) alive")


def _check_profile(profile_data: dict | None) -> list[CheckResult]:
    if profile_data is None:
        return [CheckResult("profile", "station profile", "skip", "no profile loaded")]
    out: list[CheckResult] = []
    p = profile_data.get("profile", {})
    missing = [k for k in ("callsign", "grid") if not p.get(k)]
    if missing:
        out.append(
            CheckResult(
                "profile",
                "station profile",
                "warn",
                "empty fields: "
                + ", ".join(missing)
                + " — fill in ~/.config/grid-down/profiles/ or $GRID_DOWN_PROFILE",
            )
        )
    else:
        out.append(
            CheckResult(
                "profile",
                "station profile",
                "ok",
                f"id={p.get('id')} callsign={p.get('callsign')} grid={p.get('grid')}",
            )
        )
    path = str(profile_data.get("rig", {}).get("path") or "")
    if path and os.path.exists(path):
        out.append(CheckResult("rig-path", "rig serial path", "ok", path))
    else:
        out.append(
            CheckResult(
                "rig-path",
                "rig serial path",
                "warn",
                f"{path or '(unset)'} not present — rigctld will fail until " "the rig is attached",
            )
        )
    if profile_data.get("internet", {}).get("present") is False:
        out.append(
            CheckResult(
                "air-gap",
                "air-gap master switch",
                "ok",
                "internet.present=false — suite stays fully offline (G2); "
                "meshtastic MQTT is scanned below (mesh-mqtt)",
            )
        )
    else:
        out.append(
            CheckResult(
                "air-gap",
                "air-gap master switch",
                "warn",
                "internet.present is not false — set it for emergency operation",
            )
        )
    return out


def _mesh_section(profile_data: dict | None) -> tuple[dict, dict, dict]:
    mesh = (profile_data or {}).get("mesh") or {}
    return mesh.get("device") or {}, mesh.get("node") or {}, mesh.get("daemon") or {}


def _check_mesh_handset(profile_data: dict | None) -> CheckResult:
    """M9 (§6.11): the USB handset path — profile sanity + presence via the
    VID:PID chip-family sweep (cp210x / ch341 shape the by-id device names)."""
    cid, title = "mesh-handset", "mesh handset (cp210x/ch341)"
    if profile_data is None:
        return CheckResult(cid, title, "skip", "no profile loaded")
    dev, _node, daemon = _mesh_section(profile_data)
    if daemon.get("enabled"):
        return CheckResult(
            cid,
            title,
            "skip",
            "mesh.daemon.enabled=true — meshtasticd owns the radio (LoRa HAT); "
            "handset probe not applicable",
        )
    dtype = str(dev.get("type") or "").strip().lower()
    if not dtype:
        return CheckResult(
            cid, title, "skip", "mesh.device.type unset — configure the handset (`gd profile mesh`)"
        )
    if dtype == "tcp":
        host = str(dev.get("host") or "").strip()
        if not host:
            return CheckResult(
                cid,
                title,
                "warn",
                "mesh.device.type is tcp but mesh.device.host is empty — set it in the profile",
            )
        return CheckResult(cid, title, "ok", f"tcp node {host} — handset probe not applicable")
    if dtype == "ble":
        return CheckResult(
            cid,
            title,
            "ok",
            "ble node — the meshtastic CLI discovers the handset; keep it powered and paired",
        )
    if dtype != "serial":
        return CheckResult(
            cid,
            title,
            "warn",
            f"mesh.device.type {dtype!r} is not serial|tcp|ble — fix the profile (spec §4)",
        )
    path = str(dev.get("path") or "").strip()
    if not path:
        return CheckResult(
            cid,
            title,
            "warn",
            "mesh.device.type is serial but mesh.device.path is empty — set it "
            "(e.g. /dev/ttyUSB1 — `ls /dev/serial/by-id/`)",
        )
    hits = sorted({p for pat in MESH_USB_GLOBS for p in glob.glob(pat)})
    family = [p for p in hits if re.search(r"cp210|ch341", p, re.I)]
    if path in hits:
        detail = f"{path} present"
        if family:
            detail += " — cp210x/ch341 handset detected: " + ", ".join(family[:3])
        return CheckResult(cid, title, "ok", detail)
    if family:
        return CheckResult(
            cid,
            title,
            "warn",
            f"{path} not present, but a cp210x/ch341 handset is on: {family[0]} — "
            "point mesh.device.path at it (`ls /dev/serial/by-id/`)",
        )
    return CheckResult(
        cid,
        title,
        "warn",
        f"{path} not present — attach the handset (cp210x 10c4:ea60 / ch341 1a86:7523)",
    )


def _check_mesh_node(profile_data: dict | None) -> CheckResult:
    """M9: the payload the meshtastic-cli adapter would push — set vs empty,
    and the one conflict §6.11 forbids (daemon + handset identity at once).
    channel_url can carry the PSK: the summary reports it set, never its value."""
    cid, title = "mesh-node", "mesh node payload (profile)"
    if profile_data is None:
        return CheckResult(cid, title, "skip", "no profile loaded")
    _dev, node, daemon = _mesh_section(profile_data)
    if daemon.get("enabled") and any(str(v or "").strip() for v in node.values()):
        return CheckResult(
            cid,
            title,
            "warn",
            "mesh.daemon.enabled=true (HAT) and handset node identity are both set — pick one "
            "radio path: clear mesh.node for the HAT, or mesh.daemon.enabled for the handset",
        )
    summary = ", ".join(
        f"{k}={v}" for k, v in node.items() if str(v or "").strip() and k != "channel_url"
    )
    if str(node.get("channel_url") or "").strip():
        summary += (", " if summary else "") + "channel_url set (PSK masked)"
    if not summary:
        return CheckResult(
            cid,
            title,
            "skip",
            "mesh.node empty — nothing to push (`gd profile mesh` / GUI Mesh tab)",
        )
    return CheckResult(cid, title, "ok", summary)


def _check_mesh_mqtt(profile_data: dict | None) -> CheckResult:
    """M9: the only meshtasticd-side phone-home is MQTT (§6.11). MQTT on the
    handset itself is device-side and not file-scannable here — stated limitation."""
    cid, title = "mesh-mqtt", "mesh MQTT uplink (air-gap scan)"
    if (profile_data or {}).get("internet", {}).get("present") is not False:
        return CheckResult(
            cid,
            title,
            "skip",
            "internet.present is not false — the scan applies to air-gapped stations",
        )
    present, hits, unreadable = False, [], []
    for path in MESHTASTICD_CONFIG_FILES:
        if not path.is_file():
            continue
        present = True
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except OSError, yaml.YAMLError:
            unreadable.append(str(path))
            continue
        stack = [("", data)]
        while stack:
            prefix, obj = stack.pop()
            if isinstance(obj, dict):
                for k, v in obj.items():
                    stack.append((f"{prefix}.{k}" if prefix else str(k), v))
            elif "mqtt" in prefix.lower() and obj:
                hits.append(f"{prefix}={obj} ({path})")
    if hits:
        return CheckResult(
            cid,
            title,
            "warn",
            "MQTT uplink configured while air-gapped — disable it (spec §6.11): "
            + "; ".join(hits[:4]),
        )
    if unreadable:
        return CheckResult(
            cid,
            title,
            "warn",
            "meshtasticd config present but unreadable: " + ", ".join(unreadable),
        )
    if not present:
        return CheckResult(
            cid,
            title,
            "skip",
            "no meshtasticd config on disk — daemon not installed, no MQTT surface",
        )
    return CheckResult(
        cid,
        title,
        "ok",
        "no MQTT uplink configured in: " + ", ".join(str(p) for p in MESHTASTICD_CONFIG_FILES),
    )


def _check_mesh(profile_data: dict | None) -> list[CheckResult]:
    """M9 (§6.11): handset presence, node payload sanity, MQTT air-gap scan."""
    return [
        _check_mesh_handset(profile_data),
        _check_mesh_node(profile_data),
        _check_mesh_mqtt(profile_data),
    ]


def _check_wine() -> CheckResult:
    """VARA wine hub (M5): prefix + wine + COM map, driven by core/winehub."""
    try:
        st = winehub.WineHub(apps=[]).status()
    except Exception as exc:  # doctor contract: failures are data, never raises
        return CheckResult("wine", "VARA wine hub", "warn", f"could not check: {exc}")
    if not st["prefix"]:
        return CheckResult(
            "wine",
            "VARA wine hub",
            "skip",
            f"{st['prefix_path']} not installed yet (kit postinst creates it; "
            "`gd winehub status` for detail)",
        )
    if not st["wine"]:
        return CheckResult(
            "wine",
            "VARA wine hub",
            "warn",
            f"prefix {st['prefix_path']} present but the wine binary is not on "
            "PATH — install it (Fedora 45: sudo dnf install wine)",
        )
    detail = f"prefix {st['prefix_path']}"
    if st["com"]:
        detail += "; " + ", ".join(f"{c}->{d}" for c, d in sorted(st["com"].items()))
    else:
        detail += "; no COM mappings (gd winehub map)"
    return CheckResult("wine", "VARA wine hub", "ok", detail)


def _check_deferred() -> CheckResult:
    return CheckResult("deferred", "checks deferred to later phases", "skip", "; ".join(DEFERRED))


def _load_registry() -> tuple[list[dict], dict]:
    root = Path(__file__).resolve().parents[1]
    return (
        registry.load_registry(root / "registry" / "apps"),
        registry.load_bundles(root / "registry" / "bundles.yaml"),
    )


def _check_systemd_units(
    state_dir: Path | None = None, profile_data: dict | None = None
) -> CheckResult:
    """M4 (§6.5): one result covering the optional systemd backend —
    skip when no units are installed; WARN on drift vs a fresh render;
    FAIL when a running service would outlive every target bound to it."""
    cid, title = "systemd", "systemd user units (§6.5)"
    try:
        installed = systemd.installed_unit_paths()
        if not installed:
            return CheckResult(
                cid,
                title,
                "skip",
                "no grid-down units installed (direct backend in "
                "use — optional: `gd units install`)",
            )
        apps, bundles = _load_registry()
        n_svc = sum(1 for p in installed if p.name.endswith(".service"))
        n_tgt = len(installed) - n_svc
        # render with the same profile the installer used (`gd units install`
        # builds its Supervisor from profile.load_profile()): the rig section
        # shapes hamlib's ExecStart, so a bare Supervisor here would flag
        # every profiled install as drifted (P2 Fedora 45 VM finding)
        if systemd.units_current(
            sup.Supervisor(apps, bundles, state_dir=state_dir, profile=profile_data)
        ):
            status = "ok"
            detail = (
                f"{len(installed)} units installed, current with "
                f"registry/profile ({n_svc} services, {n_tgt} targets)"
            )
        else:
            status = "warn"
            detail = (
                f"{len(installed)} units installed but drifted from "
                f"registry/profile — re-run: `gd units install`"
            )
        ok_bus, why = systemd.backend_available()
        if not ok_bus:  # no user session: runtime unit state not observable
            if status == "ok":
                detail += f"; runtime state not checked ({why})"
            return CheckResult(cid, title, status, detail)
        svc = systemd.query_unit_states(f"{systemd.UNIT_PREFIX}*.service")
        tgt = systemd.query_target_states()
        offenders = []
        for app in apps:
            unit = systemd.service_name(app["id"])
            if svc.get(unit) != "running":
                continue
            bound = systemd.service_bound_targets(app["id"], bundles)
            if bound and all(tgt.get(t) != "running" for t in bound):
                offenders.append(unit)
        if offenders:
            return CheckResult(
                cid,
                title,
                "fail",
                "service(s) running but outliving every bound target — stop them "
                "(`gd stop <app> --backend systemd`) or restart their bundle: "
                + ", ".join(offenders),
            )
        if status == "ok":
            detail += "; runtime clean (no service outliving its targets)"
        return CheckResult(cid, title, status, detail)
    except Exception as exc:  # doctor contract: failures are data, never raises
        return CheckResult(cid, title, "warn", f"could not check units: {exc}")


def run_checks(
    state_dir: Path | None = None,
    profile_data: dict | None = None,
    ports: dict[str, int] | None = None,
) -> list[CheckResult]:
    """Run every check; returns results in stable order. Never raises."""
    ports = PORTS if ports is None else ports
    instances = _load_state_instances(state_dir)
    results = [_check_platform(), _check_dialout(), _check_serial(), _check_audio()]
    results += _check_ports(ports, instances)
    results.append(_check_state(state_dir))
    results.append(_check_systemd_units(state_dir, profile_data))
    results += _check_profile(profile_data)
    results.append(_check_rig_model(profile_data))
    results += _check_mesh(profile_data)
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
