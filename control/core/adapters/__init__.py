# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Config adapters (§6.2): read/apply app-native config from the station profile.

Contract (spec §6.2): every adapter exposes ``read() -> dict`` and
``apply(profile, dry_run=False) -> dict``, with a mandatory timestamped
``backup()`` before any write and atomic writes (tmpfile + ``os.replace``).
``gd config show`` prints ``read()``; ``gd config apply`` prints the unified
diff before writing.

The VARA family's config lives inside the shared wine prefix, exe-adjacent
(VARA.ini beside VARA.exe, VarAC.ini beside VarAC.exe) — the "native path"
§6.1 leaves TBD for native apps is resolved here through core/winehub's
install probe instead of a pinned path, because upstream install directories
drift across versions (the probe already handles that for ``exes``).

Writes are conservative by design: an adapter may set ONLY the keys its
recipe declares (``install/recipes/<id>.yaml`` → ``wine.ini_set:`` rows of
``{section, key, value}``, where ``value`` may template ``{callsign}`` and
``{grid}`` from the station profile), and registration-shaped values
(G6: license keys are operator-applied in-app) are always masked in
``read()`` output. Exact VARA.ini key names get pinned into the shipped
recipes at the live E2E — same discipline as the ``exes`` candidates.
"""

from __future__ import annotations

from .base import BaseAdapter, ConfigError, IniFile
from .vara import VaraIniAdapter
from .varac import VaracIniAdapter

__all__ = [
    "ADAPTERS",
    "BaseAdapter",
    "ConfigError",
    "IniFile",
    "VaraIniAdapter",
    "VaracIniAdapter",
    "get_adapter",
]

# registry `config.adapter` name -> implementation (control/registry/apps/*.yaml)
ADAPTERS = {
    "vara-ini": VaraIniAdapter,  # VARA HF / FM / SAT / Chat / Terminal
    "varac-ini": VaracIniAdapter,  # VarAC
}


def get_adapter(app: dict, **kwargs) -> BaseAdapter:
    """The adapter a registry app declares, ready to read/apply.

    Kwargs pass through to the class (``hub``, ``recipes_dir``, ``state_dir``
    for tests). Apps whose registry entry declares ``adapter: none`` (or no
    config block) refuse here — there is nothing to read or apply for them.
    """
    name = str((app.get("config") or {}).get("adapter") or "none")
    cls = ADAPTERS.get(name)
    if cls is None:
        known = ", ".join(sorted(ADAPTERS)) or "none"
        raise ConfigError(
            f"{app.get('id', '?')}: config adapter {name!r} has no implementation "
            f"(registered adapters: {known})"
        )
    return cls(app, **kwargs)
