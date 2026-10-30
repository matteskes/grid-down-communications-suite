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

M7 extends the same contract to the twelve native apps: paths are pinned
from each app's own source/artifact shipped in the kit (per-module
docstrings carry the evidence), recipes declare writable keys under a
top-level ``config:`` block (``config.set`` rows; ``config.args`` for
ardopcf's launch-args file), and masking generalizes to any credential-
shaped key name (reg/password/key/token/secret/passcode). See
``adapters/native.py`` for the shared machinery.
"""

from __future__ import annotations

from .ardop_args import ArdopArgsAdapter
from .base import BaseAdapter, ConfigError, IniFile, is_secret_key
from .ini_conf import (
    ArtemisPathsAdapter,
    IniConfAdapter,
    ParaconConfAdapter,
    QtTermTCPConfAdapter,
    XyGribConfAdapter,
)
from .json_conf import (
    ChattervoxConfAdapter,
    GridtrackerConfAdapter,
    JsonConfAdapter,
    PatConfAdapter,
    SdrppConfAdapter,
)
from .meshtasticd_conf import MeshtasticdYamlAdapter
from .native import (
    JsonDoc,
    NativeConfigAdapter,
    PropsDoc,
    YamlDoc,
    mask_tree,
    xdg_config_dir,
    xdg_data_dir,
)
from .navit_conf import NavitXmlAdapter
from .vara import VaraIniAdapter
from .varac import VaracIniAdapter
from .yaac_props import YaacPrefsAdapter

__all__ = [
    "ADAPTERS",
    "ArdopArgsAdapter",
    "ArtemisPathsAdapter",
    "BaseAdapter",
    "ChattervoxConfAdapter",
    "ConfigError",
    "GridtrackerConfAdapter",
    "IniConfAdapter",
    "IniFile",
    "JsonConfAdapter",
    "JsonDoc",
    "MeshtasticdYamlAdapter",
    "NativeConfigAdapter",
    "NavitXmlAdapter",
    "ParaconConfAdapter",
    "PatConfAdapter",
    "PropsDoc",
    "QtTermTCPConfAdapter",
    "SdrppConfAdapter",
    "VaraIniAdapter",
    "VaracIniAdapter",
    "XyGribConfAdapter",
    "YaacPrefsAdapter",
    "YamlDoc",
    "get_adapter",
    "is_secret_key",
    "mask_tree",
    "xdg_config_dir",
    "xdg_data_dir",
]

# registry `config.adapter` name -> implementation (control/registry/apps/*.yaml)
ADAPTERS = {
    "vara-ini": VaraIniAdapter,  # VARA HF / FM / SAT / Chat / Terminal
    "varac-ini": VaracIniAdapter,  # VarAC
    # native apps (M7) — paths pinned in each adapter's docstring
    "ardop-cfg": ArdopArgsAdapter,  # launch-args file (ardopcf has no config file)
    "pat-conf": PatConfAdapter,  # ~/.config/pat/config.json
    "qttermtcp-cfg": QtTermTCPConfAdapter,  # exe-adjacent QtTermTCP.ini
    "paracon-cfg": ParaconConfAdapter,  # paracon.cfg (cwd-first, kit dir)
    "sdrpp-conf": SdrppConfAdapter,  # <dist>/config.json
    "gridtracker-conf": GridtrackerConfAdapter,  # ~/.config/GridTracker2/…
    "yaac-prefs": YaacPrefsAdapter,  # ~/.yaac/yaac.properties
    "meshtasticd-yaml": MeshtasticdYamlAdapter,  # /etc/meshtasticd/config.d drop-in
    "chattervox-json": ChattervoxConfAdapter,  # ~/.chattervox/config.json
    "xygrib-conf": XyGribConfAdapter,  # ~/.config/openGribs/XyGrib.conf
    "navit-xml": NavitXmlAdapter,  # ~/.navit/navit.xml (seed-then-patch)
    "artemis-paths": ArtemisPathsAdapter,  # ~/.local/share/AresValley/Artemis
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
