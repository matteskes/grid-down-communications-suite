# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""GUI model tests (M3a): snapshot -> display rows, no Qt required."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from gui import models  # noqa: E402


def _snap(**over) -> dict:
    base = {
        "version": "0.3.0-m3a",
        "profile": {"path": "/etc/grid-down/profiles/default.yaml", "id": "default",
                    "callsign": "W1AW", "grid": "FN31pr"},
        "rig": {"argv": ["rigctld", "-T", "127.0.0.1", "-t", "4532", "-v", "-m", "1"],
                "notes": ["rig.model empty — hamlib Dummy backend (bench-safe)"],
                "endpoint": "127.0.0.1:4532", "model": "", "path": "", "baud": ""},
        "audio": {"present": ["grid_down_hf_out"], "missing": ["grid_down_hf_in"]},
        "instances": {"apps": [{"id": "hamlib", "state": "running", "pid": 42},
                               {"id": "direwolf", "state": "stale", "pid": 7}],
                      "bundles": {}},
        "doctor": {"results": [], "counts": {"ok": 8, "warn": 5, "fail": 0, "skip": 4},
                   "all_ok": True},
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
        ("audio", "no owned endpoints known (run: gd audio plan)")]


def test_instance_rows_running_and_stale_only():
    rows = models.instance_rows(_snap())
    assert ("hamlib", "running") in rows and ("direwolf", "stale") in rows
    assert models.instance_rows(_snap(instances={"apps": []})) == [
        ("instances", "nothing running")]


def test_doctor_lines_summary_and_fail_tail():
    assert models.doctor_lines(_snap()) == ["doctor: 8 ok, 5 warn, 0 fail, 4 skip"]
    doc = {"results": [], "counts": {"ok": 1, "fail": 2}, "all_ok": False}
    assert models.doctor_lines(_snap(doctor=doc)) == [
        "doctor: 1 ok, 0 warn, 2 fail, 0 skip — fix FAIL items before field use"]


def test_section_errors_render_as_unavailable():
    snap = _snap(errors={"audio": "FileNotFoundError: pactl"})
    assert models.audio_rows(snap) == [
        ("audio", "unavailable — FileNotFoundError: pactl")]
    assert models.error_lines(snap) == ["audio: FileNotFoundError: pactl"]
    # other sections keep rendering normally
    assert models.rig_lines(snap) == models.rig_lines(_snap())


def test_models_tolerate_minimal_snapshot():
    rows = models.header_rows({})
    assert rows[0] == ("Version", "?")
    assert models.audio_rows({})[0][1].startswith("no owned endpoints")
