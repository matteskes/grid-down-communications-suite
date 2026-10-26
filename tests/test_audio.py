# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Audio module tests (M2): owned endpoints + preset binding (spec §6.6).

Hermetic: plan/resolve are pure; pactl interaction goes through fake runners.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core import audio  # noqa: E402


def test_endpoint_names_and_validation():
    assert audio.endpoint_name("hf", "in") == "grid_down_hf_in"
    assert audio.endpoint_name("packet", "out") == "grid_down_packet_out"
    assert audio.endpoint_name("voice", "in") == "grid_down_voice_in"
    with pytest.raises(audio.AudioError, match="role"):
        audio.endpoint_name("wifi", "in")
    with pytest.raises(audio.AudioError, match="direction"):
        audio.endpoint_name("hf", "sideways")


def test_resolve_preset_default_override_missing():
    prof = {"audio": {"presets": {"ardop": {"playback": "custom_sink"}}}}
    assert audio.resolve_preset("ardop", "playback", prof) == "custom_sink"
    # key absent from the profile's app entry -> documented default kicks in
    assert audio.resolve_preset("ardop", "capture", prof) == "grid_down_hf_out"
    # neither profile nor default -> precise profile-key error
    with pytest.raises(audio.AudioError, match=r"presets\.pat\.capture"):
        audio.resolve_preset("pat", "capture", prof)


def test_plan_shape_and_idempotent_states():
    steps = audio.plan({})
    assert [s["endpoint"] for s in steps] == [
        "grid_down_hf_in", "grid_down_hf_out",
        "grid_down_packet_in", "grid_down_packet_out",
        "grid_down_voice_in", "grid_down_voice_out"]
    assert all(s["state"] == "create" for s in steps)
    kinds = {s["endpoint"]: s["kind"] for s in steps}
    assert kinds["grid_down_hf_in"] == "sink" and kinds["grid_down_hf_out"] == "source"
    live = audio.plan({}, exists=lambda n: n == "grid_down_hf_in")
    row = next(s for s in live if s["endpoint"] == "grid_down_hf_in")
    assert row["state"] == "exists"


def test_argv_shapes():
    sink = audio.sink_argv("grid_down_hf_in", "hf")
    assert sink[:3] == ["pactl", "load-module", "module-null-sink"]
    assert "sink_name=grid_down_hf_in" in sink
    src = audio.source_argv("grid_down_hf_out", "hf")
    assert src[2] == "module-null-source" and "source_name=grid_down_hf_out" in src


def _fake_runner(listings: dict[str, str]):
    calls: list[list[str]] = []

    def run(argv):
        calls.append(list(argv))
        if argv[1] == "list":
            return listings.get(argv[3], "")
        return "42"  # load/unload module index

    run.calls = calls
    return run


def test_up_creates_missing_and_skips_existing():
    listings = {"sinks": "0\tgrid_down_hf_in\tstate=IDLE\n",
                "sources": ""}
    run = _fake_runner(listings)
    out = audio.up({}, runner=run)
    assert out["skipped"] == ["grid_down_hf_in"]
    created = [c["endpoint"] for c in out["created"]]
    assert "grid_down_hf_in" not in created and len(created) == 5
    assert any("load-module" in c and "module-null-source" in c
               for c in run.calls if len(c) > 2)


def test_down_unloads_only_owned_modules():
    listings = {"modules": "\n".join([
        "5\tmodule-null-sink\tsink_name=grid_down_hf_in node.name=grid_down_hf_in",
        "6\tmodule-null-sink\tsink_name=user_desktop_audio",   # not ours
        "7\tmodule-null-source\tsource_name=grid_down_hf_out",
    ])}
    run = _fake_runner(listings)
    out = audio.down({}, runner=run)
    assert sorted(out["unloaded"]) == ["5", "7"]
    unloads = [c for c in run.calls if c[1:2] == ["unload-module"]]
    assert all(c[2] in ("5", "7") for c in unloads)


def test_status_reports_present_missing():
    listings = {"sinks": "0\tgrid_down_hf_in\tstate=IDLE\n",
                "sources": "1\tgrid_down_hf_out\tstate=IDLE\n"}
    st = audio.status(runner=_fake_runner(listings))
    assert st["present"] == ["grid_down_hf_in", "grid_down_hf_out"]
    assert st["missing"] == ["grid_down_packet_in", "grid_down_packet_out",
                             "grid_down_voice_in", "grid_down_voice_out"]


def test_runner_without_pactl_raises(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(audio.AudioError, match="pactl not found"):
        audio.status()