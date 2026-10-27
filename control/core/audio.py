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

# Launch-cmd placeholder defaults per app (profile audio.presets override).
# Keys match the <tokens> used in control/registry/apps/*.yaml launch.cmd.
DEFAULT_PRESETS: dict[str, dict[str, str]] = {
    "ardop": {"capture": "grid_down_hf_out", "playback": "grid_down_hf_in"},
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
    """Create the role's 'out' endpoint: a null source apps capture from."""
    return [
        "pactl",
        "load-module",
        "module-null-source",
        f"source_name={name}",
        f"node.name={name}",
        f"source_properties=device.description=GridDown_{role}_out",
    ]


def plan(profile: dict | None, exists=None) -> list[dict]:
    """Pure plan of the owned graph; `exists(name) -> bool` marks live nodes.

    Each step: {endpoint, role, kind (sink|source), argv, state
    (create|exists)}. Loopback steps to the physical rig are Phase 3 and are
    surfaced by the CLI from audio.rig_capture, not fabricated here.
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
    for kind in ("sinks", "sources"):
        for line in run(["pactl", "list", "short", kind]).splitlines():
            cols = line.split("\t")
            if len(cols) > 1 and cols[1] == name:
                return True
    return False


def down(_profile: dict | None = None, runner=None) -> dict:
    """Unload every owned module; refuses to touch non-grid_down modules."""
    run = runner or _runner
    unloaded: list[str] = []
    listing = run(["pactl", "list", "short", "modules"])
    for line in listing.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        index, args = cols[0], cols[2]
        if f"{PREFIX}" in args and re.search(r"(sink|source)_name=" + PREFIX, args):
            run(["pactl", "unload-module", index])
            unloaded.append(index)
    return {"unloaded": unloaded}


def status(runner=None) -> dict:
    """Which owned endpoints exist right now (no state changes)."""
    run = runner or _runner
    expected = [endpoint_name(role, direction) for role in ROLES for direction in ("in", "out")]
    present = [e for e in expected if _owned_listed(e, run)]
    return {"present": present, "missing": [e for e in expected if e not in present]}
