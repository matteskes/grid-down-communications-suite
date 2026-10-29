# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Station profile model: load/resolve + atomic form saves.

Schema: docs/SPECIFICATION.md §4. The GUI form savers (save_rig,
save_station, save_audio) share the _persist write path below.

Resolution order:
  1. $GRID_DOWN_PROFILE (path to a profile YAML)
  2. newest ~/.config/grid-down/profiles/*.yaml
  3. <repo>/profiles/default.yaml
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

REQUIRED_KEYS = ("profile",)


class ProfileError(RuntimeError):
    pass


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ProfileError("PyYAML is required (python3 -m pip install pyyaml)") from exc
    return yaml


def default_repo_profile() -> Path:
    return Path(__file__).resolve().parents[2] / "profiles" / "default.yaml"


def profile_path() -> Path:
    env = os.environ.get("GRID_DOWN_PROFILE")
    if env:
        return Path(env)
    user_dir = Path.home() / ".config" / "grid-down" / "profiles"
    candidates = sorted(user_dir.glob("*.yaml"))
    if candidates:
        return candidates[-1]
    return default_repo_profile()


def load_profile(path: Path | None = None) -> dict:
    path = path or profile_path()
    if not path.exists():
        raise ProfileError(f"profile not found: {path}")
    data = _yaml().safe_load(path.read_text()) or {}
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ProfileError(f"{path}: missing keys {missing}")
    return data


def _persist(mutate: Callable[[dict], None]) -> dict:
    """Shared write path for the form savers (save_rig / save_station / save_audio).

    The shipped repo template (profiles/default.yaml) is never modified:
    when it is the resolved profile, the full resolved content is written to
    ~/.config/grid-down/profiles/<id>.yaml — which profile_path() prefers, so
    the edit wins on the next load. Any other resolved profile is edited in
    place, atomically (tmp + rename). `mutate(data)` mutates the loaded dict
    in place; callers validate upstream, so a raised ProfileError writes
    nothing.

    Returns {"path": <written file>, "created": bool}.
    """
    resolved = profile_path()
    target, created = resolved, False
    if resolved == default_repo_profile():
        head = load_profile(resolved).get("profile", {}) or {}
        stem = str(head.get("id") or "station").strip() or "station"
        target = Path.home() / ".config" / "grid-down" / "profiles" / f"{stem}.yaml"
        created = not target.exists()

    data = load_profile(resolved)  # base = resolved content: nothing is lost
    mutate(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(_yaml().safe_dump(data, sort_keys=False, default_flow_style=False))
    os.replace(tmp, target)
    return {"path": str(target), "created": created}


def save_rig(model: str, path: str, baud: str | None = None) -> dict:
    """Validate + persist the rig section (spec §4) into the active profile.

    Validation mirrors what supervisor.rig_argv/Doctor expect: model empty
    (hamlib Dummy, bench-safe) or a numeric hamlib id; baud numeric. Raises
    ProfileError and writes nothing on bad input. baud is only ever set,
    never cleared.

    Returns {"path": <written file>, "created": bool, "rig": <new rig dict>}.
    """
    model_s, path_s, baud_s = (
        str(model or "").strip(),
        str(path or "").strip(),
        str(baud or "").strip(),
    )
    if model_s and not model_s.isdigit():
        raise ProfileError(
            f"rig.model {model_s!r} is not a numeric hamlib id"
            " (pick one from `rigctl -l`; leave empty for the Dummy backend)"
        )
    if baud_s and not baud_s.isdigit():
        raise ProfileError(f"rig.baud {baud_s!r} is not numeric (e.g. 38400)")

    rig: dict = {}

    def mutate(data: dict) -> None:
        r = data.setdefault("rig", {})
        r["model"] = model_s
        r["path"] = path_s
        if baud_s:
            r["baud"] = baud_s
        rig.clear()
        rig.update(r)

    out = _persist(mutate)
    out["rig"] = dict(rig)
    return out


_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,7}$")
_GRID_RE = re.compile(r"^([A-R]{2})([0-9]{2})([A-X]{2})?$")


def save_station(callsign: str, grid: str) -> dict:
    """Validate + persist profile.callsign / profile.grid (spec §4).

    callsign is uppercased and shape-checked (3-7 alphanumerics — `W1AW`,
    `VE3XYZ`, `2E0ABC`); a portable suffix like `W1AW/6` is rejected, only
    the base callsign is stored. grid must be a 4- or 6-character Maidenhead
    square (A-R field pair, digit pair, optional A-X subsquare) and is
    canonicalized to FN31pr case. Both may be empty: unlike rig.baud (set-
    never-clear) these start empty in the shipped template and Doctor warns
    while they are missing, so clearing is a legitimate state. Raises
    ProfileError and writes nothing on bad input.

    Returns {"path": <written file>, "created": bool, "profile": <new head>}.
    """
    call = str(callsign or "").strip().upper()
    grid_s = str(grid or "").strip()
    if call and not _CALLSIGN_RE.fullmatch(call):
        raise ProfileError(
            f"profile.callsign {call!r} does not look like a callsign"
            " (e.g. W1AW — letters/digits, 3-7 characters)"
        )
    m = _GRID_RE.fullmatch(grid_s.upper()) if grid_s else None
    if grid_s and not m:
        raise ProfileError(
            f"profile.grid {grid_s!r} is not a Maidenhead grid square"
            " (4 or 6 characters, e.g. FN31 or FN31pr)"
        )
    grid_c = ""
    if m:
        grid_c = m.group(1) + m.group(2) + (m.group(3) or "").lower()

    head: dict = {}

    def mutate(data: dict) -> None:
        h = data.setdefault("profile", {})
        h["callsign"] = call
        h["grid"] = grid_c
        head.clear()
        head.update(h)

    out = _persist(mutate)
    out["profile"] = dict(head)
    return out


_AUDIO_NAME_RE = re.compile(r"^[A-Za-z0-9._:/~+-]+$")


def save_audio(
    backend: str, rig_capture: str, ardop_capture: str, ardop_playback: str, rig_playback: str = ""
) -> dict:
    """Validate + persist the audio section (spec §4) into the active profile.

    backend: only 'pipewire' is implemented (core/audio.py drives pactl from
    pipewire-pulse), so anything else is rejected; empty stores the template
    default 'pipewire'. rig_capture names the physical rig capture source and
    rig_playback the physical rig playback sink for the P3b loopback
    (`gd audio wire` wires them into the owned endpoints; either may be empty
    — the bench-safe pending state — and the GUI calls this with keywords).
    The ardop capture/playback presets override DEFAULT_PRESETS (core/
    audio.py); clearing a preset pops the key so resolve_preset falls back to
    the default (its lookup is falsy-based). Non-empty names must be
    PipeWire/Pulse endpoint names — no spaces, quotes or shell
    metacharacters, since supervisor expands them into launch cmds.
    Raises ProfileError and writes nothing on bad input.

    Returns {"path": <written file>, "created": bool, "audio": <new audio dict>}.
    """
    backend_s = str(backend or "").strip() or "pipewire"
    if backend_s != "pipewire":
        raise ProfileError(
            f"audio.backend {backend_s!r} is not implemented — only 'pipewire'"
            " (core/audio.py drives pactl from pipewire-pulse)"
        )
    rig_capture_s = str(rig_capture or "").strip()
    rig_playback_s = str(rig_playback or "").strip()
    ardop = {
        "capture": str(ardop_capture or "").strip(),
        "playback": str(ardop_playback or "").strip(),
    }
    for key, value in (
        ("audio.rig_capture", rig_capture_s),
        ("audio.rig_playback", rig_playback_s),
        ("audio.presets.ardop.capture", ardop["capture"]),
        ("audio.presets.ardop.playback", ardop["playback"]),
    ):
        if value and not _AUDIO_NAME_RE.fullmatch(value):
            raise ProfileError(
                f"{key} {value!r} is not a PipeWire/Pulse endpoint name"
                " (no spaces, quotes, or shell metacharacters)"
            )

    audio_out: dict = {}

    def mutate(data: dict) -> None:
        a = data.setdefault("audio", {})
        a["backend"] = backend_s
        a["rig_capture"] = rig_capture_s
        a["rig_playback"] = rig_playback_s
        ard = a.setdefault("presets", {}).setdefault("ardop", {})
        for short, value in ardop.items():
            if value:
                ard[short] = value
            else:
                ard.pop(short, None)
        audio_out.clear()
        audio_out.update(a)

    out = _persist(mutate)
    out["audio"] = dict(audio_out)
    return out


_MESH_TYPES = ("serial", "tcp", "ble")
_MESH_HOST_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_MESH_PATH_RE = re.compile(r"^[A-Za-z0-9._/:-]+$")
_MESH_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]*$")
_MESH_URL_RE = re.compile(r"""^[^"'\s]*$""")
_TRUE_WORDS = {"true", "yes", "1", "on"}


def save_mesh(
    dev_type: str,
    dev_path: str,
    dev_host: str,
    long_name: str,
    short_name: str,
    region: str,
    modem_preset: str,
    channel_url: str,
    daemon_enabled: str,
) -> dict:
    """Validate + persist the mesh section (spec §4 / §6.11).

    dev_type selects how the node is reached: serial (dev_path required,
    e.g. /dev/ttyUSB1), tcp (dev_host required, IP/hostname[:port]) or ble
    (neither). dev_host is stored as null when empty, matching the template.
    Node identity is the Meshtastic `configure` payload — long/short name
    (short_name is capped at Meshtastic's 4 characters), region (US, EU_433,
    …), modem preset (LONG_FAST, …) and the channel URL; the URL can carry
    the PSK, so it only ever lands in the user profile (spec §9 — never the
    repo template). daemon_enabled accepts true/false/yes/no/1/0 (empty =
    template default false) and flips the meshtasticd unit at the next
    `gd units install`. The profile itself is applied to the node by the
    Phase 4 `meshtastic configure` adapter; nothing dials out from here.
    Raises ProfileError and writes nothing on bad input.

    Returns {"path": <written file>, "created": bool, "mesh": <new mesh dict>}.
    """
    type_s = str(dev_type or "").strip().lower() or "serial"
    if type_s not in _MESH_TYPES:
        raise ProfileError(
            f"mesh.device.type {type_s!r} is not one of {'|'.join(_MESH_TYPES)}"
            " (how the node is reached: USB serial, TCP, or Bluetooth)"
        )
    path_s = str(dev_path or "").strip()
    if type_s == "serial" and not path_s:
        raise ProfileError(
            "mesh.device.path is required when device.type is serial"
            " (e.g. /dev/ttyUSB1 — see `ls /dev/serial/by-id/`)"
        )
    if path_s and not _MESH_PATH_RE.fullmatch(path_s):
        raise ProfileError(
            f"mesh.device.path {path_s!r} is not a device path"
            " (e.g. /dev/ttyUSB1 — no spaces or shell metacharacters)"
        )
    host_s = str(dev_host or "").strip()
    if type_s == "tcp" and not host_s:
        raise ProfileError(
            "mesh.device.host is required when device.type is tcp"
            " (IP or hostname, e.g. 192.168.1.50 or meshtastic.lan:4403)"
        )
    if host_s and not _MESH_HOST_RE.fullmatch(host_s):
        raise ProfileError(
            f"mesh.device.host {host_s!r} is not an IP/hostname"
            " (no spaces, quotes, or URL schemes — just the host[:port])"
        )
    long_s = str(long_name or "").strip()
    short_s = str(short_name or "").strip()
    if short_s and len(short_s) > 4:
        raise ProfileError(
            f"mesh.node.short_name {short_s!r} is longer than Meshtastic's"
            " 4-character limit (e.g. W1AW)"
        )
    region_s = str(region or "").strip()
    if region_s and not _MESH_TOKEN_RE.fullmatch(region_s):
        raise ProfileError(
            f"mesh.node.region {region_s!r} is not a Meshtastic region id"
            " (letters/digits/underscores, e.g. US or EU_433)"
        )
    preset_s = str(modem_preset or "").strip()
    if preset_s and not _MESH_TOKEN_RE.fullmatch(preset_s):
        raise ProfileError(
            f"mesh.node.modem_preset {preset_s!r} is not a Meshtastic preset"
            " (letters/digits/underscores, e.g. LONG_FAST)"
        )
    url_s = str(channel_url or "").strip()
    if url_s and not _MESH_URL_RE.fullmatch(url_s):
        raise ProfileError(
            f"mesh.node.channel_url {url_s!r} is not a channel URL"
            " (no spaces or quotes — paste it as meshtastic.org gives it)"
        )
    raw_daemon = str(daemon_enabled if daemon_enabled is not None else "").strip().lower()
    if raw_daemon in _TRUE_WORDS:
        daemon_b = True
    elif raw_daemon in ("", "false", "no", "0", "off"):
        daemon_b = False
    else:
        raise ProfileError(
            f"mesh.daemon.enabled {raw_daemon!r} is not a boolean"
            " (true/false — true runs meshtasticd for a LoRa HAT)"
        )

    mesh_out: dict = {}

    def mutate(data: dict) -> None:
        m = data.setdefault("mesh", {})
        dev = m.setdefault("device", {})
        dev["type"] = type_s
        dev["path"] = path_s
        dev["host"] = host_s or None
        node_d = m.setdefault("node", {})
        node_d["long_name"] = long_s
        node_d["short_name"] = short_s
        node_d["region"] = region_s
        node_d["modem_preset"] = preset_s
        node_d["channel_url"] = url_s
        m.setdefault("daemon", {})["enabled"] = daemon_b
        mesh_out.clear()
        mesh_out.update(m)

    out = _persist(mutate)
    out["mesh"] = dict(mesh_out)
    return out
