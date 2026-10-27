# Third-party license inventory (M0)

**Date:** 2026-10-26 · **Feeds:** kit manifest (G5), spec §13 · **Kit posture:** Grid Down's own
code is **MIT** (`/LICENSE`); this file tracks *bundled* third-party components only.

**Method:** upstream LICENSE files + GitHub license detection, Debian copyright data, Fedora
mdapi metadata, upstream docs. Fedora dist-git and Debian sources were bot-walled (Anubis) at
research time — rows marked **verify at pin** get re-checked when a recipe's `PIN-ME` fields are
resolved. The authoritative per-build record is `rpm -q --qf '%{LICENSE}\n' <pkg>`, captured by
the kit builder into the manifest.

## A. dnf-installed apps (Fedora 45 / RPM Fusion binaries)

| Package | License (SPDX) | Notes |
|---|---|---|
| direwolf | GPL-2.0-or-later | |
| hamlib, python3-hamlib | LGPL-2.1-or-later | lib only; no static linking |
| fldigi | GPL-2.0-or-later | its RPM Provides: flarq (Flags #4) |
| flamp, flmsg, fllog | GPL-2.0-or-later | W1HKJ suite; exact tag verify at pin |
| wsjtx | GPL-3.0-or-later | |
| js8call | GPL-3.0-or-later | WSJT-X derivative; Qt6 build in F45 |
| qsstv | GPL-2.0-or-later | exact Fedora tag verify at pin |
| kiwix-desktop | GPL-3.0-or-later | pulls aria2 (GPL-2.0-or-later) |
| audacity | GPL-2.0-or-later | bundled BSD/MIT libs are distro-managed |
| sox | GPL-2.0-or-later / LGPL-2.1-or-later | F46+ switches to sox_ng fork, same family |
| conky | GPL-3.0-or-later | |
| snapshot | GPL-3.0-or-later | GNOME Snapshot (cheese retired) |
| ffmpeg (rpmfusion-free) | GPL-2.0-or-later | rpmfusion build enables --enable-gpl |
| rtl-sdr | GPL-2.0-or-later | dump1090 build dependency |
| nodejs | MIT | plus bundled deps (Apache-2.0, ISC, …) |
| java-25-openjdk | GPL-2.0-only WITH Classpath-exception-2.0 | YAAC runtime |
| wine, winetricks | LGPL-2.1-or-later | |
| createrepo_c | GPL-2.0-or-later | |
| python3-pip | MIT | |
| php-cli | PHP-3.01 | JS8Spotter local service |

## B. build / tarball / vendored (upstream sources, pinned by recipes)

| Project | Upstream | License (SPDX) | Notes |
|---|---|---|---|
| pat | la5nta/pat | MIT | LICENSE verified upstream |
| ardopcf | pflarue/ardop | MIT | commonly mis-assumed GPL — it is MIT |
| paracon | mfncooper/paracon | MIT | LICENSE verified upstream |
| js8spotter | DanDawson/js8spotter | MIT | LICENSE.txt verified (KF7MIX); incl. Azure TTK Theme (MIT) |
| commstatone | W5DMH/commstatone | GPL-3.0 | GitHub-detected |
| chattervox | brannondorsey/chattervox | GPL-3.0 | package.json license field |
| meshtastic | meshtastic (python CLI) | GPL-3.0 | pip-vendored wheels |
| meshtasticd | meshtastic (firmware) | GPL-3.0 | |
| meshtastic-web | meshtastic (web client) | GPL-3.0 | served on localhost |
| sdrpp | AlexandreRouma/SDRPlusPlus | GPL-3.0 | |
| xygrib | opengribs/XyGrib | GPL-3.0 | |
| artemis | AresValley/Artemis | GPL-3.0 | .rpm v4.2 |
| osm-navit | navit-gps/navit | GPL-2.0 | |
| dump1090 | fork-dependent | BSD-3-Clause (antirez original) / GPL-2.0-or-later (FlightAware, mutability) | pin the fork — Flags #3 |
| flwrap | W1HKJ | GPL-2.0-or-later | exact tag verify at pin |
| flarq | W1HKJ | GPL-2.0-or-later | shipped by fldigi RPM (verified in container) — Flags #4 resolved |
| wsjtx-improved | WSJT-X improved fork | GPL-3.0-or-later | derivative of GPL-3 WSJT-X |
| yaac | KA2DDO/YAAC | LGPL (family) | exact version verify at pin — Flags #6 |
| qttermtcp | g8bpq/QtTermTCP | **none found** | Flags #1 — do not ship until resolved |

## C. user-supplied / closed (kit never redistributes — G6, fails closed)

| Project | Terms | Handling |
|---|---|---|
| VARA HF/FM/Sat/Chat/Term | proprietary shareware (EA5HVK); unregistered use free with delays; per-mode registration | kit-import installers + user licenses; shared Wine prefix |
| VarAC | freeware, closed source (4Z1AC) | same as VARA |
| GridTracker | freeware; source visible on GitLab but no FOSS license found; site license page unreachable (404) | verify redistribution terms before any tarball ships — Flags #2; fallback: operator-download |

## D. content data (operator-staged, spec §6.8)

| Content | License |
|---|---|
| Kiwix ZIM files (Wikipedia etc.) | CC-BY-SA (varies per ZIM) |
| OSM map extracts | ODbL-1.0 (attribution; share-alike on derived databases) |
| GRIB weather files | varies by source (NOAA products are public domain) |
| Artemis signal database | GPL-3.0 (ships with the app) |

## Flags — resolve at pin time (P3/P4)

1. **QtTermTCP** — no LICENSE/COPYING/README anywhere in `g8bpq/QtTermTCP` (GitHub license
   detection: none; contents API listing confirmed). Source is public but no grant exists, so
   redistribution is legally unclear. Action: ask G8BPQ for terms, or demote to user-supplied
   (kit-import) like the VARA family.
2. **GridTracker** — exact redistribution terms unverified (license page 404). Capture at pin;
   fallback is operator-download.
3. **dump1090** — recipe must pin the fork: antirez original (BSD-3-Clause) or
   FlightAware/mutability (GPL-2.0-or-later). Either is compatible with the MIT umbrella;
   record the choice in the manifest.
4. **flarq** — RESOLVED 2026-10-26: Fedora's `fldigi` package Provides: flarq = 4.2.13 (mdapi),
   confirmed in the fedora:45 container (`dnf install -y fldigi && test -x /usr/bin/flarq` →
   present). flarq now rides a dnf recipe installing `fldigi`; the pinned-source build recipe is
   retired (addendum in `f45-package-audit.md`). RPM `License:` tag still captured at kit build
   per Flags #5.
5. **W1HKJ suite / qsstv exact tags** — capture authoritative `License:` tags via rpm at kit
   build (distro sources were bot-walled during research).
6. **yaac** — confirm the LGPL version ("or-later"?) from the jar's bundled license at pin.
