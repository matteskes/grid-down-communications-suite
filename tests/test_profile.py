# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""core/profile.py save path (rig editing) — incl. the repo-template redirect."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import profile


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
