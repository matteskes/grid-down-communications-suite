# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""meshtasticd config adapter (``meshtasticd-yaml``): a suite-owned drop-in.

The meshtasticd deb ships ``/etc/meshtasticd/config.yaml`` as the upstream-
managed default and reads ``/etc/meshtasticd/config.d/*.yaml`` after it —
later/lexicographically-sorted drop-ins override (mechanism verified from
the pinned deb's own config.d). The suite therefore NEVER touches upstream
``config.yaml``: it manages exactly one drop-in,
``/etc/meshtasticd/config.d/99-grid-down.yaml``, so an upstream package
update can never collide with suite pins.

Rows are dotted keys (``Lora.Module``) rendered with PyYAML's canonical
form — this file is authored by the suite (unlike the INI/props patchers,
there are no upstream bytes to preserve). ``root=`` relocates ``/etc`` for
tests.
"""

from __future__ import annotations

from pathlib import Path

from .base import ConfigError
from .native import NativeConfigAdapter, YamlDoc, mask_tree


class MeshtasticdYamlAdapter(NativeConfigAdapter):
    """One YAML drop-in, dotted-key ``config.set`` rows."""

    format = "yaml"

    def __init__(self, app: dict, *, root=None, **kwargs):
        super().__init__(app, **kwargs)
        self._root = Path(root) if root else Path("/")

    @property
    def etc_dir(self) -> Path:
        return self._root / "etc" / "meshtasticd"

    def native_path(self) -> Path:
        return self.etc_dir / "config.d" / "99-grid-down.yaml"

    def _marker(self) -> Path:
        return self.etc_dir  # the deb's config dir is the install proof

    def read(self) -> dict:
        self._require_installed()
        doc = YamlDoc(self.native_path())
        values, masked_any = mask_tree(doc.data)
        return {
            "app": self.app_id,
            "path": str(doc.path),
            "exists": doc.exists,
            "format": self.format,
            "values": values,
            "registration": "present (values masked)" if masked_any else "absent",
            "note": "drop-in — wins over the upstream config.yaml (config.d)",
        }

    def apply(self, profile: dict, dry_run: bool = False) -> dict:
        rows = self._rows()
        self._require_installed()
        recipe_path = self.recipes_dir / f"{self.app_id}.yaml"
        station = profile.get("profile") or {}
        doc = YamlDoc(self.native_path())
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
