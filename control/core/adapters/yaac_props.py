# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""YAAC config adapter (``yaac-prefs``): ``~/.yaac/yaac.properties``.

YAAC (Java) keeps its preferences in a standard ``java.util.Properties``
file under ``~/.yaac/`` — created on first run; key names are dynamic
(per-port ``portop``/``SerialPort:…`` rows), so the shipped recipe declares
``config.set: []`` until the live E2E pins a row. The patcher
(:class:`native.PropsDoc`) replaces a key's line in place and appends new
keys, keeping YAAC's comments and ordering byte-for-byte — §6.2's
conservative-write rule applied to a format that reorders easily.
"""

from __future__ import annotations

from pathlib import Path

from .base import ConfigError
from .native import NativeConfigAdapter, PropsDoc, is_secret_key


class YaacPrefsAdapter(NativeConfigAdapter):
    """One Java properties file, ``{key, value}`` ``config.set`` rows."""

    format = "properties"
    install_dir = "yaac"
    install_marker = "YAAC.jar"  # recipe tarball dest: /opt/grid-down/yaac

    def native_path(self) -> Path:
        return self._home / ".yaac" / "yaac.properties"

    def read(self) -> dict:
        self._require_installed()
        props = PropsDoc(self.native_path())
        masked = False
        values = {}
        for k, v in props.properties().items():
            if is_secret_key(k):
                values[k] = "***"
                masked = True
            else:
                values[k] = v
        return {
            "app": self.app_id,
            "path": str(props.path),
            "exists": props.exists,
            "format": self.format,
            "values": values,
            "registration": "present (values masked)" if masked else "absent",
        }

    def apply(self, profile: dict, dry_run: bool = False) -> dict:
        rows = self._rows()
        self._require_installed()
        recipe_path = self.recipes_dir / f"{self.app_id}.yaml"
        station = profile.get("profile") or {}
        doc = PropsDoc(self.native_path())
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
