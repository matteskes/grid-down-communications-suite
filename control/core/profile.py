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


def save_rig(model: str, path: str, baud: str | None = None) -> dict:
    """Validate + persist the rig section (spec §4) into the active profile.

    Validation mirrors what supervisor.rig_argv/Doctor expect: model empty
    (hamlib Dummy, bench-safe) or a numeric hamlib id; baud numeric. Raises
    ProfileError and writes nothing on bad input.

    The shipped repo template (profiles/default.yaml) is never modified:
    when it is the resolved profile, a full copy with the new rig values is
    written to ~/.config/grid-down/profiles/<id>.yaml — which profile_path()
    prefers, so the edit wins on the next load. Other writes edit in place,
    atomically (tmp + rename). baud is only ever set, never cleared.

    Returns {"path": <written file>, "created": bool, "rig": <new rig dict>}.
    """
    model_s, path_s, baud_s = (
        str(model or "").strip(),
        str(path or "").strip(),
        str(baud or "").strip(),
    )
    if model_s and not model_s.isdigit():
        raise ProfileError(
            f"rig.model {model_s!r} is not a numeric hamlib id"
            " (pick one from `rigctl -l`; leave empty for the Dummy backend)"
        )
    if baud_s and not baud_s.isdigit():
        raise ProfileError(f"rig.baud {baud_s!r} is not numeric (e.g. 38400)")

    resolved = profile_path()
    target, created = resolved, False
    if resolved == default_repo_profile():
        head = load_profile(resolved).get("profile", {}) or {}
        stem = str(head.get("id") or "station").strip() or "station"
        target = Path.home() / ".config" / "grid-down" / "profiles" / f"{stem}.yaml"
        created = not target.exists()

    data = load_profile(resolved)  # base = resolved content: nothing is lost
    rig = data.setdefault("rig", {})
    rig["model"] = model_s
    rig["path"] = path_s
    if baud_s:
        rig["baud"] = baud_s
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(_yaml().safe_dump(data, sort_keys=False, default_flow_style=False))
    os.replace(tmp, target)
    return {"path": str(target), "created": created, "rig": dict(rig)}
