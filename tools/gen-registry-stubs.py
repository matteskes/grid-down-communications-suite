#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Generate Phase-0 registry + recipe stubs for the Grid Down app catalog.

Idempotent: overwrites control/registry/apps/<id>.yaml and install/recipes/<id>.yaml
from the APPS table below. The APPS table is the initial catalog data taken from
docs/SPECIFICATION.md §8. Edit generated files from here on; keep this script for
provenance and for re-seeding a fresh checkout.

Usage: python3 tools/gen-registry-stubs.py [--check]
  --check  fail (exit 1) if generated output would differ from what is on disk
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG_DIR = ROOT / "control" / "registry" / "apps"
REC_DIR = ROOT / "install" / "recipes"

CATEGORIES = {
    "core",
    "hf",
    "winlink",
    "packet",
    "aprs",
    "voice_sstv",
    "map_weather",
    "reference",
    "monitor",
    "mesh",
    "av",
}

# id, name, category, strategy, packages/notes, adapter, launch (via, cmd), tagline
APPS = [
    # --- core ---
    (
        "hamlib",
        "Hamlib (rigctld)",
        "core",
        "dnf",
        ["hamlib", "python3-hamlib"],
        "none",
        ("managed-service", "rigctld -T 127.0.0.1 -p 4532 -v"),
        "Rig control daemon owned by the suite (localhost:4532)",
    ),
    # --- winlink ---
    (
        "direwolf",
        "Dire Wolf",
        "packet",
        "dnf",
        ["direwolf"],
        "direwolf-conf",
        ("managed-service", "direwolf -c /etc/direwolf.conf -l /var/log/direwolf"),
        "Softmodem TNC / APRS digipeater / KISS TCP 8001",
    ),
    (
        "ardop",
        "ARDOP TNC",
        "winlink",
        "build",
        [],
        "ardop-cfg",
        ("managed-service", "ardopc 8515 <capture> <playback>"),
        "ARDOP modem for Winlink HF sessions (pflarue/ardop)",
    ),
    (
        "pat",
        "Pat (Winlink client)",
        "winlink",
        "tarball",
        [],
        "pat-conf",
        ("desktop-app", "pat"),
        "Winlink webmail client; talks to VARA over the Wine hub",
    ),
    (
        "vara-hf",
        "VARA HF",
        "winlink",
        "wine",
        [],
        "vara-ini",
        ("wine-hub", "VARA.exe (via gd winehub)"),
        "VARA HF modem (user-supplied installer + license)",
    ),
    (
        "vara-fm",
        "VARA FM",
        "winlink",
        "wine",
        [],
        "vara-ini",
        ("wine-hub", "VARA FM.exe (via gd winehub)"),
        "VARA FM modem (user-supplied installer + license)",
    ),
    (
        "vara-sat",
        "VARA SAT",
        "winlink",
        "wine",
        [],
        "vara-ini",
        ("wine-hub", "VARA SAT.exe (via gd winehub)"),
        "VARA SAT modem (user-supplied installer + license)",
    ),
    (
        "vara-chat",
        "VARA Chat",
        "winlink",
        "wine",
        [],
        "vara-ini",
        ("wine-hub", "VARA Chat.exe (via gd winehub)"),
        "VARA Chat client (user-supplied installer + license)",
    ),
    (
        "varac",
        "VarAC",
        "winlink",
        "wine",
        [],
        "varac-ini",
        ("wine-hub", "VarAC.exe (via gd winehub)"),
        "VARA-based chat/QSO platform (user-supplied installer)",
    ),
    (
        "vara-term",
        "VARA Terminal",
        "winlink",
        "wine",
        [],
        "vara-ini",
        ("wine-hub", "VARA Terminal.exe (via gd winehub)"),
        "VARA terminal (user-supplied installer)",
    ),
    # --- hf ---
    (
        "js8call",
        "JS8Call",
        "hf",
        "dnf",
        ["js8call"],
        "js8call-ini",
        ("desktop-app", "js8call"),
        "JS8 weak-signal messaging",
    ),
    (
        "fldigi",
        "fldigi",
        "hf",
        "dnf",
        ["fldigi"],
        "fldigi-xmlrpc",
        ("desktop-app", "fldigi"),
        "Digital modem suite (Olivia, PSK, RTTY, NBEMS)",
    ),
    (
        "flarq",
        "flarq",
        "hf",
        "dnf",
        ["fldigi"],
        "flarq-xmlrpc",
        ("desktop-app", "flarq"),
        "ARQ data modem companion to fldigi (shipped by the fldigi RPM — Provides: flarq)",
    ),
    (
        "flamp",
        "flamp",
        "hf",
        "dnf",
        ["flamp"],
        "flamp-xmlrpc",
        ("desktop-app", "flamp"),
        "Amateur Multicast Protocol — file broadcast for EMCOMM",
    ),
    (
        "flmsg",
        "flmsg",
        "hf",
        "dnf",
        ["flmsg"],
        "flmsg-forms",
        ("desktop-app", "flmsg"),
        "IARC/NBEMS standardized message forms",
    ),
    (
        "flwrap",
        "flwrap",
        "hf",
        "build",
        [],
        "none",
        ("desktop-app", "/opt/grid-down/flwrap/bin/flwrap"),
        "File encapsulator for fldigi transfers (no F45 package; pinned source build)",
    ),
    (
        "fllog",
        "fllog",
        "hf",
        "dnf",
        ["fllog"],
        "fllog-xmlrpc",
        ("desktop-app", "fllog"),
        "ADIF logging with fldigi integration",
    ),
    (
        "wsjtx",
        "WSJT-X",
        "hf",
        "dnf",
        ["wsjtx"],
        "wsjtx-ini",
        ("desktop-app", "wsjtx"),
        "FT8/JT65/WSPR modem suite",
    ),
    (
        "wsjtx-improved",
        "WSJT-X Improved (JTDX fork)",
        "hf",
        "build",
        [],
        "wsjtx-ini",
        ("desktop-app", "/opt/grid-down/wsjtx-improved/bin/wsjtx"),
        "Community-enhanced WSJT-X build, isolated prefix",
    ),
    (
        "qsstv",
        "QSSTV",
        "voice_sstv",
        "dnf",
        ["qsstv"],
        "qsstv-conf",
        ("desktop-app", "qsstv"),
        "SSTV send/receive",
    ),
    # --- packet/aprs ---
    (
        "qttermtcp",
        "QtTermTCP",
        "packet",
        "build",
        [],
        "qttermtcp-cfg",
        ("desktop-app", "/opt/grid-down/qttermtcp/QtTermTCP"),
        "Packet terminal for BPQ/AGW/KISS hosts (g8bpq)",
    ),
    (
        "paracon",
        "Paracon",
        "packet",
        "build",
        [],
        "paracon-cfg",
        ("desktop-app", "/opt/grid-down/paracon/paracon"),
        "Packet TNC terminal with file transfer (mfncooper)",
    ),
    (
        "chattervox",
        "ChatterVox",
        "packet",
        "tarball",
        [],
        "chattervox-json",
        ("cli-tool", "chattervox"),
        "100 baud ASTRO packet encryption/compression (brannondorsey)",
    ),
    (
        "yaac",
        "YAAC",
        "aprs",
        "tarball",
        [],
        "yaac-prefs",
        ("desktop-app", "java -jar /opt/grid-down/yaac/YAAC.jar"),
        "Yet Another APRS Client (Java)",
    ),
    # --- monitor ---
    (
        "js8spotter",
        "JS8Spotter",
        "monitor",
        "tarball",
        [],
        "none",
        ("local-service", "php -S 127.0.0.1:8091 (served by suite)"),
        "JS8 call-activity dashboard, served on localhost (DanDawson)",
    ),
    (
        "commstatone",
        "CommStat One",
        "monitor",
        "static",
        [],
        "none",
        ("local-service", "python3 -m http.server 8090 (served by suite)"),
        "EMCOMM station status web dashboard (W5DMH)",
    ),
    (
        "artemis",
        "Artemis",
        "monitor",
        "tarball",
        [],
        "artemis-paths",
        ("desktop-app", "artemis"),
        "RF signal ID: 580+ signal library, parametric filter (AresValley, GPL-3.0, v4.2)",
    ),
    (
        "gridtracker",
        "GridTracker",
        "monitor",
        "tarball",
        [],
        "gridtracker-conf",
        ("desktop-app", "GridTracker"),
        "WSJT-X traffic mapper / messaging grid",
    ),
    (
        "sdrpp",
        "SDR++",
        "monitor",
        "build",
        [],
        "sdrpp-conf",
        ("desktop-app", "sdrpp"),
        "SDR receiver (Airspy/HackRF/RTL — built from source, AlexandreRouma)",
    ),
    (
        "dump1090",
        "dump1090",
        "monitor",
        "build",
        ["rtl-sdr"],
        "none",
        ("managed-service", "dump1090 --net --device-index 0"),
        "ADS-B receiver via RTL-SDR",
    ),
    # --- reference / map / weather ---
    (
        "kiwix",
        "Kiwix",
        "reference",
        "dnf",
        ["kiwix-desktop"],
        "kiwix-library",
        ("desktop-app", "kiwix-desktop"),
        "Offline ZIM library (Wikipedia med, ARRL docs, wikis)",
    ),
    (
        "osm-navit",
        "OSM offline navigation (Navit)",
        "map_weather",
        "build",
        [],
        "navit-xml",
        ("desktop-app", "/opt/grid-down/navit/navit"),
        "Offline OSM navigation (no F45 package; pinned source build)",
    ),
    (
        "xygrib",
        "XYGrib",
        "map_weather",
        "build",
        [],
        "xygrib-conf",
        ("desktop-app", "/opt/grid-down/xygrib/xyGrib"),
        "GRIB weather viewer (no F45 package; pinned source build, staged GRIBs)",
    ),
    # --- mesh (§6.11) ---
    (
        "meshtastic",
        "Meshtastic CLI",
        "mesh",
        "pip-vendored",
        [],
        "meshtastic-cli",
        ("cli-tool", "meshtastic"),
        "LoRa mesh device management (USB/BLE/TCP), vendored wheels",
    ),
    (
        "meshtasticd",
        "Meshtastic daemon",
        "mesh",
        "tarball",
        [],
        "meshtasticd-yaml",
        ("managed-service", "meshtasticd"),
        "Linux-native LoRa HAT daemon (upstream RPM, pinned)",
    ),
    (
        "meshtastic-web",
        "Meshtastic Web",
        "mesh",
        "static",
        [],
        "none",
        ("local-service", "served on 127.0.0.1:8092 by suite"),
        "Official web client served air-gap-side with Web Serial",
    ),
    # --- av / utilities ---
    (
        "audacity",
        "Audacity",
        "av",
        "dnf",
        ["audacity"],
        "none",
        ("desktop-app", "audacity"),
        "Audio editor for SSTV/sample prep",
    ),
    (
        "ffmpeg",
        "ffmpeg",
        "av",
        "dnf",
        ["ffmpeg"],
        "none",
        ("cli-tool", "ffmpeg"),
        "A/V conversion (RPM Fusion build)",
    ),
    (
        "sox",
        "SoX",
        "av",
        "dnf",
        ["sox"],
        "none",
        ("cli-tool", "sox"),
        "Sound processing for modem audio prep",
    ),
    (
        "snapshot",
        "GNOME Snapshot",
        "av",
        "dnf",
        ["snapshot"],
        "none",
        ("desktop-app", "snapshot"),
        "Webcam capture (Cheese successor; cheese retired from Fedora 45)",
    ),
    (
        "conky",
        "Conky",
        "av",
        "dnf",
        ["conky"],
        "conky-conf",
        ("managed-service", "conky -c ~/.config/grid-down/conky.conf"),
        "Heads-up system/wx overlay on the desktop",
    ),
]

STRATEGY_NOTE = {
    "dnf": "Install from Fedora 45 / RPM Fusion repos at air-gap time via the kit-local dnf repo.",
    "dnf-verify": "Confirm present in F45 repos during Phase 0 audit; else fall back to pinned source build.",
    "build": "Pinned git tarball built in fedora:45 container during kit build; binary shipped in kit.",
    "tarball": "Pinned upstream tarball/binary shipped in kit, extracted to /opt/grid-down.",
    "wine": "Installed into the shared VARA Wine prefix; installer user-supplied (kit-import/), never redistributed.",
    "pip-vendored": "Vendored wheels shipped in kit; installed offline with Fedora python3.",
    "static": "Static site/bundle shipped in kit; served on localhost by the suite.",
}

# Which dnf repo each recipe's package set resolves from (kit-local repo split;
# see docs/audit/f45-package-audit.md for the F45 audit decision). Default: fedora.
DNF_REPO_DEFAULT = "fedora"
DNF_REPO = {
    "ffmpeg": "rpmfusion-free",  # RPM Fusion build; enablement via pinned release RPMs
}

# Wine apps: installer_glob matched case-insensitively against install/kit-import/;
# exes are the candidate basenames probed under <prefix>/drive_c after install
# (upstream names drift across VARA versions, so candidates are listed in order).
# installer picks the install mode (core/winehub): "setup" = Inno exe run with
# /VERYSILENT switches; "portable" = plain zip unpacked into install_dir (VarAC
# ships no installer — running its exe would launch the app, not install it).
WINE_META = {
    "vara-hf": ("VARA HF*", ["VARA.exe"], "setup", None),
    "vara-fm": ("VARA FM*", ["FM.exe", "VARAFM.exe"], "setup", None),
    "vara-sat": ("VARA SAT*", ["SAT.exe", "VARASAT.exe"], "setup", None),
    "vara-chat": ("VARA Chat*", ["Chat.exe", "VARACHAT.exe"], "setup", None),
    "vara-term": ("VARA Terminal*", ["Terminal.exe", "VARATERM.exe"], "setup", None),
    "varac": ("VarAC*", ["VarAC.exe"], "portable", "C:\\VarAC"),
}


def registry_yaml(app) -> str:
    id_, name, cat, strat, pkgs, adapter, (via, cmd), tagline = app
    pkgs_s = "[" + ", ".join(pkgs) + "]" if pkgs else "[]"
    text = (
        "# Grid Down app registry entry (Phase 0 stub; schema: docs/SPECIFICATION.md §6.1)\n"
        f"id: {id_}\n"
        f"name: {name}\n"
        f"category: {cat}\n"
        "ui:\n"
        f'  tagline: "{tagline}"\n'
        "install:\n"
        f"  strategy: {strat}\n"
        f"  recipe: {id_}\n"
        f"  packages: {pkgs_s}\n"
        f'  note: "{STRATEGY_NOTE[strat]}"\n'
        "launch:\n"
        f"  via: {via}\n"
        f'  cmd: "{cmd}"\n'
        "config:\n"
        f"  adapter: {adapter}\n"
    )
    if strat == "wine":
        # Windows INIs live beside their program exe inside the prefix; the
        # adapter resolves the real path via the wine hub's install probe
        # (exe names drift across VARA versions, so no concrete name here).
        text += (
            "  native_path: INI beside the installed exe"
            f"   # resolved by the {adapter} adapter via core/winehub (Phase 3)\n"
        )
    else:
        text += "  native_path: TBD   # pin exact native config path per station (Phase 3/4)\n"
    text += "state: stub\n"
    return text


def recipe_yaml(app) -> str:
    id_, name, cat, strat, pkgs, adapter, (via, cmd), tagline = app
    lines = [
        f"# Install recipe for {id_} (Phase 0 skeleton)",
        f"# strategy: {strat} — {STRATEGY_NOTE[strat]}",
        "recipe:",
        f"  id: {id_}",
        f"  strategy: {strat}",
    ]
    if pkgs:
        lines.append("  dnf:")
        lines.append(f"    repo: {DNF_REPO.get(id_, DNF_REPO_DEFAULT)}")
        lines.append("    packages:")
        lines += [f"      - {p}" for p in pkgs]
    if strat == "tarball":
        lines += [
            "  tarball:",
            "    url: PIN-ME          # exact upstream artifact URL",
            "    sha256: PIN-ME",
            f"    dest: /opt/grid-down/{id_}",
        ]
    elif strat == "build":
        lines += [
            "  source:",
            "    git: PIN-ME          # repo URL + tag",
            "    sha256: PIN-ME       # archive sha256",
            "    build_container: fedora:45",
        ]
    elif strat == "wine":
        glob_, exes, installer, install_dir = WINE_META.get(
            id_, ("PIN-ME*", ["PIN-ME.exe"], "setup", None)
        )
        lines += [
            "  wine:",
            "    prefix: /opt/grid-down/wine-prefix",
            f'    installer_glob: "{glob_}"   # user-supplied .zip/.exe staged in install/kit-import/ (G6)',
            f"    exes: [{', '.join(exes)}]   # candidate basenames probed under <prefix>/drive_c",
        ]
        if installer == "portable":
            lines += [
                "    installer: portable   # plain zip (no Inno setup): unpacked into install_dir, never run",
                f"    install_dir: '{install_dir}'   # target dir under the prefix's drive_c",
            ]
        lines += [
            f"    ini_set: []           # config keys the {adapter} adapter may write (§6.2); pinned at live E2E",
            "    license: user-supplied   # applied in-app by the operator; unregistered works (trial delay)",
        ]
    elif strat == "pip-vendored":
        lines += [
            "  pip:",
            "    wheels_dir: kit/wheels/",
            "    install: python3 -m pip install --no-index --find-links kit/wheels/ meshtastic",
        ]
    elif strat == "static":
        lines += [
            "  static:",
            f"    src: kit/static/{id_}/",
            "    serve: localhost",
        ]
    lines += ["  postinst: []           # systemd units, udev rules, adapter hooks — Phase 1+", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true", help="fail if on-disk files differ from generated output"
    )
    args = ap.parse_args()

    ids = [a[0] for a in APPS]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        print(f"FATAL: duplicate ids: {dupes}")
        return 1
    bad_cat = [(a[0], a[2]) for a in APPS if a[2] not in CATEGORIES]
    if bad_cat:
        print(f"FATAL: unknown categories: {bad_cat}")
        return 1

    changed = 0
    for app in APPS:
        targets = (
            (REG_DIR / f"{app[0]}.yaml", registry_yaml(app)),
            (REC_DIR / f"{app[0]}.yaml", recipe_yaml(app)),
        )
        for path, text in targets:
            if args.check:
                if not path.exists() or path.read_text() != text:
                    print(f"DIFF: {path}")
                    changed += 1
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists() or path.read_text() != text:
                    path.write_text(text)
                    changed += 1
    verb = "would change" if args.check else "wrote"
    print(f"{verb} {changed} file(s); catalog size: {len(APPS)} apps")
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
