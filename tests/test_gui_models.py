# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""GUI model tests (M3a): snapshot -> display rows, no Qt required."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from gui import models  # noqa: E402


def _snap(**over) -> dict:
    base = {
        "version": "0.3.0-m3a",
        "profile": {
            "path": "/etc/grid-down/profiles/default.yaml",
            "id": "default",
            "callsign": "W1AW",
            "grid": "FN31pr",
        },
        "rig": {
            "argv": ["rigctld", "-T", "127.0.0.1", "-t", "4532", "-v", "-m", "1"],
            "notes": ["rig.model empty — hamlib Dummy backend (bench-safe)"],
            "endpoint": "127.0.0.1:4532",
            "model": "",
            "path": "",
            "baud": "",
        },
        "audio": {"present": ["grid_down_hf_out"], "missing": ["grid_down_hf_in"]},
        "instances": {
            "apps": [
                {"id": "hamlib", "state": "running", "pid": 42},
                {"id": "direwolf", "state": "stale", "pid": 7},
            ],
            "bundles": {},
        },
        "doctor": {
            "results": [],
            "counts": {"ok": 8, "warn": 5, "fail": 0, "skip": 4},
            "all_ok": True,
        },
        "errors": {},
    }
    base.update(over)
    return base


def test_header_rows_render_unset_placeholders():
    rows = dict(models.header_rows(_snap()))
    assert rows["Callsign"] == "W1AW" and rows["Station"] == "default"
    assert rows["Profile"] == "/etc/grid-down/profiles/default.yaml"
    rows = dict(models.header_rows(_snap(profile={})))
    assert rows["Callsign"] == "(unset)" and rows["Version"] == "0.3.0-m3a"


def test_rig_lines_join_argv_and_notes():
    lines = models.rig_lines(_snap())
    assert lines[0].startswith("$ rigctld -T 127.0.0.1")
    joined = "\n".join(lines)
    assert "endpoint: 127.0.0.1:4532" in joined
    assert "(unset → hamlib Dummy)" in joined
    assert any(line.startswith("note: rig.model empty") for line in lines)


def test_audio_rows_present_missing_and_empty():
    rows = models.audio_rows(_snap())
    assert ("grid_down_hf_out", "present") in rows
    assert ("grid_down_hf_in", "MISSING") in rows
    assert models.audio_rows(_snap(audio={})) == [
        ("audio", "no owned endpoints known (run: gd audio plan)")
    ]


def test_instance_rows_running_and_stale_only():
    rows = models.instance_rows(_snap())
    assert ("hamlib", "running") in rows and ("direwolf", "stale") in rows
    assert models.instance_rows(_snap(instances={"apps": []})) == [("instances", "nothing running")]


def test_doctor_lines_summary_and_fail_tail():
    assert models.doctor_lines(_snap()) == ["doctor: 8 ok, 5 warn, 0 fail, 4 skip"]
    doc = {"results": [], "counts": {"ok": 1, "fail": 2}, "all_ok": False}
    assert models.doctor_lines(_snap(doctor=doc)) == [
        "doctor: 1 ok, 0 warn, 2 fail, 0 skip — fix FAIL items before field use"
    ]


def test_section_errors_render_as_unavailable():
    snap = _snap(errors={"audio": "FileNotFoundError: pactl"})
    assert models.audio_rows(snap) == [("audio", "unavailable — FileNotFoundError: pactl")]
    assert models.error_lines(snap) == ["audio: FileNotFoundError: pactl"]
    # other sections keep rendering normally
    assert models.rig_lines(snap) == models.rig_lines(_snap())


def test_models_tolerate_minimal_snapshot():
    rows = models.header_rows({})
    assert rows[0] == ("Version", "?")
    assert models.audio_rows({})[0][1].startswith("no owned endpoints")


def test_catalog_rows_merge_registry_and_state():
    apps = {
        "ardop": {
            "name": "ARDOP TNC",
            "category": "winlink",
            "launch": {"via": "managed-service"},
            "ui": {"tagline": "ARDOP modem"},
        },
        "zz-plain": {},
    }
    rows = models.catalog_rows(_snap(apps=apps))
    assert [r["app"] for r in rows] == ["ardop", "zz-plain"]
    assert rows[0]["name"] == "ARDOP TNC" and rows[0]["via"] == "managed-service"
    assert rows[0]["state"] == "-" and rows[0]["tagline"] == "ARDOP modem"
    assert rows[1]["name"] == "zz-plain" and rows[1]["category"] == "-"


def test_catalog_rows_overlay_running_state():
    snap = _snap(apps={"hamlib": {}}, instances={"apps": [{"id": "hamlib", "state": "running"}]})
    assert models.catalog_rows(snap)[0]["state"] == "running"


def test_catalog_rows_empty_on_section_error():
    assert models.catalog_rows(_snap(apps={}, errors={"apps": "boom"})) == []


def test_doctor_rows_duck_typed():
    res = [
        SimpleNamespace(status="ok", title="t", detail="d"),
        {"status": "fail", "title": "p", "detail": "q"},
    ]
    snap = _snap(doctor={"results": res, "counts": {}, "all_ok": False})
    assert models.doctor_rows(snap) == [("ok", "t", "d"), ("fail", "p", "q")]
    assert models.doctor_rows(_snap(errors={"doctor": "x"})) == [
        ("fail", "doctor", "unavailable — x")
    ]


def test_bundle_rows_merge_meta_and_state():
    snap = _snap(
        bundles={
            "winlink": {"description": "HF email", "members": ["hamlib", "ardop", "pat"]},
            "js8": {"description": "JS8", "members": ["hamlib", "js8call"]},
        },
        instances={"apps": [], "bundles": {"winlink": {"running": ["hamlib"], "total": 3}}},
    )
    rows = models.bundle_rows(snap)
    assert [r["name"] for r in rows] == ["js8", "winlink"]  # sorted
    assert rows[0]["running"] == 0 and rows[0]["total"] == 2
    assert rows[1]["running"] == 1 and rows[1]["total"] == 3
    assert rows[1]["description"] == "HF email"


def test_profile_text_renders_yaml_and_errors():
    text = models.profile_text(_snap(profile_raw={"profile": {"callsign": "W1AW"}}))
    assert "callsign: W1AW" in text
    assert models.profile_text(_snap(errors={"profile_raw": "io"})) == "unavailable — io"
