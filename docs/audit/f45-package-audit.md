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

## M6a build-deps audit (2026-10-29, `tools/audit-f45-build-deps.sh`)

Method: `dnf list available` per candidate build dep inside `fedora:45`
`linux/amd64` (same as P0), plus `rpm -q` for installed-packages (the P0
lesson generalizes: `dnf list available` HIDES installed packages, so a
MISSING row must be cross-checked before believing it).

```text
image: fedora:45 (linux/amd64)  date: 2026-10-29T18:17:15Z  container-arch: x86_64
--- base toolchain ---
AVAILABLE gcc gcc-c++ make cmake patch git-core
--- qt5 ---  AVAILABLE qt5-qtbase-devel qt5-qtmultimedia-devel qt5-qtserialport-devel qt5-qttools-devel
--- qt6 (WSJT-X Improved / "WS" needs Qt6, per xsnrg/ws-copr ws.spec) ---
AVAILABLE qt6-qtbase-devel qt6-qttools-devel qt6-qtserialport-devel qt6-qtmultimedia-devel
AVAILABLE qt6-qtwebsockets-devel qt6-qtsvg-devel
--- audio ---  AVAILABLE alsa-lib-devel portaudio-devel pulseaudio-libs-devel
--- dsp/ham --- AVAILABLE fftw-devel hamlib-devel boost-devel asciidoc glfw-devel glew-devel volk-devel
MISSING libusb-devel   → renamed: F45 package is libusb1-devel (AVAILABLE 1.0.30-2.fc45)
--- sdr ---   AVAILABLE rtl-sdr-devel;  MISSING librtlsdr-devel (wrong name)
--- fltk ---  AVAILABLE fltk-devel fltk-fluid libsamplerate-devel  (fltk 1.4.5; fltk1.3 compat exists)
--- imaging/geo --- AVAILABLE libpng-devel bzip2-devel proj-devel glib2-devel gtk3-devel
                     gettext-devel libcurl-devel sqlite-devel fontconfig-devel freetype-devel ncurses-devel
MISSING openjpeg2-devel → renamed: F45 package is openjpeg-devel (2.5.4-5.fc45, updates-testing)
MISSING zlib-devel      → renamed: F45 package is zlib-ng-compat-devel (2.3.3-6.fc45)
--- station-side extraction ---
MISSING libarchive-tools (Debian name) → F45 package is bsdtar (AVAILABLE 3.8.9-2.fc45, provides /usr/bin/bsdtar)
AVAILABLE cpio unzip rpm-build;  libarchive libs already INSTALLED
```

Findings folded into recipes (M6a):
- **Build-dep renames on F45:** `libusb1-devel` (not libusb-devel), `openjpeg-devel`
  (not openjpeg2-devel; currently updates-testing — the kit builder must enable
  updates-testing on F45 until it reaches the updates repo), `zlib-ng-compat-devel`
  (not zlib-devel), station-side extractor package = **`bsdtar`** (not
  libarchive-tools; `cpio`/`unzip` available as fallbacks).
- **wsjtx-improved** is now "WS" v3.2.1 by DG2YCB, **Qt6**-based, GPL-3.0-or-later,
  published as SF source drops (no git releases). Fedora COPR packager
  (xsnrg/ws-copr ws.spec) corroborates version + BuildRequires — used as the
  authoritative build_deps list.
- **License flags resolved at pin:** Flags #1 qttermtcp → upstream (g8bpq) ships
  **no license file at all** (repo has no LICENSE/COPYING/README) → recorded
  `LicenseRef-Unknown`; redistribution caveat goes in third-party-licenses.md.
  Flags #3 dump1090 → FlightAware LICENSE states **GPL v2 or later** →
  GPL-2.0-or-later (wiedehopf fork identical wording).
  flwrap COPYING → GPL v2 (w1hkj suite); gridtracker (Flags #2) → **BSD-3-Clause**
  (GitLab gridtracker.org/gridtracker2 LICENSE, verified 2026-10-29).
- **Pin-anchoring notes:** SDR++ ships a rolling "nightly" release since 1.0.4 →
  pinned by master commit `f10fb98af8b80de01de94e60c17608e4281e2515` (2026-10-23).
  wsjtx-improved/flwrap/osm-navit pin upstream-hosted archives (`archive_url`)
  where upstream digests exist (navit source asset carries an official sha256).
  paracon is a Python zipapp (build = zipapp packaging, not compilation).
  meshtastic CLI pinned by PyPI version (2.7.11) — wheels enumerated at M6c.

## M6d full-strategy gate build (2026-10-29) — PASS

`build-kit.sh --no-pack` with all 19 apps (every strategy, zero PIN-ME):

```text
manifest : 1859 files (sha256sum -c PASS; file-count matches)
rpms     : 891 (dnf closure incl. every build app's runtime deps)
built    : 9/9 kit/opt/<id>/ (ardop, dump1090, flwrap, osm-navit, paracon,
           qttermtcp, sdrpp, wsjtx-improved, xygrib)
artifacts: 7/7 kit/tarball/<id>/ (sha256-verified at download)
wheels   : 14 for meshtastic (vendored for python3.14; cp314 wheels included)
static   : 2 payload slots (meshtastic-web, commstatone — empty by default)
apps     : 19 · staging 1.8G
```

Build-fix ledger (each fix lives in the recipe's build_cmd/build_deps — the
recipes are the record; round-trip + full-build iterations caught them):

| App | F45-toolchain failure | Fix (in recipe) |
|---|---|---|
| ardop | gcc C23 default rejects K&R decls (`SendData`); rawhid pointer-fd misuse now errors | `CFLAGS=-std=gnu17 -Wno-error=int-conversion -Wno-error=implicit-function-declaration -Wno-error=incompatible-pointer-types` |
| dump1090 | view1090 needs curses; Makefile's `DUMP1090_CFLAGS` carries `-Werror` (format-truncation trips) | `ncurses-devel`; rebuild `DUMP1090_CFLAGS` without `-Werror` |
| flwrap | F45 ships fltk 1.4; 1.3.6-era configure fails against it | fltk1.3 compat stack (`fltk1.3-devel`, `FLTK_CONFIG=fltk1.3-config`) |
| osm-navit | REQUIRED `protobuf-c`; build downloads an OSM sample map mid-compile | `protobuf-c-devel`; `-DSAMPLE_MAP=n` |
| qttermtcp | .pro requires Qt serialport+multimedia modules | `qt5-qtserialport-devel qt5-qtmultimedia-devel` (+ runtime rows) |
| sdrpp | optional SDR drivers are REQUIRED pkg-config deps (libairspy, rtaudio) | `-DOPT_BUILD_*_SOURCE=OFF` (RTL-SDR only); `rtaudio-devel` |
| wsjtx-improved | CMake 4 rejects ancient `cmake_minimum_required`; reserved target name `install`; docs need asciidoctor; manpages need a2x; full `-j` OOM-kills cc1plus | `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`; sed-rename the custom `install` target; `-DWSJT_GENERATE_DOCS=OFF`; `asciidoc`; `-j2` |
| xygrib | opengribs 1.2.6.1 hunts removed `proj_api.h` — cannot build on proj >= 8; openjpeg.h in versioned subdir | **source pivot** to the Debian-packaged continuation `frfa/XyGrib` v1.3.1 (modern proj.h, GPL-3.0 unchanged); `-DOPENJPEG_INCLUDE_DIR/LIBRARY` |

Tooling lessons (folded into the builder):
- **Rosetta (linux/amd64 emulation) lacks `statx`** — GNU tar fails with
  ENOSYS ("Cannot stat/open: Function not implemented") inside the container
  on ANY filesystem; same syscall family as the M4 fexecve quirk. Builder
  extracts with `python3 -m tarfile` and packs via python tarfile + zstd;
  build scratch rides a host bind mount (virtiofs).
- **bash `read` collapses consecutive IFS-whitespace tabs** — empty plan
  fields shifted columns and a sha256 ended up as a curl host. Plan rows now
  join with `\x1f` (non-whitespace).
- **fedora:45 base has no python3**; the builder installs python3-pip (+ bsdtar,
  zstd) in preflight.
- **meshtastic CLI 2.7.11 caps Requires-Python <3.15** while F45 ships python
  3.15 — the recipe vendors wheels for the **python3.14** alternate stack
  (AVAILABLE on F45; pip comes from venv+ensurepip — no python3.14-pip package).
- Download verified checksums at fetch time for all 7 artifacts; the yaac
  "latest" URL is checksum-pinned (drift fails the build loudly).



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
