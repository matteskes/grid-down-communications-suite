# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""ARDOP config adapter (``ardop-cfg``): the launch-args file.

ardopcf (pflarue/ardop 1.0.4.1.3 — the kit's pinned source) has NO config
file: everything is command-line flags — the TCP control port, then the
ALSA capture and playback device names (the kit launches
``ardopc 8515 <capture> <playback>``). §6.1's ``native_path`` for ardop is
therefore a suite-owned file: ``<state>/app-args/ardop.args``, one argument
per line, which the managed service consumes (M8 wires the unit; the file
lives under the state dir so a read-only /opt never blocks it).

Rows come from the recipe's ``config.args:`` (a plain list of launch
arguments; values may template ``{callsign}``/``{grid}``). Port and audio
endpoints are station-specific (§6.4 audio presets) — pinned at the live
E2E, same discipline as the other apps' key names.
"""

from __future__ import annotations

from pathlib import Path

from .base import ConfigError, expand_tokens
from .native import NativeConfigAdapter, load_recipe, unified_diff


class ArdopArgsAdapter(NativeConfigAdapter):
    """The managed service's launch args, one per line."""

    format = "launch-args"
    install_dir = "ardop"
    install_marker = "ardopcf"

    def args_path(self) -> Path:
        return self.state_dir / "app-args" / "ardop.args"

    def _args_rows(self, recipe_path: Path) -> list:
        recipe, _ = load_recipe(self.app_id, self.recipes_dir)
        rows = (recipe.get("config") or {}).get("args") or []
        if not isinstance(rows, list):
            raise ConfigError(
                f"{self.app_id}: recipe {recipe_path} config.args must be a list of arguments"
            )
        return rows

    def read(self) -> dict:
        self._require_installed()
        path = self.args_path()
        exists = path.is_file()
        args = []
        if exists:
            args = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        return {
            "app": self.app_id,
            "path": str(path),
            "exists": exists,
            "format": self.format,
            "args": args,
            "registration": "n/a (ardopcf has no config file — launch args only)",
        }

    def apply(self, profile: dict, dry_run: bool = False) -> dict:
        recipe_path = self.recipes_dir / f"{self.app_id}.yaml"
        args_rows = self._args_rows(recipe_path)
        path = self.args_path()
        if not args_rows:
            return {
                "app": self.app_id,
                "path": str(path),
                "changed": False,
                "dry_run": dry_run,
                "diff": "",
                "note": (
                    f"{recipe_path} declares no config.args — nothing to apply. Add "
                    "launch arguments there (one per row; values may template "
                    "{callsign} and {grid}): ardopcf takes the TCP port and the ALSA "
                    "capture/playback devices on the command line — pin them at the "
                    "live E2E (station audio preset, §6.4)."
                ),
            }
        self._require_installed()
        station = profile.get("profile") or {}
        planned = [self._expand(arg, station, arg) for arg in args_rows]
        new_text = "".join(f"{arg}\n" for arg in planned)
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        diff = unified_diff(path, current, new_text)
        out = {
            "app": self.app_id,
            "path": str(path),
            "changed": bool(diff),
            "dry_run": dry_run,
            "diff": diff,
        }
        if not diff:
            out["note"] = "already in sync — nothing to write"
            return out
        if not dry_run:
            if path.is_file():
                out["backup"] = self.backup(path)
            self._write(path, new_text, "utf-8")
            out["wrote"] = True
            out["args"] = planned
        return out

    def _expand(self, value, station: dict, row) -> str:
        return expand_tokens(self.app_id, value, station, "config.args", row)
