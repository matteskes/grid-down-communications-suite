# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Suite-owned PipeWire audio endpoints (spec §6.6, rule G4 — M2).

The suite owns exactly one audio namespace (G4: one graph, suite-owned;
radio apps consume it). Endpoints are PipeWire nodes named with the
``grid_down_`` prefix, one sink + one source per radio role:

    role    in (apps play → radio side)   out (radio side → apps capture)
    hf      grid_down_hf_in               grid_down_hf_out
    packet  grid_down_packet_in           grid_down_packet_out
    voice   grid_down_voice_in            grid_down_voice_out

Rules enforced here:
- Nothing outside this module may create or destroy ``grid_down_*`` nodes;
  ``down()`` unloads only modules whose arguments reference the prefix.
- Apps never address hardware directly: registry launch cmds use
  ``<placeholder>`` tokens (e.g. ``ardopc 8515 <capture> <playback>``)
  resolved through ``presets`` — profile ``audio.presets.<app>.<key>``
  overrides ``DEFAULT_PRESETS`` (spec §4).
- M2 creates the endpoints only. Loopbacking them to the physical rig
  interface (``audio.rig_capture``) lands with the Phase 3 adapter work;
  ``plan()`` output makes the intended final graph visible either way.

Backend: ``pactl`` from pipewire-pulse (Fedora 45 ships it). ``plan()`` and
``resolve_preset()`` are pure and testable without any audio stack.
"""

from __future__ import annotations

import re
import shutil
import subprocess

ROLES = ("hf", "packet", "voice")
PREFIX = "grid_down_"
# P3b rig loopback: roles `gd audio wire` serves when audio.loopback_roles is
# unset (the VARA HF path; add 'packet' for a DireWolf-on-the-rig-audio bench).
DEFAULT_LOOPBACK_ROLES = ("hf",)

# Launch-cmd placeholder defaults per app (profile audio.presets override).
# Keys match the <tokens> used in control/registry/apps/*.yaml launch.cmd.
# Capture tokens name the live monitor of the owned 'out' endpoint (see
# source_argv: pipewire-pulse exposes it as <endpoint>.monitor).
DEFAULT_PRESETS: dict[str, dict[str, str]] = {
    "ardop": {"capture": "grid_down_hf_out.monitor", "playback": "grid_down_hf_in"},
}


class AudioError(RuntimeError):
    """Audio graph operation failed with operator-facing text."""


def endpoint_name(role: str, direction: str) -> str:
    """grid_down_<role>_<direction>; direction is 'in' (sink) or 'out' (source)."""
    if role not in ROLES:
        raise AudioError(f"unknown audio role: {role} (roles: {', '.join(ROLES)})")
    if direction not in ("in", "out"):
        raise AudioError(f"unknown audio direction: {direction} (in|out)")
    return f"{PREFIX}{role}_{direction}"


def resolve_preset(app_id: str, key: str, profile: dict | None) -> str:
    """Resolve a launch-cmd placeholder to an endpoint name (spec §6.6)."""
    presets = ((profile or {}).get("audio", {}) or {}).get("presets", {}) or {}
    value = presets.get(app_id, {}).get(key) or DEFAULT_PRESETS.get(app_id, {}).get(key)
    if not value:
        raise AudioError(
            f"audio preset unresolved: {app_id}.{key} — set "
            f"audio.presets.{app_id}.{key} in the station profile "
            f"(defaults live in control/core/audio.py; see 'gd audio plan')"
        )
    return str(value)


def sink_argv(name: str, role: str) -> list[str]:
    """Create the role's 'in' endpoint: a null sink apps play into."""
    return [
        "pactl",
        "load-module",
        "module-null-sink",
        f"sink_name={name}",
        f"node.name={name}",
        f"sink_properties=device.description=GridDown_{role}_in",
    ]


def source_argv(name: str, role: str) -> list[str]:
    """Create the role's 'out' endpoint: apps capture from its monitor.

    pipewire-pulse has no ``module-null-source`` (PulseAudio-only module —
    found on the real Fedora 45 VM bus, P2 acceptance), so the source is a
    null *sink* whose monitor is the capture node: the live PipeWire source
    name is ``<name>.monitor`` (see :func:`capture_name`).
    """
    return [
        "pactl",
        "load-module",
        "module-null-sink",
        f"sink_name={name}",
        f"node.name={name}",
        f"sink_properties=device.description=GridDown_{role}_out",
    ]


def capture_name(name: str) -> str:
    """Live PipeWire source name for an owned 'out' endpoint: ``<name>.monitor``."""
    return f"{name}.monitor"


def plan(profile: dict | None, exists=None) -> list[dict]:
    """Pure plan of the owned graph; `exists(name) -> bool` marks live nodes.

    Each step: {endpoint, role, kind (sink|source), argv, state
    (create|exists)}. The rig-interface loopbacks (P3b) are planned separately
    by :func:`loopback_rows` — the CLI surfaces both (`gd audio plan`) and
    applies the loopbacks with `gd audio wire`.
    """
    steps: list[dict] = []
    for role in ROLES:
        for direction, kind in (("in", "sink"), ("out", "source")):
            name = endpoint_name(role, direction)
            argv = sink_argv(name, role) if kind == "sink" else source_argv(name, role)
            live = bool(exists and exists(name))
            steps.append(
                {
                    "endpoint": name,
                    "role": role,
                    "kind": kind,
                    "argv": argv,
                    "state": "exists" if live else "create",
                }
            )
    return steps


def _runner(argv: list[str]) -> str:
    """Default pactl runner: returns stdout, raises AudioError on failure."""
    exe = shutil.which("pactl")
    if exe is None:
        raise AudioError(
            "pactl not found — install pipewire-pulse (Fedora 45: "
            "dnf install pipewire-pulse); 'gd audio plan' works offline"
        )
    try:
        proc = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioError(f"pactl {' '.join(argv[1:2] or ['?'])} failed: {exc}") from None
    if proc.returncode != 0:
        raise AudioError(f"pactl {argv[1]} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def up(profile: dict | None, runner=None) -> dict:
    """Create missing owned endpoints (idempotent). Requires a PipeWire session."""
    run = runner or _runner
    created, skipped = [], []
    for step in plan(profile, exists=None):
        name = step["endpoint"]
        if _owned_listed(name, run):
            skipped.append(name)
            continue
        out = run(step["argv"])
        created.append({"endpoint": name, "module": out.splitlines()[-1] if out else "?"})
    return {"created": created, "skipped": skipped}


def _owned_listed(name: str, run) -> bool:
    monitor = capture_name(name)
    for kind in ("sinks", "sources"):
        for line in run(["pactl", "list", "short", kind]).splitlines():
            cols = line.split("\t")
            if len(cols) > 1 and cols[1] in (name, monitor):
                return True
    return False


def down(_profile: dict | None = None, runner=None) -> dict:
    """Unload every owned module; refuses to touch non-grid_down modules.

    Owned means the endpoint null sinks (`sink_name=grid_down_*` / `source_
    name=grid_down_*`) *and* the P3b rig loopbacks, whose module-loopback
    args name the owned endpoints (`sink=grid_down_*` / `source=grid_down_*`
    — e.g. `source=grid_down_hf_in.monitor`). A loopback between two user
    endpoints never carries grid_down_ in its args and is never touched.
    """
    run = runner or _runner
    unloaded: list[str] = []
    listing = run(["pactl", "list", "short", "modules"])
    for line in listing.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        index, args = cols[0], cols[2]
        if f"{PREFIX}" in args and re.search(r"(sink|source)(_name)?=" + PREFIX, args):
            run(["pactl", "unload-module", index])
            unloaded.append(index)
    return {"unloaded": unloaded}


def status(runner=None) -> dict:
    """Which owned endpoints exist right now (no state changes).

    ``loopback`` lists the args of every rig loopback module wired to an
    owned endpoint (P3b, `gd audio wire`) — empty on a bench that only ran
    `gd audio up`.
    """
    run = runner or _runner
    expected = [endpoint_name(role, direction) for role in ROLES for direction in ("in", "out")]
    present = [e for e in expected if _owned_listed(e, run)]
    loopback = [
        f"{name} {args}"
        for _index, name, args in _module_rows(run)
        if "module-loopback" in name and PREFIX in args
    ]
    return {
        "present": present,
        "missing": [e for e in expected if e not in present],
        "loopback": loopback,
    }


# --------------------------------------------------------------------
# P3b: rig-interface loopback wiring (spec §6.4/§6.6) — `gd audio wire|unwire`
# --------------------------------------------------------------------


def loopback_rows(profile: dict | None) -> list[dict]:
    """Pure plan of the rig-interface loopbacks (spec §6.4/§6.6).

    RX: the rig's capture source feeds the owned 'out' endpoint —
    ``source=<audio.rig_capture> sink=grid_down_<role>_out`` — so audio the
    rig *hears* reaches the suite apps. TX: the owned 'in' endpoint's monitor
    feeds the rig's playback sink — ``source=grid_down_<role>_in.monitor
    sink=<audio.rig_playback>`` — so what suite apps play reaches the rig.
    Each side plans only when its profile key is set; with neither key set
    the rows are the documented bench-safe pending state (no rig card on the
    dev box / aarch64 VM — nothing pending is an error).

    Roles come from ``audio.loopback_roles`` (default: hf only; add 'packet'
    for a DireWolf rig-audio bench). Unknown roles refuse with AudioError,
    same contract as endpoint_name().
    """
    cfg = (profile or {}).get("audio", {}) or {}
    capture = str(cfg.get("rig_capture") or "").strip()
    playback = str(cfg.get("rig_playback") or "").strip()
    roles = cfg.get("loopback_roles") or list(DEFAULT_LOOPBACK_ROLES)
    if isinstance(roles, str):
        roles = [roles]
    roles = [str(r).strip() for r in roles if str(r).strip()] or list(DEFAULT_LOOPBACK_ROLES)
    for role in roles:
        if role not in ROLES:
            raise AudioError(
                f"unknown audio role in loopback_roles: {role} (roles: {', '.join(ROLES)})"
            )
    rows: list[dict] = []
    for role in roles:
        out_name = endpoint_name(role, "out")
        in_monitor = capture_name(endpoint_name(role, "in"))
        if capture:
            rows.append(_loopback_row(role, "rx", capture, out_name, "planned"))
        elif not playback:
            rows.append(_loopback_row(role, "rx", "", out_name, "pending (set audio.rig_capture)"))
        if playback:
            rows.append(_loopback_row(role, "tx", in_monitor, playback, "planned"))
        elif not capture:
            rows.append(
                _loopback_row(role, "tx", in_monitor, "", "pending (set audio.rig_playback)")
            )
    return rows


def _loopback_row(role: str, direction: str, source: str, sink: str, state: str) -> dict:
    argv = (
        ["pactl", "load-module", "module-loopback", f"source={source}", f"sink={sink}"]
        if state == "planned"
        else []
    )
    return {
        "role": role,
        "direction": direction,
        "source": source,
        "sink": sink,
        "argv": argv,
        "state": state,
    }


def _module_rows(run) -> list[tuple[str, str, str]]:
    """`(index, name, args)` rows of `pactl list short modules`.

    The module NAME is its own column (1) and never part of the args (2) —
    the loopback matchers need both.
    """
    rows = []
    for line in run(["pactl", "list", "short", "modules"]).splitlines():
        cols = line.split("\t")
        if len(cols) > 2:
            rows.append((cols[0], cols[1], cols[2]))
    return rows


def _loopback_listed(row: dict, run) -> bool:
    """True when a module-loopback with exactly this source/sink pair exists."""
    want_source, want_sink = f"source={row['source']}", f"sink={row['sink']}"
    return any(
        "module-loopback" in name and want_source in args and want_sink in args
        for _index, name, args in _module_rows(run)
    )


def wire(profile: dict | None, runner=None) -> dict:
    """Load the planned rig loopbacks (idempotent; P3b `gd audio wire`).

    Requires the owned endpoints to exist (`gd audio up` first — module-
    loopback binds at load time). Rows whose profile key is missing are
    reported as skipped-with-reason, never an error: a bench without the rig
    card stays green.
    """
    run = runner or _runner
    wired, skipped = [], []
    for row in loopback_rows(profile):
        tag = f"{row['role']}.{row['direction']}"
        if row["state"] != "planned":
            skipped.append(f"{tag}: {row['state']}")
            continue
        if _loopback_listed(row, run):
            skipped.append(f"{tag}: already wired")
            continue
        out = run(row["argv"])
        wired.append({**row, "module": out.splitlines()[-1] if out else "?"})
    return {"wired": wired, "skipped": skipped}


def unwire(runner=None) -> dict:
    """Unload every rig loopback that references an owned endpoint (P3b).

    The loopback half of down(): matches module-loopback args carrying the
    grid_down_ namespace only. A loopback between two user endpoints (e.g. a
    desktop mic monitor → speakers) has no grid_down_ in its args and is
    never touched.
    """
    run = runner or _runner
    unloaded: list[str] = []
    for index, name, args in _module_rows(run):
        if "module-loopback" in name and PREFIX in args:
            run(["pactl", "unload-module", index])
            unloaded.append(index)
    return {"unloaded": unloaded}
