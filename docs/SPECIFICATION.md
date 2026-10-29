# Grid Down — System Specification

**Version:** 0.3 · **Status:** Approved draft · **Date:** 2026-10-26
**Target platform:** Fedora 45 Linux, x86_64, GNOME/Wayland, PipeWire

---

## 1. Goals & constraints

1. **G1 — One control interface.** A single Control Center (PySide6 GUI) plus a `gd` CLI manages
   installation, configuration, launch and teardown of ~40 amateur-radio applications.
2. **G2 — Air-gap integrity.** Everything must be *installable* and *runnable* with zero network
   connectivity. Kits are built online; installation and operation are offline.
3. **G3 — No app forks.** Adapters write each application's **native** config format. Backups,
   atomic writes, and diffs are mandatory.
4. **G4 — Single owner of shared resources.** One `rigctld` (localhost:4532) and one PipeWire
   audio graph are owned by the suite; all radio apps consume them.
5. **G5 — Reproducible kits.** Every artifact in an offline kit is pinned by SHA-256 and recorded
   in a manifest; the manifest is part of the kit.
6. **G6 — User-supplied artifacts stay user-supplied.** VARA family + VarAC installers/licenses are
   never redistributed; the kit build fails closed when they are missing.

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│ Control Center (PySide6)         gd CLI (argparse)       │
├──────────────────────────────────────────────────────────┤
│ core: profile · registry · supervisor · doctor · media   │
│       rig · audio · winehub · adapters/*                 │
├──────────────────────────────────────────────────────────┤
│ install/: bootstrap.sh · lib/common.sh · recipes/*       │
│          bundle-builder/ (fedora:45, online side)        │
└──────────────────────────────────────────────────────────┘
```

- **Registry** — `control/registry/apps/*.yaml`, one file per application (schema §6.2).
- **Recipes** — `install/recipes/*.yaml`: how to obtain/install each app (strategy per §8).
- **Adapters** — `control/core/adapters/*`: per-app config writers (native formats only, G3).
- **Supervisor** — launches, monitors, and tears down app processes and localhost services.
- **Doctor** — preflight checks: udev/dialout, uucp lock dirs, PipeWire graph, rigctld ports,
  Wine prefix health, air-gap violations (apps with known phone-home features).

## 3. Scope

**In scope:** HF digital (Winlink/VARA/VarAC, JS8Call, FL suite, WSJT-X family), packet/APRS
(direwolf, QtTermTCP, Paracon, YAAC, ChatterVox), voice/SSTV (QSSTV), monitoring
(GridTracker, JS8Spotter, CommStat One, Artemis, SDR++/dump1090), offline reference & weather
(Kiwix, OSM/Navit, XYGrib), AV utilities (Audacity, ffmpeg, sox, GNOME Snapshot, conky), LoRa/Meshtastic
management (§6.11), and the offline kit/bootstrap pipeline.

**Non-goals:**

- **EchoLink / Qtel / SvxLink EchoLink module** — excluded by design: its directory/reflector model
  requires internet connectivity, violating G2. A future appendix could document a LAN-only
  SvxLink deployment *without* the EchoLink module.
- **Raw LoRa SDR demodulation research tools** (gr-lora_sdr et al.) — future appendix; v1 LoRa
  support is the Meshtastic stack (§6.11).
- Satellite tracking/gateways beyond the VARA-sat recipe; Windows-only apps without a Wine or
  native-Linux story are out of scope.

## 4. Station profile

One YAML file describes the station; every adapter reads from it. Default template:
`profiles/default.yaml`. Active profile: `$GRID_DOWN_PROFILE`, else
`~/.config/grid-down/profiles/<id>.yaml`, else repo default.

```yaml
profile: { id: default, callsign: "", grid: "" }
rig:
  model: ""                 # numeric hamlib id from `rigctl -l` (e.g. 3073 = TS-480);
                            # empty = Dummy backend (bench-safe; Doctor warns)
  path: /dev/ttyUSB0
  baud: 38400
audio:
  backend: pipewire
  rig_capture: ""           # physical rig capture source (P3b `gd audio wire` RX half)
  rig_playback: ""          # physical rig playback sink (P3b `gd audio wire` TX half)
  loopback_roles: [hf]      # roles the rig loopback serves (hf | packet | voice)
  presets:                  # per-app launch-cmd placeholders, see §6.6/§6.4 M2 note
    ardop: { capture: grid_down_hf_out.monitor, playback: grid_down_hf_in }
supervisor: { backend: auto }   # direct | systemd | auto (§6.5; systemd first: `gd units install`)
internet: { present: false }   # air-gap master switch
winlink:
  secure_password: ""
  service_codes: [PUBLIC]
mesh:                        # §6.11
  device: { type: serial|tcp|ble, path: /dev/ttyUSB1, host: null }
  node:  { long_name: "", short_name: "", region: "", modem_preset: "", channel_url: "" }
  daemon: { enabled: false } # meshtasticd (LoRa HAT) instead of USB device
```

## 5. Install pipeline

- **Online side:** `install/bundle-builder/build-kit.sh` runs in a `fedora:45` container, resolves
  every recipe into artifacts (rpms, tarballs, git-built binaries, vendored wheels, static
  bundles), writes `kit/manifest.sha256` (path + sha256 + source URL + version), packs
  `grid-down-kit-<date>.tar.zst`.
- **User-supplied staging:** VARA installers/licenses and any license-keyed artifacts are dropped
  into `install/kit-import/` before kit build. Builder fails closed (G6) if missing.
- **Air-gap side:** `bootstrap.sh --kit <dir>` verifies the manifest, then installs via
  `dnf --cacheonly` from the kit's local repo (`createrepo_c`), extracts tarballs, runs
  per-recipe postinst (systemd units, udev rules, Wine prefix from cached prefix tarball).
- **Idempotency:** `bootstrap.sh` is safe to re-run; state in `/var/lib/grid-down/state.json`.

## 6. Data model

### 6.1 Registry entry (`control/registry/apps/<id>.yaml`)

```yaml
id: js8call
name: JS8Call
category: hf                  # core|hf|winlink|packet|aprs|voice_sstv|map_weather|reference|monitor|mesh|av
ui: { tagline: "..." }        # one-liner shown in Control Center
install:
  strategy: build             # dnf|dnf-verify|build|tarball|wine|pip-vendored|static|managed-service|cli-tool
  recipe: js8call             # matches install/recipes/<id>.yaml
  packages: [js8call]         # dnf names, when strategy involves dnf
launch:
  via: managed-service        # managed-service|desktop-app|local-service|cli-tool|wine-hub
  cmd: js8call
config:
  adapter: js8call            # control/core/adapters/<adapter>.py
  native_path: ~/.config/JS8Call/JS8Call.ini
```

### 6.2 Config adapters

Each adapter: `read() -> dict`, `apply(profile, dry_run) -> diff`, with mandatory
`backup()` (timestamped copy) and **atomic write** (tmpfile + `os.replace`), preserving app-native
formatting where practical. `gd config apply <app>` prints the unified diff before writing.

### 6.3 Session bundles (`control/registry/bundles.yaml`)

| Bundle | Members (typical) |
|---|---|
| winlink | rigctld, direwolf, ardop, VARA (hub), pat, QtTermTCP |
| js8 | rigctld, js8call, JS8Spotter (localhost) |
| packet | rigctld, direwolf, QtTermTCP, Paracon, ChatterVox |
| ft8 | rigctld, wsjtx, GridTracker |
| aprs | direwolf, YAAC, QtTermTCP |
| sstv | rigctld, qsstv |
| wx | XYGrib, (GRIB files staged offline) |
| sdr | SDR++, dump1090, Artemis |
| mesh | meshtasticd (HAT mode) or USB node, meshtastic-web (localhost) |

### 6.4 Shared resources (suite-owned)

- **Rig:** single `rigctld` at `127.0.0.1:4532`, started by the supervisor before any radio app;
  every rig-capable app is configured (via adapter) to use `rigctld:4532` instead of the serial
  device. Only rigctld touches the serial port (G4).
- **Audio:** PipeWire graph with named loopback sinks/sources per role (HF in/out, packet in/out,
  voice in/out). Apps bind to loopbacks via adapters; presets live in the profile (`audio.presets`).

**M2 implementation (2026-10-26):** `control/core/audio.py` owns the endpoint namespace —
`grid_down_{hf,packet,voice}_{in,out}` (in = sink apps play into, out = source apps capture
from), created/destroyed idempotently via `gd audio up|down` (pactl / pipewire-pulse) and
audited by `gd audio status` + Doctor. Nothing outside the module may create or destroy
`grid_down_*` nodes (G4). Apps bind to endpoints through registry launch-cmd placeholders
(e.g. `ardopc 8515 <capture> <playback>`), resolved per-app from `audio.presets` (profile)
over `DEFAULT_PRESETS`; unresolved placeholders are refused naming the exact profile key.
**P3b (2026-10-28) wires the rig interface in:** `gd audio wire|unwire` load module-loopbacks
from `audio.rig_capture` into the owned 'out' endpoints (RX) and from the owned 'in' monitors
into `audio.rig_playback` (TX), per `audio.loopback_roles` (default hf). Teardown only ever
matches loopbacks whose args name the `grid_down_` namespace — a user's own loopback or null
sink is never touched (proved by tests and by the VM driver's stage A4). `gd audio plan`
prints the endpoint graph and the loopback rows (planned or pending) either way.
- **Wine hub:** one prefix at `/opt/grid-down/wine-prefix` for the VARA family + VarAC;
  `gd winehub` wraps start/stop and serial/COM mapping. Installers/licenses never leave
  `install/kit-import/` (G6). **M5 (0.6.0-m5) ships the plumbing:** `core/winehub.py`
  (single source for the prefix path) with `gd winehub status|start|stop|map|unmap` —
  status = prefix + wine binary + `dosdevices/comN` map + running pids (`<state>/winehub.json`);
  start/stop = detached `wine <exe>` with `WINEPREFIX` set, SIGTERM→SIGKILL teardown and
  stale-pid cleanup; COM mapping = validated com1–com32 symlinks. Every verb refuses cleanly
  when the prefix or wine is missing. **P3 adds install:** `gd winehub init` (`wineboot -i`,
  the dev path — the kit postinst still unpacks the cached tarball) and `gd winehub install
  <app>`: the user-supplied `install/kit-import/` artifact (.exe, or the .zip wrapping it)
  runs in place with Inno `/VERYSILENT` switches, then the per-app probe decides success
  (recipe `wine.exes` candidates walked under `<prefix>/drive_c`; `status` and the install
  state columns report `installed`/`prefix`/`missing`). Licenses stay operator-applied
  in-app (G6): registration is per program, and unregistered installs are fully functional
  with VARA's trial delay — enough for the P3 acceptance path. **P3 progress:**
  the real installers are staged (`kit-import/` carries VARA HF v4.9.0, FM v4.4.0
  and the portable VarAC v16.1.8 zip from their official sources, sha256s in its
  README; SAT/Chat/Term stay operator-supplied) — which surfaced that VarAC ships
  no installer at all, so recipes now declare `wine.installer: setup|portable`
  (portable = unpacked into `wine.install_dir`, never run). And the first config
  adapters landed (`core/adapters`, §6.2): `gd config show|apply <app>` reads and
  applies the VARA family's exe-adjacent INIs inside the prefix — the path comes
  from the install probe (no pinned path to drift), registration-shaped values
  are masked in reads (G6), and writes are limited to the keys the recipe
  declares (`wine.ini_set:`), with the mandatory timestamped backup, atomic
  write and printed diff; the exact VARA.ini key names get pinned into those
  recipes at the live E2E, same discipline as the `exes` candidates.
  **P3b (2026-10-28) closes the arm-testable remainder:** `gd winehub bridge <n>`
  maps COMn to one end of a pty pair through this module's own `map_serial`/`unmap`
  and relays to a peer pty or TCP listener (core/ptybridge.py, pure stdlib — no
  wine, no socat; the pty runs raw, since a COM port has no line discipline), and
  `GRID_DOWN_WINE_PREFIX` points the default prefix at a fixture directory so the
  COM map is rehearsal-able without wine. Both are validated on the aarch64 VM
  (docs/phase3b-vm-runbook.md). **Remaining P3:** the on-air end-to-end (§10),
  still gated on an x86_64 carrier.

### 6.5 Supervisor & services

Managed services (rigctld, direwolf, meshtasticd, dump1090, localhost HTTP/PHP servers) run as
systemd user units with `BindsTo` on a bundle target (`grid-down-<bundle>.target`). Control Center
starts/stops bundles; processes outliving their target are treated as errors by Doctor.

**M1 implementation (2026-10-26):** `control/core/supervisor.py` ships the *direct* backend —
process-group spawn (`start_new_session`), JSON instance table at
`$XDG_STATE_HOME/grid-down/supervisor.json` (override: `GRID_DOWN_STATE_DIR`), per-app logs under
`.../logs/<app>.log`, atomic state writes, SIGTERM→SIGKILL teardown in reverse bundle order.
Suite rules enforced: `hamlib` (rigctld) is a singleton auto-started before any rig-dependent app
(an app is rig-dependent when it appears in any bundle that includes hamlib — data-driven from
§6.3) and stopped only once the last dependent is down; wine-hub and cli-tool launches are
refused with operator guidance. **M2 (2026-10-26):** rigctld flags are built from the profile
`rig:` section by `Supervisor.rig_argv()` — `gd rig` previews the exact argv (Dummy backend +
note when `rig.model` is empty; non-numeric ids refused); launch-cmd `<placeholders>` resolve
through `audio.presets` (§6.4 M2 note) and are refused naming the exact profile key when
unresolved. The systemd user-unit backend above (bundle targets + `BindsTo`)
is now landed; `Supervisor` remains the backend-neutral API both
frontends share.
>
> **M4 note (2026-10-27):** the backend landed as `control/core/systemd.py`
> (`SystemdSupervisor(Supervisor)`, 0.5.0-m4) — this section's Phase 2
> completion target. `gd units install` renders the whole set:
> `grid-down-<app>.service` per supervisable app and `grid-down-<bundle>.target`
> per bundle. Services carry `BindsTo=` on each containing target (stop
> propagation; apps in several bundles are bound to all of them, preserving
> today's `stop_bundle` semantics). Ordering is one-directional — the target
> is `Wants=`+`After=` its members; the reverse edge would be an ordering
> cycle (`systemd-analyze verify` rejects those — found and fixed during the
> container proof). `hamlib` renders unbound (suite-owned singleton, §6.4);
> wine-hub and cli-tool apps get no units, matching the direct backend's
> refusals. systemd is instance truth in that mode — no supervisor.json
> instance writes. `--backend {auto,direct,systemd}` on `gd launch/stop/status`
> and the `supervisor.backend` profile key (§4) select the backend; `auto` =
> systemd only when a user bus is reachable and units are installed. Doctor
> (§6.7) grew the unit check: WARN on drift vs a fresh render, FAIL on a
> service outliving every bound target. Container proof:
> `tools/systemd-analyze-verify.sh` (fedora:45, systemd 262, rc=0 — see
> `docs/audit/f45-package-audit.md`). Real `systemctl --user` acceptance is
> the P2 Fedora GUI-VM pass (§10).

### 6.6 Local services & ports

| Port | Service |
|---|---|
| 4532 | rigctld (TCP, loopback) |
| 8001 | direwolf KISS (loopback) |
| 8090 | CommStat One (static) |
| 8091 | JS8Spotter (PHP built-in server) |
| 8092 | Meshtastic Web (static + Web Serial) |
| 8515 | ARDOP TNC |

### 6.7 Doctor checks (summary)

dialout/udev groups · serial/SPI device presence (incl. Meshtastic cp210x/ch341 VID:PID) ·
PipeWire graph health · rigctld port conflicts · Wine prefix sanity · kit manifest verification ·
**air-gap violations**: any enabled phone-home feature (notably Meshtastic MQTT uplink) when
`internet.present: false`.

M1 (2026-10-26) implements via `control/core/doctor.py`: platform sanity, dialout group, serial
device presence, PipeWire tooling, §6.6 port occupancy, supervisor state hygiene (stale
instances), profile sanity, wine-prefix presence, air-gap switch report. **M2 adds:** rig-model
sanity (warns on Dummy fallback and non-numeric `rig.model`) and the owned-endpoint audit
(missing `grid_down_*` endpoints → warn with `gd audio up`), which retires the deep PipeWire
graph audit from the deferred list. **M5 upgrades the wine check** to the wine hub (§6.4):
prefix presence + wine binary + COM-map report via `core/winehub.py` (skip while the prefix
is absent, warn when the prefix exists but wine is not installed). Deferred with an explicit
`skip` line in every report: kit manifest verify, Meshtastic VID:PID probe, per-app
phone-home scan.

### 6.8 Offline kit contents

`kit/` = local dnf repo (rpms + rpmfusion) · `/opt` payloads (built binaries, tarballs) ·
vendored wheels · static bundles (CommStat One, Meshtastic Web, JS8Spotter) · VARA prefix tarball ·
ZIM/GRIB/OSM data staged by the operator · `manifest.sha256` (path, sha256, url, version) ·
`bootstrap.sh` + install lib.

### 6.11 LoRa / Meshtastic subsystem

- `meshtastic` Python CLI + library: vendored wheels in the kit; manages devices over USB/serial,
  TCP, BLE. Profile `mesh:` block applied via `meshtastic configure` (node names, region, modem
  preset, channel URLs; PSKs stay local).
- `meshtasticd`: Linux-native LoRa HAT daemon; upstream RPM pinned in kit; systemd unit managed by
  the supervisor; off by default (USB-handset stations don't need it).
- `meshtastic-web`: official web client served on localhost:8092 (Web Serial in-browser).
- Dashboard tiles: node list, SNR/RSSI, battery via CLI JSON polling.
- Air-gap notes: MQTT uplink disabled + flagged by Doctor when air-gapped; everything else is
  pure LoRa/local (spec v0.2 added this subsystem; v0.3 removed EchoLink — see §3).

## 7. GUI (Control Center)

PySide6. Views: Dashboard (bundle tiles, rig/audio status), Catalog (registry-driven app list,
install state), Bundles (start/stop sessions), Profiles (form-driven YAML editing), Doctor
(check results + fixes). Offscreen-testable in CI (`QT_QPA_PLATFORM=offscreen`).

> **M3 note (2026-10-26):** Control Center v1 (`gd gui`, 0.4.0-m3) ships all five views:
> Dashboard (station/rig/audio/instances/doctor + audio up/down actions), Catalog (filterable
> table over the 41-app registry), Bundles (start/stop tiles + confirm dialog + notes pane,
> calling `Supervisor.launch_bundle/stop_bundle` in-process), Doctor (color-coded checks +
> copy-as-JSON), Profiles (YAML pane + rig model/path/baud (`save_rig`), station
> identity — callsign/grid (`save_station`), audio — backend / rig_capture /
> ardop capture-playback presets (`save_audio`), and mesh — device
> type/path/host, node identity (long/short name, region, modem preset,
> channel URL) and the meshtasticd daemon switch (`save_mesh`; the node config
> itself is applied later by the Phase 4 `meshtastic configure` adapter);
> atomic writes, never the repo
> template). Core is reached through a
> dependency-injected service layer
> (`control/gui/services.py` — pure Python, fakes in tests); every core call runs in a
> worker thread; any failing section degrades to "unavailable" instead of killing the UI.
> PySide6 on the target comes from the Fedora repos (`python3-pyside6`, see
> `docs/audit/f45-package-audit.md`); pip wheels are dev/CI-only. Remaining profile-form
> sections (mesh), install-state columns, winehub controls, and the
> tray/launcher stay out of scope.
>
> **M5 note (2026-10-26):** 0.6.0-m5 ships the last P2 controls. Catalog gains the
> `install` column — one probe per registry app from `core/install_state.py`
> (`rpm -q` batched for dnf apps, `shutil.which` on the launch binary for
> build/tarball/pip-vendored, `<app_root>/<id>` docroot for static, shared-prefix
> check for wine; tooltip carries the probe detail) — and the Wine hub panel
> (prefix/wine/COM status line + start/stop per wine-hub app, buttons gated on
> prefix+wine, same worker-thread pattern as Bundles). The tray lands as an
> availability-gated `TrayController` (`gui/tray.py`: Show/Hide/Quit, tooltip =
> doctor tally, clean no-op headless or with `gd gui --no-tray`; no
> appindicator dependency), and `packaging/` + `tools/install-launcher.sh`
> install a user-local `.desktop` launcher + SVG icon. **P2 remaining work is
> the §10 Fedora GUI-VM acceptance pass only;** VARA end-to-end (per-app
> install probes inside the prefix, licenses, on-air session) is Phase 3 (§10).

## 8. App catalog & install strategies

Registry entries (41 apps) live in `control/registry/apps/`; install strategies:

Post-audit state (2026-10-26; `docs/audit/f45-package-audit.md` is authoritative):

- **dnf** — hamlib (+python3-hamlib), fldigi (ships flarq), direwolf, wsjtx, js8call, flamp, flmsg, fllog,
  qsstv, kiwix-desktop, audacity, sox, conky, snapshot, ffmpeg (needs RPM Fusion release rpms),
  wine 11.20 (x86_64), winetricks, java-25-openjdk (YAAC runtime), nodejs, rtl-sdr, php-cli,
  createrepo_c, python3-pip
- **build** (pinned git sources in fedora:45 container) — ardop, wsjtx-improved, qttermtcp,
  paracon, sdrpp, dump1090, flwrap, xygrib, osm-navit
- **tarball** — pinned upstream artifacts: pat, chattervox (npm), yaac (jar), artemis (.rpm,
  AresValley GPL-3.0 v4.2), gridtracker, js8spotter, meshtasticd (RPM), commstatone (static)
- **wine** — user-supplied installers into shared prefix: vara-hf, vara-fm, vara-sat, vara-chat,
  varac, vara-term
- **pip-vendored** — meshtastic (wheels in kit)
- **static** — meshtastic-web (served locally)

## 9. Security & privacy

Kit integrity via SHA-256 manifest; profile secrets (Winlink password, mesh PSKs) stored with
user-only permissions and never committed; no telemetry anywhere in the suite; kit builds embed
source URLs for every artifact for auditability.

## 10. Testing & acceptance

- `tests/` validate registry/profile/bundle/recipe consistency on every commit.
- Kit build runs in clean `fedora:45` container (this Mac); air-gap install validated in a
  Fedora 45 GUI VM (any hypervisor; x86_64 or aarch64) and the `fedora:45` container
  (headless), with networking removed.
- Per-phase acceptance: P3 = Winlink session end-to-end in Wine; P4 = mesh device-profile apply
  round-trip + Artemis RPM launch; P5 = full offline rebuild from USB kit on a clean host.

## 11. Phased rollout

| Phase | Scope | Acceptance |
|---|---|---|
| P0 | Scaffold, spec v0.3, F45 package audit | audit doc committed; tests green |
| P1 | Installer lib + kit builder skeleton; dnf-only round-trip | direwolf+fldigi install from kit in VM |
| P2 | GUI + CLI v1; rigctld + PipeWire ownership | bundle start/stop from Control Center |
| P3 | VARA/Wine hub; Winlink E2E | VARA session over loopback to pat |
| P4 | Remaining apps + mesh recipes/adapters | mesh profile round-trip; Artemis launch |
| P5 | USB kit, bootstrap hardening, drills | clean-host offline rebuild |

## 12. Open items

1. VARA/VarAC installer versions + licenses (user-supplied, pre-P3).
2. Kiwix ZIM selection, OSM extract region, GRIB staging (pre-P4).
3. Meshtastic hardware style: USB handset (default) vs LoRa HAT + meshtasticd.
4. ~~Fedora 45 package audit~~ **Resolved 2026-10-26** — see `docs/audit/f45-package-audit.md`
   (wine confirmed on x86_64; 6 dnf flips, 4 build flips; cheese retired → GNOME Snapshot).
5. ~~Project license choice~~ **Resolved 2026-10-26** — suite code is **MIT** (`LICENSE` at the
   repo root, SPDX headers on all source files); bundled third-party attribution is inventoried
   in `docs/audit/third-party-licenses.md` (see §13).

---

## 13. Licensing & third-party attribution (M0)

Grid Down's own code is **MIT** so the suite imposes no copyleft of its own; obligations run per
bundled component only.

- **Inventory** — every third-party component with its SPDX id, verification source, and open
  flags: `docs/audit/third-party-licenses.md`.
- **dnf apps** — Fedora/RPM Fusion binaries carry their license texts; the kit manifest (G5)
  records the RPM `License:` tag + NVR per package at build time.
- **build/tarball apps** — each recipe records an upstream `license:`; the kit build ships the
  upstream LICENSE into `/opt/grid-down/<app>/` and pins source URL + SHA-256 (satisfies GPL
  source-offer obligations).
- **user-supplied** — VARA family / VarAC installers and licenses are never redistributed (G6);
  GridTracker stays fail-closed until its redistribution terms are verified (inventory Flags #2).
- **No invented licenses** — unresolved components carry `license: TBD-VERIFY-AT-PIN` and are
  flagged by Doctor at kit-build time; nothing ships silently unlicensed.


