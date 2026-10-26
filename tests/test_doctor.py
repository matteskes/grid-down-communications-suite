# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Doctor check tests (M1). Run: python3 -m pytest tests/test_doctor.py -q"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core import doctor  # noqa: E402  (after sys.path insert)


def _dead_pid() -> int:
    """A pid we know is dead: spawn, wait, return (child reaped -> not running)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_baseline_report_has_no_failures(tmp_path):
    results = doctor.run_checks(state_dir=tmp_path, profile_data={})
    assert results, "expected at least one check"
    assert set(r.status for r in results) <= {"ok", "warn", "fail", "skip"}
    assert doctor.all_ok(results), [r for r in results if r.status == "fail"]


def test_state_hygiene_flags_stale_instance(tmp_path):
    (tmp_path / "supervisor.json").write_text(json.dumps({
        "version": 1,
        "instances": {"ghost": {"pid": _dead_pid(), "pgid": 1, "via": "desktop-app"}},
    }))
    results = doctor.run_checks(state_dir=tmp_path, profile_data=None)
    state = next(r for r in results if r.id == "state")
    assert state.status == "warn"
    assert "ghost" in state.detail and "gd stop" in state.detail


def test_port_occupied_reports_warn(tmp_path):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        results = doctor.run_checks(state_dir=tmp_path, profile_data=None,
                                    ports={"testsvc": port})
    finally:
        blocker.close()
    check = next(r for r in results if r.id == f"port-{port}")
    assert check.status == "warn"
    assert "unknown listener" in check.detail


def test_port_free_reports_ok(tmp_path):
    results = doctor.run_checks(state_dir=tmp_path, profile_data=None,
                                ports={"testsvc": 59999})
    check = next(r for r in results if r.id == "port-59999")
    assert check.status == "ok" and check.detail == "free"


def test_text_and_json_rendering(tmp_path):
    profile_data = {
        "profile": {"id": "d", "callsign": "W1AW", "grid": "FN31"},
        "rig": {"path": "/dev/ttyUSB0"},
        "internet": {"present": False},
    }
    results = doctor.run_checks(state_dir=tmp_path, profile_data=profile_data)
    text = doctor.format_text(results)
    assert "doctor:" in text and "air-gap master switch" in text
    payload = json.loads(doctor.to_json(results))
    assert payload["ok"] is True
    assert payload["checks"][0]["id"] == "platform"
    assert all(set(c) == {"id", "title", "status", "detail"} for c in payload["checks"])