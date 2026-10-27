# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""GUI service layer (M3a): the seam between Control Center and core.

Pure Python — no Qt in this module. `Services` bundles zero-arg callables the
GUI polls; `default_services()` wires them to the core modules in-process
(spec §7/G1: the GUI shares core/supervisor + core/audio + core/doctor with
the CLI — it never shells out to `gd`). Tests build `Services` from fakes.

`snapshot(svc)` collects one dashboard payload. Any section that raises is
demoted to an error string in `errors` — the dashboard degrades per-section
(e.g. no pactl on the box) instead of dying.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core import audio, doctor, profile, registry
from core.supervisor import PORTS, Supervisor


def repo_root() -> Path:
    """control/gui/services.py -> repo root (same walk as cli/gd.py)."""
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Services:
    """Zero-arg callables the dashboard polls. Attribute names are the contract
    between views and tests: version, profile, rig, audio, instances, doctor."""

    version: Callable[[], str]
    profile: Callable[[], dict]
    profile_raw: Callable[[], dict]
    rig: Callable[[], dict]
    audio: Callable[[], dict]
    instances: Callable[[], dict]
    apps: Callable[[], dict]
    bundles: Callable[[], dict]
    doctor: Callable[[], list]
    bundle_start: Callable[[str], dict]
    bundle_stop: Callable[[str], dict]
    audio_up: Callable[[], dict]
    audio_down: Callable[[], dict]


def default_services(root: Path | None = None) -> Services:
    """Production wiring: registry + bundles loaded once, everything else
    re-read per snapshot so profile edits and rig changes show up live."""
    root = Path(root) if root is not None else repo_root()
    apps = registry.load_registry(root / "control" / "registry" / "apps")
    bundles = registry.load_bundles(root / "control" / "registry" / "bundles.yaml")

    def _sup() -> Supervisor:
        return Supervisor(apps, bundles, profile=profile.load_profile())

    def _profile() -> dict:
        data = profile.load_profile() or {}
        head = data.get("profile", {}) or {}
        return {
            "path": str(profile.profile_path()),
            "id": str(head.get("id", "") or ""),
            "callsign": str(head.get("callsign", "") or ""),
            "grid": str(head.get("grid", "") or ""),
        }

    def _rig() -> dict:
        argv, notes = _sup().rig_argv()
        rig = (profile.load_profile() or {}).get("rig", {}) or {}
        return {
            "argv": [str(a) for a in argv],
            "notes": [str(n) for n in notes],
            "endpoint": f"127.0.0.1:{PORTS['hamlib']}",
            "model": str(rig.get("model") or ""),
            "path": str(rig.get("path") or ""),
            "baud": str(rig.get("baud") or ""),
        }

    def _doctor() -> list:
        return doctor.run_checks(profile_data=profile.load_profile())

    def _bundle_start(name: str) -> dict:
        return _sup().launch_bundle(name)

    def _bundle_stop(name: str) -> dict:
        return _sup().stop_bundle(name)

    def _audio_up() -> dict:
        return audio.up(profile.load_profile())

    def _audio_down() -> dict:
        return audio.down(profile.load_profile())

    def _apps() -> dict:
        # load_registry() returns a list[dict] (each entry has "id"); the GUI
        # contract is a dict keyed by id so views/tests can index directly.
        if isinstance(apps, dict):
            return apps
        return {str((a or {}).get("id", f"app-{i}")): (a or {}) for i, a in enumerate(apps or [])}

    return Services(
        version=lambda: VERSION,
        profile=_profile,
        profile_raw=profile.load_profile,
        rig=_rig,
        audio=audio.status,
        instances=lambda: _sup().status(),
        apps=_apps,
        bundles=lambda: bundles,
        doctor=_doctor,
        bundle_start=_bundle_start,
        bundle_stop=_bundle_stop,
        audio_up=_audio_up,
        audio_down=_audio_down,
    )


def _counts(results: list) -> dict:
    counts = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def snapshot(svc: Services) -> dict:
    """One dashboard payload. Sections never raise; failures land in `errors`
    keyed by section name so views can show 'unavailable — <why>'."""
    errors: dict[str, str] = {}

    def collect(name: str, fn: Callable, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — deliberate: GUI must survive
            errors[name] = f"{type(exc).__name__}: {exc}"
            return default

    results = collect("doctor", svc.doctor, [])
    return {
        "version": collect("version", svc.version, "?"),
        "profile": collect("profile", svc.profile, {}),
        "profile_raw": collect("profile_raw", svc.profile_raw, {}),
        "rig": collect("rig", svc.rig, {}),
        "audio": collect("audio", svc.audio, {}),
        "instances": collect("instances", svc.instances, {}),
        "apps": collect("apps", svc.apps, {}),
        "bundles": collect("bundles", svc.bundles, {}),
        "doctor": {
            "results": results,
            "counts": _counts(results),
            "all_ok": doctor.all_ok(results),
        },
        "errors": errors,
    }


# gui/__init__ defines VERSION without importing Qt; importing it here is safe
# (no cycle) and keeps the dashboard header on the package version.
from gui import VERSION  # noqa: E402
