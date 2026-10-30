# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Shared machinery for the native (non-wine) config adapters (§6.2, M7).

VARA's config lives inside the wine prefix and is probed through the wine
hub; the twelve native apps own ordinary Linux-side files instead — JSON
(pat, chattervox, sdrpp, gridtracker), QSettings INIs (qttermtcp, xygrib,
paracon, artemis), Java properties (yaac), a YAML drop-in (meshtasticd),
an XML overlay (osm-navit) — or, for ardopcf, no config file at all:
everything is command-line flags, so its adapter manages the launch-args
file the managed service consumes (M8 wires the unit).

Where the §6.1 registry left ``native_path: TBD``, each adapter now pins
the path from that app's own source/artifact shipped in the kit (the
evidence lives in each adapter's docstring). Which keys an adapter may
write stays recipe-declared, generalizing the VARA ``wine.ini_set``
precedent to a top-level ``config:`` block::

    recipe:
      config:
        set:                              # structured formats: dotted keys
          - {key: mycall, value: "{callsign}"}
        # INI-family apps use {section, key, value} rows, like VARA
        # osm-navit uses {xpath, attribute, value} rows
        # ardop manages config.args (list of launch arguments) instead

Masking generalizes VARA's registration rule: any value whose KEY contains
reg/password/passwd/key/token/secret/passcode (case-insensitive) is
reported as ``***`` (G6: license keys are operator-applied in-app).
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

import yaml

try:  # package import (gd CLI, pytest)
    from .. import winehub
except ImportError:  # pragma: no cover - sibling import inside control/core/
    import winehub  # type: ignore

from .base import BaseAdapter, ConfigError, expand_tokens, is_secret_key


def load_recipe(app_id: str, recipes_dir) -> tuple[dict, Path]:
    """The full ``recipe:`` block, ``{}`` when absent/unreadable.

    Same degrade-don't-raise contract as ``winehub.load_wine_recipe``:
    status and config never break on a box because a recipe is missing.
    """
    d = Path(recipes_dir) if recipes_dir else winehub.RECIPES_DIR
    path = d / f"{app_id}.yaml"
    if not path.is_file():
        return {}, path
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}, path
    return data.get("recipe") or {}, path


def config_rows(app_id: str, recipes_dir) -> tuple[list, Path]:
    """The recipe's ``config.set:`` rows — what the adapter may write (§6.2)."""
    recipe, path = load_recipe(app_id, recipes_dir)
    rows = (recipe.get("config") or {}).get("set") or []
    if not isinstance(rows, list):
        raise ConfigError(f"{app_id}: recipe {path} config.set must be a list of rows")
    return rows, path


def mask_tree(obj):
    """Deep-copy ``obj`` with credential-shaped keys' values replaced by ``***``.

    Returns ``(masked, any_masked)``. Dict insertion order and list order are
    kept (``json.load`` already preserves file order).
    """
    if isinstance(obj, dict):
        out, masked = {}, False
        for k, v in obj.items():
            if is_secret_key(k):
                out[k] = "***"
                masked = True
            else:
                mv, m = mask_tree(v)
                out[k] = mv
                masked = masked or m
        return out, masked
    if isinstance(obj, list):
        out, masked = [], False
        for v in obj:
            mv, m = mask_tree(v)
            out.append(mv)
            masked = masked or m
        return out, masked
    return obj, False


def xdg_config_dir(home: Path) -> Path:
    """``$XDG_CONFIG_HOME`` or ``~/.config`` (the XDG base-dir default)."""
    env = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(env).expanduser() if env else Path(home) / ".config"


def xdg_data_dir(home: Path) -> Path:
    """``$XDG_DATA_HOME`` or ``~/.local/share`` (the XDG base-dir default)."""
    env = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(env).expanduser() if env else Path(home) / ".local" / "share"


def unified_diff(path: Path, current: str, proposed: str) -> str:
    """The §6.2 unified diff (same labels as the VARA IniFile's)."""
    return "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"{path} (current)",
            tofile=f"{path} (proposed)",
        )
    )


def set_dotted(doc: dict, dotted: str, value) -> None:
    """Set ``a.b.c`` in nested dicts, creating intermediates (JSON/YAML rows)."""
    parts = [p for p in str(dotted).split(".") if p]
    if not parts:
        raise ConfigError(f"empty key path (row: {dotted!r})")
    node = doc
    for p in parts[:-1]:
        nxt = node.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            node[p] = nxt
        node = nxt
    node[parts[-1]] = value


class JsonDoc:
    """One JSON config file: ordered load, dotted set, diff-able dump (§6.2)."""

    encoding = "utf-8"

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.exists = self.path.is_file()
        self._raw = ""
        self.data: dict = {}
        if self.exists:
            self._raw = self.path.read_text(encoding=self.encoding)
            if self._raw.strip():
                try:
                    loaded = json.loads(self._raw)
                except json.JSONDecodeError as exc:
                    raise ConfigError(
                        f"{self.path}: not valid JSON ({exc}) — fix or remove it first"
                    ) from exc
                if not isinstance(loaded, dict):
                    raise ConfigError(f"{self.path}: top level is not a JSON object")
                self.data = loaded

    def set(self, dotted: str, value) -> None:
        set_dotted(self.data, dotted, value)

    def current_text(self) -> str:
        return self._raw if self.exists else ""

    def dumps(self) -> str:
        """Two-space indent, key order kept, trailing newline (grep-friendly)."""
        return json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"

    def diff_against(self, proposed: str) -> str:
        return unified_diff(self.path, self.current_text(), proposed)


class PropsDoc:
    """Java ``.properties`` file patched line-by-line (comments/order kept).

    YAAC writes simple ``key=value`` lines; the patcher replaces a key's
    first defining line in place and appends unknown keys at the end, so
    everything else — comments, blank lines, ordering, case — survives
    byte-for-byte. Continuation lines (a value ending in a backslash) are
    parsed for display, but a key whose definition spans them is refused on
    write (safe limitation: YAAC's own settings are simple one-line values).
    """

    encoding = "utf-8"

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.exists = self.path.is_file()
        self._raw = self.path.read_text(encoding=self.encoding) if self.exists else ""
        self.lines = self._raw.splitlines()
        self._edits: dict[str, tuple[int, str]] = {}
        self._appends: list[tuple[str, str]] = []

    @staticmethod
    def _split(line: str):
        """``(key, value)`` for a simple properties line, else None.

        A single trailing backslash (the continuation marker) is stripped
        from the displayed value; a key whose definition really spans lines
        is refused on write (see ``set``).
        """
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            return None
        for sep in ("=", ":"):
            idx = stripped.find(sep)
            if idx > 0:
                return PropsDoc._pair(stripped[:idx], stripped[idx + 1 :])
        parts = stripped.split(None, 1)  # whitespace-separated form
        return PropsDoc._pair(*parts) if len(parts) == 2 else None

    @staticmethod
    def _pair(key: str, value: str) -> tuple[str, str]:
        value = value.strip()
        return key.strip(), (value[:-1] if value.endswith("\\") else value)

    def properties(self) -> dict:
        """Simple ``{key: value}`` pairs in file order (continuations skipped)."""
        out, cont = {}, False
        for line in self.lines:
            if cont:  # continuation of the previous value: skip (limitation)
                cont = line.rstrip().endswith("\\")
                continue
            kv = self._split(line)
            if kv:
                out[kv[0]] = kv[1]
                cont = line.rstrip().endswith("\\")
        return out

    def set(self, key: str, value: str) -> None:
        text = str(value)
        for i, line in enumerate(self.lines):
            kv = self._split(line)
            if kv and kv[0] == key:
                if line.rstrip().endswith("\\"):
                    raise ConfigError(
                        f"{self.path}: key {key!r} uses continuation lines — patch it by hand"
                    )
                if kv[1] != text:  # already equal: no edit, idempotent apply
                    self._edits[key] = (i, text)
                return
        self._appends.append((key, text))

    def current_text(self) -> str:
        return self._raw if self.exists else ""

    def dumps(self) -> str:
        if not self._edits and not self._appends:
            return self._raw
        by_index = {i: f"{key}={v}" for key, (i, v) in self._edits.items()}
        out = [by_index.get(i, line) for i, line in enumerate(self.lines)]
        out.extend(f"{key}={v}" for key, v in self._appends)
        return "\n".join(out) + "\n"

    def diff_against(self, proposed: str) -> str:
        return unified_diff(self.path, self.current_text(), proposed)


class YamlDoc:
    """Suite-owned YAML drop-in: dotted set, canonical dump, diff-able.

    Unlike the INI/props patchers this document is authored by the suite
    (meshtasticd's ``config.d`` override — the deb's documented mechanism),
    so it is written back in PyYAML's canonical form rather than preserving
    upstream bytes.
    """

    encoding = "utf-8"

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.exists = self.path.is_file()
        self._raw = self.path.read_text(encoding=self.encoding) if self.exists else ""
        self.data: dict = {}
        if self._raw.strip():
            try:
                loaded = yaml.safe_load(self._raw)
            except yaml.YAMLError as exc:
                raise ConfigError(
                    f"{self.path}: not valid YAML ({exc}) — fix or remove it first"
                ) from exc
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ConfigError(f"{self.path}: top level is not a YAML mapping")
            self.data = loaded

    def set(self, dotted: str, value) -> None:
        set_dotted(self.data, dotted, value)

    def current_text(self) -> str:
        return self._raw if self.exists else ""

    def dumps(self) -> str:
        return yaml.safe_dump(self.data, sort_keys=False, default_flow_style=False)

    def diff_against(self, proposed: str) -> str:
        return unified_diff(self.path, self.current_text(), proposed)


class NativeConfigAdapter(BaseAdapter):
    """§6.2 plumbing shared by the twelve native (non-wine) apps (M7).

    A subclass pins three things: ``install_dir``/``install_marker`` (the
    on-disk proof the app is installed — refusal names it, the same operator
    contract as VARA's wine-probe refusal), ``native_path()`` (the config
    this adapter manages, pinned from the app's own source/artifact), and a
    ``read()``/``apply()`` over one of the documents above. ``install_root=``
    and ``home=`` (plus per-app extras like ``run_dir``/``root``) let tests —
    and any future non-/opt install — relocate everything.
    """

    format = "?"
    install_dir = ""  # subdir under install_root (/opt/grid-down)
    install_marker = ""  # file/dir under install_dir; "" = the dir itself

    def __init__(self, app: dict, *, install_root=None, home=None, **kwargs):
        super().__init__(app, **kwargs)
        self._install_root = (
            Path(install_root).expanduser() if install_root else Path("/opt/grid-down")
        )
        self._home = Path(home).expanduser() if home else Path.home()

    @property
    def install_root(self) -> Path:
        return self._install_root

    @property
    def home(self) -> Path:
        return self._home

    def _marker(self) -> Path:
        base = self._install_root / self.install_dir
        return base / self.install_marker if self.install_marker else base

    def _require_installed(self) -> None:
        marker = self._marker()
        if not marker.exists():
            raise ConfigError(
                f"{self.app_id}: not installed yet — its config is managed only once "
                f"the app is on disk (install marker: {marker}). Install the kit app "
                f"first, then re-run `gd config show {self.app_id}`."
            )

    def _rows(self) -> list:
        """``config.set`` rows, each validated to be a mapping (§6.2)."""
        rows, path = config_rows(self.app_id, self.recipes_dir)
        for row in rows:
            if not isinstance(row, dict):
                raise ConfigError(
                    f"{self.app_id}: every config.set row must be a mapping "
                    f"(recipe: {path}; got {row!r})"
                )
        return rows

    def _expand(self, value, station: dict, row) -> str:
        return expand_tokens(self.app_id, value, station, "config.set", row)

    def _commit(self, doc, planned: list[str], dry_run: bool, new_text: str | None = None) -> dict:
        """The shared apply tail: diff, backup, atomic write (VARA's shape).

        ``doc`` is the ORIGINAL document (its ``exists``/``encoding`` drive
        the backup); ``new_text`` is the proposed serialization — ``doc``
        itself when it keeps its original bytes (JsonDoc et al), or a
        separate mutated copy for IniFile, which serializes live state
        (same shape as VARA's ``ini.diff_against(new_text)``).
        """
        if new_text is None:
            new_text = doc.dumps()
        diff = doc.diff_against(new_text)
        out = {
            "app": self.app_id,
            "path": str(doc.path),
            "changed": bool(diff),
            "dry_run": dry_run,
            "diff": diff,
        }
        if not diff:
            out["note"] = "already in sync — nothing to write"
            return out
        if not dry_run:
            if doc.exists:
                out["backup"] = self.backup(doc.path)
            self._write(doc.path, new_text, doc.encoding)
            out["wrote"] = True
            out["keys"] = planned
        return out
