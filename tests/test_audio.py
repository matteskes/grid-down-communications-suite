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
    assert audio.resolve_preset("ardop", "capture", prof) == "grid_down_hf_out.monitor"
    # neither profile nor default -> precise profile-key error
    with pytest.raises(audio.AudioError, match=r"presets\.pat\.capture"):
        audio.resolve_preset("pat", "capture", prof)


def test_plan_shape_and_idempotent_states():
    steps = audio.plan({})
    assert [s["endpoint"] for s in steps] == [
        "grid_down_hf_in",
        "grid_down_hf_out",
        "grid_down_packet_in",
        "grid_down_packet_out",
        "grid_down_voice_in",
        "grid_down_voice_out",
    ]
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
    # pipewire-pulse has no module-null-source: the 'out' endpoint is a null
    # sink and apps capture from its .monitor (P2 VM acceptance finding)
    src = audio.source_argv("grid_down_hf_out", "hf")
    assert src[2] == "module-null-sink" and "sink_name=grid_down_hf_out" in src
    assert audio.capture_name("grid_down_hf_out") == "grid_down_hf_out.monitor"


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
    listings = {"sinks": "0\tgrid_down_hf_in\tstate=IDLE\n", "sources": ""}
    run = _fake_runner(listings)
    out = audio.up({}, runner=run)
    assert out["skipped"] == ["grid_down_hf_in"]
    created = [c["endpoint"] for c in out["created"]]
    assert "grid_down_hf_in" not in created and len(created) == 5
    assert any("sink_name=grid_down_hf_out" in " ".join(c) for c in run.calls if len(c) > 2)


def test_down_unloads_only_owned_modules():
    listings = {
        "modules": "\n".join(
            [
                "5\tmodule-null-sink\tsink_name=grid_down_hf_in node.name=grid_down_hf_in",
                "6\tmodule-null-sink\tsink_name=user_desktop_audio",  # not ours
                "7\tmodule-null-sink\tsink_name=grid_down_hf_out node.name=grid_down_hf_out",
            ]
        )
    }
    run = _fake_runner(listings)
    out = audio.down({}, runner=run)
    assert sorted(out["unloaded"]) == ["5", "7"]
    unloads = [c for c in run.calls if c[1:2] == ["unload-module"]]
    assert all(c[2] in ("5", "7") for c in unloads)


def test_status_reports_present_missing():
    listings = {
        "sinks": "0\tgrid_down_hf_in\tstate=IDLE\n",
        # live capture node carries the .monitor suffix pipewire-pulse gives it
        "sources": "1\tgrid_down_hf_out.monitor\tstate=IDLE\n",
    }
    st = audio.status(runner=_fake_runner(listings))
    assert st["present"] == ["grid_down_hf_in", "grid_down_hf_out"]
    assert st["missing"] == [
        "grid_down_packet_in",
        "grid_down_packet_out",
        "grid_down_voice_in",
        "grid_down_voice_out",
    ]


def test_runner_without_pactl_raises(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(audio.AudioError, match="pactl not found"):
        audio.status()


# ------------------------------------------------------- P3b: rig loopbacks


def test_loopback_rows_default_is_pending_bench_state():
    rows = audio.loopback_rows({})
    assert [(r["role"], r["direction"]) for r in rows] == [("hf", "rx"), ("hf", "tx")]
    assert all(r["state"].startswith("pending") for r in rows)
    assert all(r["argv"] == [] for r in rows)  # nothing to run without the keys


def test_loopback_rows_planned_argv_and_roles():
    prof = {"audio": {"rig_capture": "alsa_input.rig", "rig_playback": "alsa_output.rig"}}
    rows = audio.loopback_rows(prof)
    rx = next(r for r in rows if r["direction"] == "rx")
    tx = next(r for r in rows if r["direction"] == "tx")
    assert rx["argv"] == [
        "pactl",
        "load-module",
        "module-loopback",
        "source=alsa_input.rig",
        "sink=grid_down_hf_out",
    ]
    # TX taps the owned 'in' endpoint's monitor (the .monitor convention)
    assert tx["argv"][3:] == ["source=grid_down_hf_in.monitor", "sink=alsa_output.rig"]
    assert all(r["state"] == "planned" for r in rows)
    both = {
        "audio": {"rig_capture": "cap", "rig_playback": "out", "loopback_roles": ["hf", "packet"]}
    }
    assert [(r["role"], r["direction"]) for r in audio.loopback_rows(both)] == [
        ("hf", "rx"),
        ("hf", "tx"),
        ("packet", "rx"),
        ("packet", "tx"),
    ]
    with pytest.raises(audio.AudioError, match="loopback_roles"):
        audio.loopback_rows({"audio": {"loopback_roles": ["wifi"]}})


def test_wire_loads_planned_rows_and_skips_rest():
    listings = {"modules": "9\tmodule-loopback\tsource=alsa_input.rig sink=grid_down_hf_out\n"}
    prof = {"audio": {"rig_capture": "alsa_input.rig"}}  # RX only: TX half stays unplanned
    out = audio.wire(prof, runner=_fake_runner(listings))
    assert out["wired"] == []
    assert out["skipped"] == ["hf.rx: already wired"]
    run2 = _fake_runner({"modules": ""})
    out2 = audio.wire(prof, runner=run2)
    assert [w["direction"] for w in out2["wired"]] == ["rx"]
    assert out2["wired"][0]["module"] == "42"
    assert [c for c in run2.calls if c[:2] == ["pactl", "load-module"]] == [
        [
            "pactl",
            "load-module",
            "module-loopback",
            "source=alsa_input.rig",
            "sink=grid_down_hf_out",
        ]
    ]
    # nothing configured: both halves report their pending profile key, no pactl runs
    run3 = _fake_runner({})
    assert audio.wire({}, runner=run3)["skipped"] == [
        "hf.rx: pending (set audio.rig_capture)",
        "hf.tx: pending (set audio.rig_playback)",
    ]
    assert run3.calls == []  # pending rows never reach pactl


def test_unwire_and_down_never_touch_a_user_loopback():
    owned_rx = "5\tmodule-loopback\tsource=alsa_input.rig sink=grid_down_hf_out"
    owned_tx = "6\tmodule-loopback\tsource=grid_down_hf_in.monitor sink=alsa_output.rig"
    user_lb = "7\tmodule-loopback\tsource=user_mic.monitor sink=user_speakers"
    user_sink = "8\tmodule-null-sink\tsink_name=user_desktop_audio"
    listings = {"modules": "\n".join([owned_rx, owned_tx, user_lb, user_sink])}
    run = _fake_runner(listings)
    assert sorted(audio.unwire(runner=run)["unloaded"]) == ["5", "6"]
    # down() owns the loopback half too: same two modules, user gear untouched
    assert sorted(audio.down({}, runner=_fake_runner(listings))["unloaded"]) == ["5", "6"]
    # status surfaces the wired loopbacks by their module args
    st = audio.status(runner=_fake_runner(listings))
    assert len(st["loopback"]) == 2
    assert all("grid_down_" in a for a in st["loopback"])
