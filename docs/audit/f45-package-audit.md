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

## GUI dependency check (M3, 2026-10-26)

`gd gui` (Control Center, spec §7) needs PySide6 on the target station. Repoquery
inside `fedora:45` `linux/amd64` (same method as the raw results above):

```text
fedora:45 (linux/amd64)  date: 2026-10-26T23:28:12Z  container-arch: x86_64
python3-pyside6-0:6.11.2-2.fc45.i686
python3-pyside6-0:6.11.2-2.fc45.x86_64
python3-pyside6-0:6.11.2-3.fc45.i686
AVAILABLE python3-pyside6
```

Decision: the GUI lane is **`python3-pyside6` from the Fedora repos** (Qt 6.11,
Wayland-native) — `dnf install python3-pyside6` goes into the station install
recipe; pip `PySide6-Essentials` wheels remain dev/CI-only and are never
vendored. Headless smoke: `QT_QPA_PLATFORM=offscreen gd gui --selfcheck`.

Smoke (same container, after `dnf install -y python3-pyside6 python3-pyyaml`,
repo mounted read-only):

```text
fedora:45 (linux/amd64) smoke 2026-10-26T23:35:26Z
gui selfcheck ok — apps=41, bundles=9, doctor checks=17, platform=offscreen
gd 0.4.0-m3
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
| flarq | dnf | **build** | MISSING (glob empty) — **resolved: dnf via fldigi** (see Post-audit correction) |
| flwrap | dnf-verify | **build** | MISSING (glob empty) |
| xygrib | dnf-verify | **build** | MISSING (glob empty) |
| osm-navit | dnf-verify | **build** | MISSING (glob empty) |
| cheese → **snapshot** | dnf (cheese) | dnf (snapshot) | cheese retired from Fedora; GNOME Snapshot available (51.0-2) |
| hamlib packages | [hamlib] | [hamlib, python3-hamlib] | bindings package is `python3-hamlib` (not `hamlib-python`) |

## Notes for later phases

- **wine 11.20** is present on x86_64 but currently from `updates-testing` (F45 just GA'd) —
  the kit builder must record the exact NVR + repo in the manifest (G5).
- **ffmpeg** requires RPM Fusion release RPMs *before* kit download; the builder installs them
  from the pinned mirrors (user-supplied URL, verified 2026-10-26 — redirect → FCIX mirrors):

  | Artifact | URL (mirrors.rpmfusion.org) | SHA-256 | Size |
  |---|---|---|---|
  | free release | `https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-45.noarch.rpm` | `a120c2a2c70ec2c8ad461908aacf316998b03f6b2156c143b772052972e80a95` | 11,589 B |
  | nonfree release | `https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-45.noarch.rpm` | `0cb381899827136c92be41a068e6177982bda36ce594729c0bf6f4fa485a15a9` | 11,677 B |

  Verified in-container (`fedora:45`, linux/amd64, 2026-10-26): both install as
  `rpmfusion-free-release-45-0.2.noarch` / `rpmfusion-nonfree-release-45-0.2.noarch`; ffmpeg then
  resolves to `ffmpeg-9.0.2-1.fc45` from the **rpmfusion-free** repo.
- **Java:** only `java-25-openjdk` / `java-latest-openjdk` remain; YAAC recipe pins
  `java-latest-openjdk-headless` (or the 25 equivalent at kit time).
- **php-cli** is available — JS8Spotter's local service needs no extra repo.
- `pat`, `meshtastic`, `gridtracker` absent as designed (tarball / vendored-wheels strategies).

## Post-audit correction (2026-10-26, license-research pass)

- **flarq — RESOLVED 2026-10-26** — Fedora's `fldigi` package **Provides: flarq = 4.2.13** (Fedora
  mdapi), i.e. `dnf install fldigi` ships flarq; the `MISSING flarq` result above only meant "no
  package *named* flarq". **Verified in-container (`fedora:45`, aarch64, 2026-10-26):**
  `dnf repoquery --whatprovides flarq` → `fldigi-0:4.2.13-1.fc45`; `dnf install -y fldigi` then
  `test -x /usr/bin/flarq` → present. Applied: the pinned-source **build** recipe became a **dnf**
  recipe installing the `fldigi` package (the file stays — one recipe per app is a test contract,
  `tests/test_registry.py`); registry entry is dnf/fldigi with `cmd: flarq`. Same-family note:
  flwrap remains genuinely absent (no Provides found) — build stands.
  Details: `docs/audit/third-party-licenses.md` (Flags #4).
- Full license inventory for every catalog app: `docs/audit/third-party-licenses.md`.

## M4 systemd unit verify (2026-10-27)

`tools/systemd-analyze-verify.sh` renders the current registry with
`gd units install` (on the dev box: 2 services + 9 targets; 29 unresolved
apps get no unit by design, 10 wine-hub/cli-tool skipped) and runs
`systemd-analyze verify` inside fedora:45 — systemd **262.1-1.fc45**:

```
verify platform: linux/arm64
stubbed /usr/bin/rigctld (for verify)
stubbed /usr/bin/java (for verify)
systemd-analyze verify rc=0
```

Environment notes (both are tool concerns, not unit defects):
- Under amd64-on-Rosetta, verify's ExecStart probe returns EINVAL for every
  command, including stock `/usr/bin/true` (fexecve quirk). The tool picks
  the native arch (`uname -m`); the systemd version is identical either way.
- The first verify run caught a real unit-design bug: a service `After=` its
  target while the target `After=` its members is an unfixable ordering
  cycle. The renderer now emits `BindsTo=` only on the service side —
  ordering stays one-directional (target `Wants=`+`After=` members).
