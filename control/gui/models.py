# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Pure view-model builders (M3a): snapshot dict -> display rows.

No Qt here, so these run in plain pytest. Every function tolerates missing
keys and per-section error payloads from gui.services.snapshot() — the
dashboard must degrade, never KeyError.
"""
from __future__ import annotations

import shlex

STATUS_ORDER = ("ok", "warn", "fail", "skip")


def _err(snap: dict, section: str) -> str | None:
    msg = (snap.get("errors") or {}).get(section)
    return f"unavailable — {msg}" if msg else None


def header_rows(snap: dict) -> list[tuple[str, str]]:
    prof = snap.get("profile") or {}
    return [("Version", str(snap.get("version", "?"))),
            ("Profile", str(prof.get("path", "?"))),
            ("Station", str(prof.get("id", "") or "(unset)")),
            ("Callsign", str(prof.get("callsign", "") or "(unset)")),
            ("Grid", str(prof.get("grid", "") or "(unset)"))]


def rig_lines(snap: dict) -> list[str]:
    err = _err(snap, "rig")
    if err:
        return [err]
    rig = snap.get("rig") or {}
    argv = [str(a) for a in (rig.get("argv") or [])]
    lines = [f"$ {shlex.join(argv)}"] if argv else ["$ (no rig command resolved)"]
    lines.append(f"endpoint: {rig.get('endpoint', '?')} — suite-owned singleton (G4)")
    lines.append(f"model: {rig.get('model') or '(unset → hamlib Dummy)'}"
                 f"  path: {rig.get('path') or '(unset)'}"
                 f"  baud: {rig.get('baud') or '(unset)'}")
    lines += [f"note: {n}" for n in rig.get("notes", [])]
    return lines


def audio_rows(snap: dict) -> list[tuple[str, str]]:
    err = _err(snap, "audio")
    if err:
        return [("audio", err)]
    aud = snap.get("audio") or {}
    rows = [(str(n), "present") for n in aud.get("present", [])]
    rows += [(str(n), "MISSING") for n in aud.get("missing", [])]
    return rows or [("audio", "no owned endpoints known (run: gd audio plan)")]


def instance_rows(snap: dict) -> list[tuple[str, str]]:
    err = _err(snap, "instances")
    if err:
        return [("instances", err)]
    rows = (snap.get("instances") or {}).get("apps", []) or []
    active = [(str(r.get("id", "?")), str(r.get("state", "?")))
              for r in rows if r.get("state") in ("running", "stale")]
    return active or [("instances", "nothing running")]


def doctor_lines(snap: dict) -> list[str]:
    err = _err(snap, "doctor")
    if err:
        return [err]
    doc = snap.get("doctor") or {}
    counts = doc.get("counts") or {}
    summary = ", ".join(f"{counts.get(k, 0)} {k}" for k in STATUS_ORDER)
    tail = "" if doc.get("all_ok", True) else " — fix FAIL items before field use"
    return [f"doctor: {summary}{tail}"]


def error_lines(snap: dict) -> list[str]:
    return [f"{section}: {msg}" for section, msg in (snap.get("errors") or {}).items()]


def catalog_rows(snap: dict) -> list[dict]:
    """One row per registry app: id/name/category/via + live supervisor state."""
    if _err(snap, "apps"):
        return []
    state_by_id = {str(r.get("id")): str(r.get("state"))
                   for r in (snap.get("instances") or {}).get("apps", [])}
    rows = []
    for app_id, app in sorted((snap.get("apps") or {}).items()):
        app = app or {}
        rows.append({
            "app": str(app_id),
            "name": str(app.get("name") or app_id),
            "category": str(app.get("category") or "-"),
            "via": str((app.get("launch") or {}).get("via") or "-"),
            "tagline": str((app.get("ui") or {}).get("tagline") or ""),
            "state": state_by_id.get(str(app_id), "-"),
        })
    return rows


DOCTOR_GLYPH = {"ok": "ok", "warn": "WARN", "fail": "FAIL", "skip": "skip"}


def doctor_rows(snap: dict) -> list[tuple[str, str, str]]:
    """(status, title, detail) per check; accepts CheckResult objects or dicts."""
    err = _err(snap, "doctor")
    if err:
        return [("fail", "doctor", err)]
    rows = []
    for r in (snap.get("doctor") or {}).get("results") or []:
        if isinstance(r, dict):
            status, title, detail = r.get("status", "?"), r.get("title", ""), r.get("detail", "")
        else:
            status = getattr(r, "status", "?")
            title, detail = getattr(r, "title", ""), getattr(r, "detail", "")
        rows.append((str(status), str(title), str(detail)))
    return rows
