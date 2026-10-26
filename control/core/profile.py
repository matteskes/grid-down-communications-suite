# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Station profile model (Phase 0 stub). Schema: docs/SPECIFICATION.md §4.

Resolution order:
  1. $GRID_DOWN_PROFILE (path to a profile YAML)
  2. newest ~/.config/grid-down/profiles/*.yaml
  3. <repo>/profiles/default.yaml
"""
from __future__ import annotations

import os
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
