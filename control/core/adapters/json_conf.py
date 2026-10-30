# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""JSON config adapters (§6.2, M7): pat, chattervox, sdrpp, gridtracker.

Each app keeps its settings in one JSON file; the path is pinned from the
app's own source/artifact shipped in the kit (evidence per class below).
Rows are dotted key paths (``mycall``, ``httpd.listen``) — intermediate
objects are created, existing keys keep their position, and the file is
rewritten two-space-indented with its key order intact.
"""

from __future__ import annotations

from pathlib import Path

from .base import ConfigError
from .native import JsonDoc, NativeConfigAdapter, mask_tree, xdg_config_dir


class JsonConfAdapter(NativeConfigAdapter):
    """One JSON config file, dotted-key ``config.set`` rows."""

    format = "json"

    def native_path(self) -> Path:
        raise NotImplementedError

    def read(self) -> dict:
        self._require_installed()
        path = self.native_path()
        values, masked_any = {}, False
        exists = path.is_file()
        if exists:
            data = JsonDoc(path).data
            values, masked_any = mask_tree(data)
        return {
            "app": self.app_id,
            "path": str(path),
            "exists": exists,
            "format": self.format,
            "values": values,
            "registration": "present (values masked)" if masked_any else "absent",
        }

    def apply(self, profile: dict, dry_run: bool = False) -> dict:
        rows = self._rows()
        self._require_installed()
        recipe_path = self.recipes_dir / f"{self.app_id}.yaml"
        station = profile.get("profile") or {}
        doc = JsonDoc(self.native_path())
        planned = []
        for row in rows:
            key = str(row.get("key") or "").strip()
            if not key:
                raise ConfigError(
                    f"{self.app_id}: every config.set row needs a non-empty key "
                    f"(recipe: {recipe_path}; got {row!r})"
                )
            doc.set(key, self._expand(row.get("value"), station, row))
            planned.append(key)
        return self._commit(doc, planned, dry_run)


class PatConfAdapter(JsonConfAdapter):
    """pat (la5nta, v1.0.0 shipped in kit): ``$XDG_CONFIG_HOME/pat/config.json``.

    Upstream keeps its whole config in one JSON file under the XDG config
    dir — the file `pat configure` creates (Winlink password lives under a
    ``password``-shaped key, so it is masked, never printed).
    """

    install_dir = "pat"  # recipe tarball dest: /opt/grid-down/pat

    def native_path(self) -> Path:
        return xdg_config_dir(self._home) / "pat" / "config.json"


class ChattervoxConfAdapter(JsonConfAdapter):
    """ChatterVox: ``~/.chattervox/config.json``.

    Upstream's README pins ``~/.chattervox/config.json`` as the config path;
    its encryption-key settings are key-shaped and therefore masked.
    """

    install_dir = "chattervox"  # recipe tarball dest: /opt/grid-down/chattervox

    def native_path(self) -> Path:
        return self._home / ".chattervox" / "config.json"


class SdrppConfAdapter(JsonConfAdapter):
    """SDR++ (pinned f10fb98 build): ``<prefix>/config.json`` — kit prefix
    ``/opt/grid-down/sdrpp/dist``.

    core.cpp (pinned source): on desktop Linux ``root`` is the build's
    INSTALL_PREFIX and ``configManager.setPath(root + "/config.json")`` —
    and the default ``resourcesDirectory`` is ``INSTALL_PREFIX "/share/sdrpp"``,
    exactly the kit's ``dist/`` layout (``bin/sdrpp``, ``lib/sdrpp/plugins``,
    ``share/sdrpp/…``). So config.json sits beside those, in ``dist/``.
    The file is app-managed (SDR++ rewrites it on exit): the suite pins
    ``config.set: []`` until the E2E, and only ever writes keys the recipe
    declares.
    """

    install_dir = "sdrpp/dist"

    def native_path(self) -> Path:
        return self.install_root / "sdrpp" / "dist" / "config.json"


class GridtrackerConfAdapter(JsonConfAdapter):
    """GridTracker2 (Electron): ``~/.config/GridTracker2/app-settings.json``.

    Electron stores userData under ``~/.config/<app name>`` (not XDG-aware);
    the pinned tarball's binary is ``gridtracker2`` and its asar carries
    ``app-settings.json``/``config.json``. The dir name and file split get a
    one-look confirmation at the live E2E (first run creates them).
    """

    install_dir = "gridtracker"  # recipe tarball dest: /opt/grid-down/gridtracker

    def native_path(self) -> Path:
        return self._home / ".config" / "GridTracker2" / "app-settings.json"
