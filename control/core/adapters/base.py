# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Shared plumbing for config adapters (§6.2): INI I/O, backup, atomic write.

Windows INIs (VARA.ini, VarAC.ini, …) keep key case significant (RegStation
vs regstation are different keys to GetPrivateProfileString), so the parser
runs with ``optionxform = str`` and section/key order is preserved on write.
Comments are the one casualty: Python's configparser drops them on rewrite —
acceptable for these files (the VARA family writes none), and stated here so
nobody expects comment round-tripping.

Encoding is detected per file: a UTF-16 BOM wins, then UTF-8, then cp1252
(what VB6's ``WritePrivateProfileString`` actually writes) — and the same
encoding is used to write back, so a cp1252 file stays cp1252.
"""

from __future__ import annotations

import configparser
import difflib
import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path

try:  # package import (gd CLI, pytest)
    from .. import winehub
except ImportError:  # pragma: no cover - sibling import inside control/core/
    import winehub  # type: ignore


class ConfigError(RuntimeError):
    """Refusal with operator-facing text (same contract as WinehubError)."""


# §6.2 value templating: {callsign}/{grid} from the station profile, shared by
# the VARA (wine.ini_set) and native (config.set / config.args) adapters.
_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")
_ALLOWED_TOKENS = ("callsign", "grid")

# §6.2 masking rule, generalized from VARA's registration check (M7): any
# value whose KEY contains one of these (case-insensitive) is reported as
# "***" — the suite surfaces that a credential is present, never its contents.
SECRET_KEY_PARTS = ("reg", "password", "passwd", "key", "token", "secret", "passcode")


def is_secret_key(name: str) -> bool:
    """True when a config key's *name* looks like a credential (§6.2)."""
    low = str(name).lower()
    return any(part in low for part in SECRET_KEY_PARTS)


def expand_tokens(app_id: str, value, station: dict, what: str, row) -> str:
    """Substitute ``{callsign}``/``{grid}`` from the station profile.

    ``what`` names the recipe block in refusals (``wine.ini_set`` for VARA,
    ``config.set``/``config.args`` for native apps). Same contract everywhere:
    unknown tokens and empty profile fields refuse with the exact fix.
    """
    text = "" if value is None else str(value)

    def sub(match: re.Match) -> str:
        token = match.group(1)
        if token not in _ALLOWED_TOKENS:
            allowed = ", ".join(f"{{{t}}}" for t in _ALLOWED_TOKENS)
            raise ConfigError(
                f"{app_id}: {what} value {text!r} uses unknown template "
                f"{{{token}}} — allowed: {allowed} (row: {row!r})"
            )
        got = str(station.get(token) or "").strip()
        if not got:
            raise ConfigError(
                f"{app_id}: {what} needs {{{token}}} but the station "
                f"profile's {token} is empty — set it first (spec §4, "
                "profiles/<id>.yaml, `gd profile show`)"
            )
        return got

    return _TOKEN_RE.sub(sub, text)


def _new_parser() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(
        interpolation=None,
        strict=False,
        delimiters=("=",),
        comment_prefixes=(";", "#"),
    )
    cp.optionxform = str  # case-sensitive INI keys (VARA: RegStation …)
    return cp


class IniFile:
    """One INI on disk: load, inspect, mutate in memory, diff, serialize."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.encoding = "utf-8"
        self._cp = _new_parser()
        raw = b""
        if self.path.is_file():
            raw = self.path.read_bytes()
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            self.encoding = "utf-16"
        elif raw:
            for enc in ("utf-8-sig", "cp1252"):
                try:
                    raw.decode(enc)
                    self.encoding = enc
                    break
                except UnicodeDecodeError:
                    continue
        if raw:
            self._cp.read_string(raw.decode(self.encoding))

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def sections(self) -> dict:
        """``{section: {key: value}}`` in file order (no DEFAULT section)."""
        return {s: dict(self._cp.items(s)) for s in self._cp.sections()}

    def has_key_matching(self, needle: str) -> bool:
        low = needle.lower()
        return any(low in k.lower() for s in self._cp.sections() for k in self._cp.options(s))

    def set(self, section: str, key: str, value: str) -> None:
        if not self._cp.has_section(section):
            self._cp.add_section(section)
        self._cp.set(section, key, str(value))

    def dumps(self) -> str:
        buf = io.StringIO()
        self._cp.write(buf, space_around_delimiters=False)
        return buf.getvalue()

    def diff_against(self, proposed: str) -> str:
        current = self.dumps() if self.path.is_file() else ""
        lines = difflib.unified_diff(
            current.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"{self.path} (current)",
            tofile=f"{self.path} (proposed)",
        )
        return "".join(lines)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


class BaseAdapter:
    """Common adapter state + the mandatory backup/atomic-write helpers (§6.2)."""

    def __init__(
        self,
        app: dict,
        hub=None,
        recipes_dir: Path | None = None,
        state_dir: Path | None = None,
    ):
        self.app = app
        self.app_id = str(app.get("id") or "?")
        self.recipes_dir = Path(recipes_dir) if recipes_dir else winehub.RECIPES_DIR
        self.hub = hub if hub is not None else winehub.WineHub([app], recipes_dir=self.recipes_dir)
        self._state_dir = Path(state_dir) if state_dir else None

    @property
    def state_dir(self) -> Path:
        return self._state_dir or winehub.sup.default_state_dir()

    # -- §6.2 contract (subclasses implement) --------------------------------
    def read(self) -> dict:
        raise NotImplementedError

    def apply(self, profile: dict, dry_run: bool = False) -> dict:
        raise NotImplementedError

    # -- shared plumbing ------------------------------------------------------
    def backup(self, path: Path | str) -> str:
        """Timestamped copy under ``<state>/config-backups/<app>/`` (§6.2)."""
        src = Path(path)
        if not src.is_file():
            raise ConfigError(f"{self.app_id}: nothing to back up — {src} does not exist")
        bdir = self.state_dir / "config-backups" / self.app_id
        bdir.mkdir(parents=True, exist_ok=True)
        dst = bdir / f"{_ts()}-{src.name}"
        dst.write_bytes(src.read_bytes())
        return str(dst)

    def _write(self, path: Path | str, text: str, encoding: str) -> None:
        """Atomic write: tmpfile + os.replace (the suite-wide convention)."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".gd-tmp")
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, target)
