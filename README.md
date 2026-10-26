# Grid Down

**Offline-first, air-gapped amateur radio emergency communications suite for Fedora 45 (x86_64, GNOME/Wayland, PipeWire).**

One unified control interface for ~40 radio applications — Winlink/VARA, JS8Call, packet/APRS,
FT8/WSJT-X, SSTV, SDR/monitoring, offline maps & reference, and a LoRa/Meshtastic mesh layer —
designed to be **built online, installed offline, and run fully offline**.

- Full design and scope: [`docs/SPECIFICATION.md`](docs/SPECIFICATION.md) (v0.3)
- Fedora 45 package audit: [`docs/audit/f45-package-audit.md`](docs/audit/f45-package-audit.md)
- Status: **Phase 0 — scaffold + spec + package audit**

## Why

In an emergency when the internet is down, HF/VHF packet, Winlink, JS8Call and Meshtastic still
move traffic — *if* the software is already installed, configured, and battle-tested before the
grid drops. Grid Down pre-stages that entire stack on a single Fedora 45 workstation and keeps it
consistent:

- **One rig control daemon** (`rigctld`, localhost:4532) owned by the suite — no serial contention.
- **One audio pipeline** (PipeWire loopbacks + per-app presets) — no "who owns the soundcard".
- **Adapters that write each app's native config** (no forks), with backup + atomic write + diff.
- **A VARA/Wine hub** — one Wine prefix for the whole VARA family + VarAC; installers and licenses
  are always user-supplied and never redistributed.
- **An offline kit** — a SHA-256-pinned artifact bundle built online in a `fedora:45` container and
  installed air-gap-side with one `bootstrap.sh --kit` invocation.

## Layout

```
grid-down/
├── docs/
│   ├── SPECIFICATION.md        # full system specification (v0.3)
│   └── audit/                  # Fedora package audit results
├── control/
│   ├── registry/apps/*.yaml    # app catalog: one file per app (41 entries)
│   ├── registry/bundles.yaml   # session bundles (winlink, js8, packet, ft8, aprs, sstv, wx, sdr, mesh)
│   ├── core/                   # profile model, registry loader, supervisor, adapters, doctor
│   ├── gui/                    # PySide6 Control Center (Phase 2+)
│   └── cli/gd.py               # `gd` command-line control interface
├── install/
│   ├── bootstrap.sh            # air-gap entry point (--online | --kit DIR)
│   ├── lib/common.sh           # install-library helpers
│   ├── recipes/*.yaml          # per-app install recipes (strategy, sources, pins)
│   ├── bundle-builder/         # kit builder (runs in fedora:45 container, online side)
│   └── kit-import/             # staging area for user-supplied artifacts (VARA, licenses)
├── profiles/default.yaml       # station profile template
├── tools/                      # dev/audit utilities
└── tests/                      # registry/profile validation tests
```

## Quick start (dev)

```bash
# validate registry + recipes
python3 -m pytest tests/ -q

# CLI smoke test
python3 control/cli/gd.py apps list

# Fedora 45 package audit (online machine, uses Docker)
bash tools/run-audit.sh > docs/audit/f45-package-audit.md
```

## Phased rollout

| Phase | Scope |
|---|---|
| P0 | Scaffold, spec v0.3, F45 package audit *(current)* |
| P1 | Installer library + kit builder skeleton; first online↔air-gap round-trip (dnf-only subset) |
| P2 | Control Center GUI + `gd` CLI v1; rigctld + PipeWire audio ownership |
| P3 | VARA/Wine Hub; Winlink session bundle end-to-end |
| P4 | Remaining apps + LoRa/Meshtastic recipes, adapters, localhost services |
| P5 | Offline USB kit, bootstrap hardening, docs, drills |

## Rules of the road

- The suite never redistributes VARA/VarAC installers or licenses; the kit build **fails closed**
  if such an artifact is missing instead of substituting a download.
- Everything installed must run with zero network connectivity. Anything that phones home
  (e.g. Meshtastic MQTT uplink) is disabled and flagged by Doctor in air-gap mode.
- Adapters write native config formats only — never patched apps, never wrapper hacks.
