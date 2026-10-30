# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Navit config adapter (``navit-xml``): seed-then-patch ``~/.navit/navit.xml``.

Navit reads ``~/.navit/navit.xml`` when it exists and falls back to the
system default (``/usr/share/navit/navit.xml``) — the pinned kit build
keeps that layout. The adapter seeds the user overlay from the system
default on first apply (leaving the system file pristine), then patches
only recipe-declared ``{xpath, attribute, value}`` rows with ElementTree.

Reformat caveat (stated, like base.py's comment round-tripping): serializing
the tree rewrites the whole file once, at seed time — accepted because the
user overlay is suite-created from that point on; the system default is
never touched. Rows' xpaths must be relative (``.//vehicle[@name='…']``);
a row whose element is absent refuses instead of creating structure.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .base import ConfigError
from .native import NativeConfigAdapter, unified_diff


class _NavitDoc:
    """One navit overlay: seed text fallback, xpath patch, serialize."""

    encoding = "utf-8"

    def __init__(self, path: Path, seed_text: str = ""):
        self.path = Path(path)
        self.exists = self.path.is_file()
        self._raw = self.path.read_text(encoding=self.encoding) if self.exists else seed_text
        self.root = None
        if self._raw.strip():
            try:
                self.root = ET.fromstring(self._raw)
            except ET.ParseError as exc:
                raise ConfigError(
                    f"{self.path}: not valid XML ({exc}) — fix or remove it first"
                ) from exc

    def declaration(self) -> str:
        """The raw's XML declaration line when present, else the default one."""
        head = self._raw.lstrip()
        if head.startswith("<?xml") and "?>" in head[:200]:
            return head[: head.index("?>") + 2] + "\n"
        return '<?xml version="1.0"?>\n'

    def dumps(self) -> str:
        if self.root is None:
            return self._raw
        body = ET.tostring(self.root, encoding="unicode")
        return self.declaration() + body + ("\n" if not body.endswith("\n") else "")

    def diff_against(self, proposed: str) -> str:
        return unified_diff(self.path, self._raw if self.exists else "", proposed)


class NavitXmlAdapter(NativeConfigAdapter):
    """One XML overlay, ``{xpath, attribute, value}`` ``config.set`` rows."""

    format = "xml"
    install_dir = "navit"
    install_marker = "navit"

    def __init__(self, app: dict, *, root=None, **kwargs):
        super().__init__(app, **kwargs)
        self._root = Path(root) if root else Path("/")

    @property
    def system_seed(self) -> Path:
        return self._root / "usr" / "share" / "navit" / "navit.xml"

    def native_path(self) -> Path:
        return self._home / ".navit" / "navit.xml"

    def read(self) -> dict:
        self._require_installed()
        path = self.native_path()
        exists = path.is_file()
        values: list[dict] = []
        if exists:
            doc = _NavitDoc(path)
            for row in self._rows():
                xpath, attr = self._row_target(row)
                hit = doc.root.find(xpath) if doc.root is not None else None
                values.append(
                    {
                        "xpath": xpath,
                        "attribute": attr,
                        "value": hit.get(attr) if hit is not None else "(absent)",
                    }
                )
        out = {
            "app": self.app_id,
            "path": str(path),
            "exists": exists,
            "format": self.format,
            "values": values,
            "registration": "n/a (navit.xml carries no credentials)",
        }
        if not exists:
            out["note"] = (
                "not created yet — seeded from the system default on first apply "
                f"(source: {self.system_seed})"
            )
        return out

    def _row_target(self, row: dict) -> tuple[str, str]:
        xpath = str(row.get("xpath") or "").strip()
        attr = str(row.get("attribute") or "").strip()
        if not xpath or not attr:
            raise ConfigError(
                f"{self.app_id}: every config.set row needs a non-empty xpath and "
                f"attribute (got {row!r})"
            )
        if not xpath.startswith("."):
            raise ConfigError(
                f"{self.app_id}: navit xpath rows must be relative (start with '.') "
                f"for ElementTree — got {xpath!r}"
            )
        return xpath, attr

    def apply(self, profile: dict, dry_run: bool = False) -> dict:
        rows = self._rows()
        self._require_installed()
        recipe_path = self.recipes_dir / f"{self.app_id}.yaml"
        station = profile.get("profile") or {}
        seed_text = ""
        if not self.native_path().is_file():
            if not self.system_seed.is_file():
                raise ConfigError(
                    f"{self.app_id}: no user navit.xml yet and no system default to "
                    f"seed from ({self.system_seed} is missing) — install navit's "
                    "data files first"
                )
            seed_text = self.system_seed.read_text(encoding="utf-8")
        doc = _NavitDoc(self.native_path(), seed_text=seed_text)
        planned = []
        for row in rows:
            xpath, attr = self._row_target(row)
            hits = doc.root.findall(xpath)
            if not hits:
                raise ConfigError(
                    f"{self.app_id}: config.set row matches no element — {xpath!r} "
                    f"(recipe: {recipe_path}); navit.xml structure differs from the "
                    "pinned default — pin the xpath at the E2E"
                )
            for el in hits:
                el.set(attr, self._expand(row.get("value"), station, row))
            planned.append(f"{xpath}@{attr}")
        return self._commit(doc, planned, dry_run)
