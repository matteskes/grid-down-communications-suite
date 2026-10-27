# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""core/profile.py save path (rig / station identity / audio) — incl. the redirect."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import audio, profile


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Fresh HOME; resolved profile = the repo template via GRID_DOWN_PROFILE."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo_default = profile.default_repo_profile()
    monkeypatch.setenv("GRID_DOWN_PROFILE", str(repo_default))
    return tmp_path, repo_default


def _user_profile(home: Path) -> Path:
    return home / ".config" / "grid-down" / "profiles" / "default.yaml"


def test_save_rig_redirects_repo_template_to_user_profile(isolated_home):
    home, repo_default = isolated_home
    original = repo_default.read_text()
    out = profile.save_rig("3073", "/dev/ttyUSB1", "9600")
    target = _user_profile(home)
    assert Path(out["path"]) == target
    assert out["created"] is True
    assert repo_default.read_text() == original  # template never touched
    data = profile._yaml().safe_load(target.read_text())
    assert data["rig"] == {"model": "3073", "path": "/dev/ttyUSB1", "baud": "9600"}
    template = profile._yaml().safe_load(original)
    assert data["profile"] == template["profile"]  # rest of the profile copied, not lost


def test_save_rig_edits_user_profile_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    user = tmp_path / ".config" / "grid-down" / "profiles" / "mine.yaml"
    user.parent.mkdir(parents=True)
    user.write_text(
        'profile:\n  id: mine\n  callsign: W1AW\n  grid: ""\n'
        'rig:\n  model: ""\n  path: /dev/ttyUSB0\n  baud: "38400"\n'
    )
    monkeypatch.setenv("GRID_DOWN_PROFILE", str(user))
    out = profile.save_rig("3073", "/dev/ttyUSB2", None)
    assert Path(out["path"]) == user and out["created"] is False
    data = profile.load_profile()
    assert data["rig"] == {"model": "3073", "path": "/dev/ttyUSB2", "baud": "38400"}
    assert data["profile"]["callsign"] == "W1AW"
    assert not list(user.parent.glob("*.tmp"))  # atomic: tmp renamed away


def test_save_rig_rejects_non_numeric_model_and_baud(isolated_home):
    home, repo_default = isolated_home
    original = repo_default.read_text()
    with pytest.raises(profile.ProfileError):
        profile.save_rig("TS-480", "/dev/ttyUSB0", "38400")
    with pytest.raises(profile.ProfileError):
        profile.save_rig("3073", "/dev/ttyUSB0", "fast")
    assert not _user_profile(home).exists()
    assert repo_default.read_text() == original


def test_save_rig_allows_empty_model_dummy(isolated_home):
    home, _ = isolated_home
    out = profile.save_rig("", "/dev/ttyUSB0", "38400")
    assert out["rig"]["model"] == ""  # Dummy backend is a legitimate choice
    assert _user_profile(home).exists()


def test_user_profile_wins_resolution_after_save(isolated_home, monkeypatch):
    home, _ = isolated_home
    profile.save_rig("3073", "/dev/ttyUSB1", "9600")
    monkeypatch.delenv("GRID_DOWN_PROFILE")
    assert profile.profile_path() == _user_profile(home)
    assert profile.load_profile()["rig"]["model"] == "3073"


def test_save_station_redirects_repo_template_and_preserves_sections(isolated_home):
    home, repo_default = isolated_home
    original = repo_default.read_text()
    out = profile.save_station("w1aw", "FN31PR")
    target = _user_profile(home)
    assert Path(out["path"]) == target and out["created"] is True
    assert repo_default.read_text() == original  # template never touched
    data = profile._yaml().safe_load(target.read_text())
    assert data["profile"]["callsign"] == "W1AW"  # uppercased
    assert data["profile"]["grid"] == "FN31pr"  # canonical Maidenhead case
    template = profile._yaml().safe_load(original)
    assert data["rig"] == template["rig"]  # rest of the profile copied, not lost


def test_save_station_edits_user_profile_in_place_and_clears(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    user = tmp_path / ".config" / "grid-down" / "profiles" / "mine.yaml"
    user.parent.mkdir(parents=True)
    user.write_text(
        "profile:\n  id: mine\n  callsign: W1AW\n  grid: FN31pr\n" "rig:\n  path: /dev/ttyUSB0\n"
    )
    monkeypatch.setenv("GRID_DOWN_PROFILE", str(user))
    out = profile.save_station("N0CALL", "")  # grid explicitly cleared
    assert Path(out["path"]) == user and out["created"] is False
    data = profile.load_profile()
    assert data["profile"] == {"id": "mine", "callsign": "N0CALL", "grid": ""}
    assert data["rig"]["path"] == "/dev/ttyUSB0"  # rig untouched
    assert not list(user.parent.glob("*.tmp"))  # atomic: tmp renamed away


def test_save_station_rejects_bad_callsign_and_grid(isolated_home):
    home, repo_default = isolated_home
    original = repo_default.read_text()
    with pytest.raises(profile.ProfileError):
        profile.save_station("no", "FN31pr")  # too short
    with pytest.raises(profile.ProfileError):
        profile.save_station("W1AW/6", "FN31pr")  # portable suffix is not stored
    with pytest.raises(profile.ProfileError):
        profile.save_station("W1AW", "FN31p")  # odd length
    with pytest.raises(profile.ProfileError):
        profile.save_station("W1AW", "ZZ09pr")  # field letters run A-R
    assert not _user_profile(home).exists()  # nothing written on bad input
    assert repo_default.read_text() == original


def test_save_station_allows_clearing_both_fields(isolated_home):
    home, _ = isolated_home
    profile.save_station("W1AW", "FN31pr")
    out = profile.save_station("", "")  # unset both: legitimate (Doctor warns)
    assert out["profile"]["callsign"] == "" and out["profile"]["grid"] == ""
    data = profile._yaml().safe_load(_user_profile(home).read_text())
    assert data["profile"]["callsign"] == "" and data["profile"]["grid"] == ""


def test_save_audio_redirects_repo_template_and_preserves_sections(isolated_home):
    home, repo_default = isolated_home
    original = repo_default.read_text()
    out = profile.save_audio("", "alsa_input.usb-codex", "grid_down_packet_out.monitor", "")
    target = _user_profile(home)
    assert Path(out["path"]) == target and out["created"] is True
    assert repo_default.read_text() == original  # template never touched
    data = profile._yaml().safe_load(target.read_text())
    assert data["audio"]["backend"] == "pipewire"  # empty -> template default
    assert data["audio"]["rig_capture"] == "alsa_input.usb-codex"
    assert data["audio"]["presets"]["ardop"]["capture"] == "grid_down_packet_out.monitor"
    assert "playback" not in data["audio"]["presets"]["ardop"]  # cleared -> popped
    template = profile._yaml().safe_load(original)
    assert data["rig"] == template["rig"]  # rest of the profile copied, not lost


def test_save_audio_edits_user_profile_in_place_and_clears(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    user = tmp_path / ".config" / "grid-down" / "profiles" / "mine.yaml"
    user.parent.mkdir(parents=True)
    user.write_text(
        "profile:\n  id: mine\n  callsign: W1AW\n"
        'audio:\n  backend: pipewire\n  rig_capture: ""\n'
        "  presets:\n    ardop:\n      capture: grid_down_hf_out.monitor\n"
        "      playback: grid_down_hf_in\n"
    )
    monkeypatch.setenv("GRID_DOWN_PROFILE", str(user))
    out = profile.save_audio("pipewire", "", "grid_down_packet_out.monitor", "")
    assert Path(out["path"]) == user and out["created"] is False
    data = profile.load_profile()
    assert data["profile"]["callsign"] == "W1AW"  # head untouched
    assert data["audio"]["rig_capture"] == ""  # explicitly cleared
    ard = data["audio"]["presets"]["ardop"]
    assert ard == {"capture": "grid_down_packet_out.monitor"}  # cleared playback popped
    assert not list(user.parent.glob("*.tmp"))  # atomic: tmp renamed away


def test_save_audio_rejects_bad_backend_and_names(isolated_home):
    home, repo_default = isolated_home
    original = repo_default.read_text()
    with pytest.raises(profile.ProfileError):
        profile.save_audio("jack", "", "", "")  # only pipewire is implemented
    with pytest.raises(profile.ProfileError):
        profile.save_audio("pipewire", "alsa input", "", "")  # no spaces
    with pytest.raises(profile.ProfileError):
        profile.save_audio("pipewire", 'has"quote', "", "")  # no quotes
    with pytest.raises(profile.ProfileError):
        profile.save_audio("pipewire", "", "grid_down_hf_out;rm", "")  # no shell metachars
    assert not _user_profile(home).exists()  # nothing written on bad input
    assert repo_default.read_text() == original


def test_save_audio_cleared_presets_fall_back_to_defaults(isolated_home):
    home, _ = isolated_home
    profile.save_audio("pipewire", "alsa_input.usb-codex", "", "")
    # load the user profile directly: GRID_DOWN_PROFILE still resolves to the
    # repo template in this fixture (same pattern as the save_rig tests)
    data = profile._yaml().safe_load(_user_profile(home).read_text())
    assert data["audio"]["presets"]["ardop"] == {}  # both keys popped
    # resolve_preset's falsy chain falls back to DEFAULT_PRESETS (core/audio.py)
    assert audio.resolve_preset("ardop", "capture", data) == "grid_down_hf_out.monitor"
    assert audio.resolve_preset("ardop", "playback", data) == "grid_down_hf_in"
