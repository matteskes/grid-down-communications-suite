# Fedora 45 package audit — Phase 0

**Date:** 2026-10-26 · **Image:** `fedora:45` (Docker, `--platform linux/amd64`) ·
**Container arch:** x86_64 · **Method:** `dnf list available` per package, inside the container,
after `dnf makecache`; RPM Fusion enabled via official release RPMs for the second pass.
**Runner:** `bash tools/run-audit.sh` · **Script:** `tools/audit-f45-packages.sh`

> Lesson learned (round 1 discarded): on an Apple Silicon host, `docker pull fedora:45` fetches
> the **aarch64** image, whose repos answer differently (wine absent, `java-25-openjdk` only,
> `.aarch64` rows in glob sweeps). The target station is x86_64, so the audit must pin
> `--platform linux/amd64` — the runner now does this unconditionally.

## Raw results (x86_64, verbatim)

```text
image: fedora:45 (linux/amd64)  date: 2026-10-26T17:56:49Z  container-arch: x86_64
rpmfusion: NOT enabled (ffmpeg/audacity strategy may change)
--- ham/digital group ---
AVAILABLE  direwolf
AVAILABLE  hamlib
MISSING    hamlib-python
AVAILABLE  python3-hamlib
AVAILABLE  fldigi
MISSING    flarq
AVAILABLE  flamp
AVAILABLE  flmsg
MISSING    flwrap
AVAILABLE  fllog
AVAILABLE  flrig
AVAILABLE  wsjtx
AVAILABLE  qsstv
--- reference/map/weather ---
AVAILABLE  kiwix-desktop
MISSING    xygrib
MISSING    navit
--- av / utils ---
AVAILABLE  audacity
AVAILABLE  sox
AVAILABLE  conky
MISSING    cheese
MISSING    ffmpeg
--- toolchain / runtime ---
AVAILABLE  rtl-sdr
AVAILABLE  nodejs
MISSING    java-21-openjdk
MISSING    java-17-openjdk
AVAILABLE  wine
AVAILABLE  winetricks
AVAILABLE  createrepo_c
AVAILABLE  python3-pip
AVAILABLE  php-cli
--- expected-missing (tarball/build/wine strategies) ---
AVAILABLE  js8call
MISSING    pat
MISSING    meshtastic
MISSING    gridtracker
--- rpmfusion via official release rpms ---
rpmfusion: enabled (release rpms)
AVAILABLE  ffmpeg
--- alternate names for round-1 misses ---
AVAILABLE  wine-common
AVAILABLE  wine-core
MISSING    java-openjdk
MISSING    java-21-openjdk-headless
AVAILABLE  java-latest-openjdk
AVAILABLE  snapshot
AVAILABLE  obs-studio
MISSING    gnome-snapshot
--- glob sweeps ---
glob wine:
wine.i686   11.20-1.fc45 updates-testing
wine.x86_64 11.20-1.fc45 updates-testing
glob java-2*openjdk*:
java-25-openjdk.x86_64  1:25.0.4.1.1-1.1.fc45 fedora
glob flarq*:            (empty)
glob flwrap*:           (empty)
glob xygrib*:           (empty)
glob navit*:            (empty)
glob cheese*:           (empty)
glob snapshot*:
snapshot.x86_64 51.0-2.fc45 fedora
glob meshtastic*:       (empty)
```

## Decisions applied to the registry (2026-10-26)

| App | Before | After | Basis |
|---|---|---|---|
| js8call | dnf-verify | **dnf** | AVAILABLE in F45 repos |
| flamp | dnf-verify | **dnf** | AVAILABLE |
| flmsg | dnf-verify | **dnf** | AVAILABLE |
| fllog | dnf-verify | **dnf** | AVAILABLE |
| qsstv | dnf-verify | **dnf** | AVAILABLE |
| kiwix-desktop | dnf-verify | **dnf** | AVAILABLE |
| flarq | dnf | **build** | MISSING (glob empty) — pinned source build |
| flwrap | dnf-verify | **build** | MISSING (glob empty) |
| xygrib | dnf-verify | **build** | MISSING (glob empty) |
| osm-navit | dnf-verify | **build** | MISSING (glob empty) |
| cheese → **snapshot** | dnf (cheese) | dnf (snapshot) | cheese retired from Fedora; GNOME Snapshot available (51.0-2) |
| hamlib packages | [hamlib] | [hamlib, python3-hamlib] | bindings package is `python3-hamlib` (not `hamlib-python`) |

## Notes for later phases

- **wine 11.20** is present on x86_64 but currently from `updates-testing` (F45 just GA'd) —
  the kit builder must record the exact NVR + repo in the manifest (G5).
- **ffmpeg** requires RPM Fusion release RPMs *before* kit download; the builder installs them
  from pinned URLs (recorded in the manifest).
- **Java:** only `java-25-openjdk` / `java-latest-openjdk` remain; YAAC recipe pins
  `java-latest-openjdk-headless` (or the 25 equivalent at kit time).
- **php-cli** is available — JS8Spotter's local service needs no extra repo.
- `pat`, `meshtastic`, `gridtracker` absent as designed (tarball / vendored-wheels strategies).
