# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""VARA family config adapter (``vara-ini``): VARA.ini beside the installed exe.

The five VARA modem programs (HF, FM, SAT, Chat, Terminal) each keep their
settings in an INI next to their own executable inside the shared wine
prefix — VARA.ini beside VARA.exe, "VARA FM.ini" beside "VARA FM.exe", and
so on. The file's location is resolved through the wine hub's install probe
(``core/winehub``) rather than pinned, because upstream install directories
drift across versions; the INI name derives from the probed exe's stem
(recipe ``wine.ini:`` overrides when upstream ever changes it).

``read()`` masks any registration-shaped key's value (G6: license keys are
operator-applied in VARA's own dialog — the suite surfaces whether they are
present, never their contents). ``apply()`` writes only the keys the recipe
declares in ``wine.ini_set:`` (§6.2); the shipped recipes declare none until
the exact key names are pinned at the live E2E.
"""

from __future__ import annotations

from pathlib import Path

from .base import BaseAdapter, ConfigError, IniFile, expand_tokens


def winehub_recipe(app_id: str, recipes_dir):
    """The recipe's ``recipe.wine`` block via core/winehub (single source)."""
    try:  # package import (gd CLI, pytest)
        from .. import winehub
    except ImportError:  # pragma: no cover - sibling import inside control/core/
        import winehub  # type: ignore
    return winehub.load_wine_recipe(app_id, recipes_dir)


class VaraIniAdapter(BaseAdapter):
    """Read/apply one VARA program's exe-adjacent INI inside the prefix."""

    def __init__(self, app: dict, **kwargs):
        super().__init__(app, **kwargs)
        self._recipe = winehub_recipe(self.app_id, self.recipes_dir)

    # -- resolution -----------------------------------------------------------
    def _resolve(self) -> tuple[IniFile, dict]:
        probe = self.hub.probe(self.app_id)
        if not probe.get("installed"):
            raise ConfigError(
                f"{self.app_id}: not installed in the wine prefix yet — its config INI "
                f"lives beside the program exe, so install it first "
                f"(gd winehub install {self.app_id}; probe looked for: "
                f"{', '.join(probe.get('candidates') or [])})"
            )
        exe = Path(str(probe["exe_path"]))
        ini_name = str(self._recipe.get("ini") or "").strip() or f"{exe.stem}.ini"
        return IniFile(exe.with_name(ini_name)), probe

    # -- read (§6.2) ------------------------------------------------------------

    def read(self) -> dict:
        ini, probe = self._resolve()
        masked = False
        sections = {}
        for sec, keys in ini.sections().items():
            out = {}
            for k, v in keys.items():
                if "reg" in k.lower():
                    out[k] = "***"
                    masked = True
                else:
                    out[k] = v
            sections[sec] = out
        return {
            "app": self.app_id,
            "path": str(ini.path),
            "exists": ini.exists,
            "encoding": ini.encoding,
            "sections": sections,
            "registration": "present (values masked)" if masked else "absent",
            "exe_path": probe.get("exe_path"),
        }

    # -- apply (§6.2) -----------------------------------------------------------

    def apply(self, profile: dict, dry_run: bool = False) -> dict:
        table = self._recipe.get("ini_set") or []
        ini, _probe = self._resolve()
        recipe_path = self.recipes_dir / f"{self.app_id}.yaml"
        if not table:
            return {
                "app": self.app_id,
                "path": str(ini.path),
                "changed": False,
                "dry_run": dry_run,
                "diff": "",
                "note": (
                    f"{recipe_path} declares no wine.ini_set keys — nothing to apply. "
                    "Add rows of {section, key, value} there once the exact VARA.ini "
                    "key names are pinned (values may template {callsign} and {grid}); "
                    f"`gd config show {self.app_id}` prints the file's current contents."
                ),
            }
        station = profile.get("profile") or {}
        planned = []
        for row in table:
            sec = str(row.get("section") or "").strip()
            key = str(row.get("key") or "").strip()
            val = self._expand(row.get("value"), station, row)
            if not sec or not key:
                raise ConfigError(
                    f"{self.app_id}: every wine.ini_set row needs a non-empty "
                    f"section and key (recipe: {recipe_path}; got {row!r})"
                )
            planned.append((sec, key, val))
        proposed = IniFile(ini.path)
        for sec, key, val in planned:
            proposed.set(sec, key, val)
        new_text = proposed.dumps()
        diff = ini.diff_against(new_text)
        out = {
            "app": self.app_id,
            "path": str(ini.path),
            "changed": bool(diff),
            "dry_run": dry_run,
            "diff": diff,
        }
        if not diff:
            out["note"] = "already in sync — nothing to write"
            return out
        if not dry_run:
            if ini.exists:
                out["backup"] = self.backup(ini.path)
            self._write(ini.path, new_text, ini.encoding)
            out["wrote"] = True
            out["keys"] = [f"{sec}:{key}" for sec, key, _ in planned]
        return out

    # -- helpers ---------------------------------------------------------------

    def _expand(self, value, station: dict, row: dict) -> str:
        """INI values are strings; {callsign}/{grid} come from the station profile."""
        return expand_tokens(self.app_id, value, station, "wine.ini_set", row)
