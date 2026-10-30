# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""INI config adapters (§6.2, M7): qttermtcp, xygrib, paracon, artemis.

All four keep QSettings-style INIs; the VARA ``IniFile`` patcher does the
I/O (case-significant keys, section/key order kept, encoding detected and
reused). Paths are pinned from each app's own source/artifact shipped in
the kit (evidence per class below). Rows are ``{section, key, value}`` —
the exact shape VARA's ``wine.ini_set`` uses. Credential-shaped keys
(``is_secret_key``) are masked in ``read()``, the §6.2 rule VARA applies
to registration fields.
"""

from __future__ import annotations

from pathlib import Path

from .base import ConfigError, IniFile, is_secret_key
from .native import NativeConfigAdapter, load_recipe, xdg_config_dir, xdg_data_dir


class IniConfAdapter(NativeConfigAdapter):
    """One INI file, ``{section, key, value}`` ``config.set`` rows."""

    format = "ini"

    def native_path(self) -> Path:
        raise NotImplementedError

    def read(self) -> dict:
        self._require_installed()
        ini = IniFile(self.native_path())
        masked = False
        sections = {}
        for sec, keys in ini.sections().items():
            out = {}
            for k, v in keys.items():
                if is_secret_key(k):
                    out[k] = "***"
                    masked = True
                else:
                    out[k] = v
            sections[sec] = out
        return {
            "app": self.app_id,
            "path": str(ini.path),
            "exists": ini.exists,
            "format": self.format,
            "encoding": ini.encoding,
            "sections": sections,
            "registration": "present (values masked)" if masked else "absent",
        }

    def apply(self, profile: dict, dry_run: bool = False) -> dict:
        rows = self._rows()
        self._require_installed()
        recipe_path = self.recipes_dir / f"{self.app_id}.yaml"
        station = profile.get("profile") or {}
        ini = IniFile(self.native_path())  # original — the diff/backup side
        proposed = IniFile(self.native_path())  # mutated copy — VARA's pattern
        planned = []
        for row in rows:
            sec = str(row.get("section") or "").strip()
            key = str(row.get("key") or "").strip()
            if not sec or not key:
                raise ConfigError(
                    f"{self.app_id}: every config.set row needs a non-empty "
                    f"section and key (recipe: {recipe_path}; got {row!r})"
                )
            proposed.set(sec, key, self._expand(row.get("value"), station, row))
            planned.append(f"{sec}:{key}")
        return self._commit(ini, planned, dry_run, new_text=proposed.dumps())


class QtTermTCPConfAdapter(IniConfAdapter):
    """QtTermTCP (g8bpq): ``QtTermTCP.ini`` beside the installed exe —
    ``/opt/grid-down/qttermtcp/QtTermTCP.ini``.

    QtTermTCP.cpp ``GetConfPath()`` (pinned source, ~594-625): on Linux the
    QSettings file stays next to the program (only macOS relocates it), named
    after the executable — the exe-adjacent pattern, now on the Linux side.
    """

    install_dir = "qttermtcp"
    install_marker = "QtTermTCP"

    def native_path(self) -> Path:
        return self.install_root / "qttermtcp" / "QtTermTCP.ini"


class XyGribConfAdapter(IniConfAdapter):
    """XYGrib (openGribs): ``~/.config/openGribs/XyGrib.conf``.

    main.cpp (pinned source) constructs ``QSettings("openGribs", "XyGrib")``
    with IniFormat — QSettings puts that at
    ``$XDG_CONFIG_HOME/openGribs/XyGrib.conf`` (default ``~/.config/…``).
    """

    install_dir = "xygrib"
    install_marker = "xyGrib"

    def native_path(self) -> Path:
        return xdg_config_dir(self._home) / "openGribs" / "XyGrib.conf"


class ParaconConfAdapter(IniConfAdapter):
    """Paracon (mfncooper, v1.3.0): ``paracon.cfg`` in the launcher's working dir.

    config.py ``Config('paracon', …)`` (pinned source): the user config is
    found cwd-first (``./paracon.cfg``), then ``~/paracon.cfg``. The kit
    launches the zipapp with its working directory pinned to the kit dir
    (M8 owns the unit), so the managed file is ``<run_dir>/paracon.cfg``.
    ``.def`` is the shipped defaults resource — never written.
    """

    install_dir = "paracon"
    install_marker = "paracon"

    def __init__(self, app: dict, *, run_dir=None, **kwargs):
        super().__init__(app, **kwargs)
        self._run_dir = Path(run_dir) if run_dir else None

    @property
    def run_dir(self) -> Path:
        return self._run_dir or self.install_root / "paracon"

    def native_path(self) -> Path:
        return self.run_dir / "paracon.cfg"


class ArtemisPathsAdapter(IniConfAdapter):
    """Artemis (AresValley v4.2): prefs under ``~/.local/share/AresValley/Artemis``.

    utils/path_utils.py (pinned rpm): the Linux data dir is
    ``$XDG_DATA_HOME/AresValley/Artemis`` (default ``~/.local/share/…`` —
    the env var only matters for flatpak upstream, but the code honors it
    unconditionally, so we mirror that), and constants.py pins
    ``APPLICATION_NAME = 'Artemis'`` / ``ORGANIZATION_NAME = 'AresValley'``.
    The app creates the dir on first run; this adapter reads it and writes
    only recipe-declared INI rows (file defaults to ``qtquickcontrols2.conf``,
    the recipe's ``config.file:`` overrides).
    """

    install_dir = "artemis"

    @property
    def prefs_dir(self) -> Path:
        return xdg_data_dir(self._home) / "AresValley" / "Artemis"

    def _file_name(self) -> str:
        recipe, _path = load_recipe(self.app_id, self.recipes_dir)
        return str((recipe.get("config") or {}).get("file") or "qtquickcontrols2.conf")

    def native_path(self) -> Path:
        return self.prefs_dir / self._file_name()

    def read(self) -> dict:
        info = super().read()
        info["prefs_dir"] = str(self.prefs_dir)
        return info
