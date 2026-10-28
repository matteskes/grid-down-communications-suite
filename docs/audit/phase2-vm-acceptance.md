# Phase 2 VM acceptance — GUI + systemd user-unit pass

Spec §11 P2. Runbook: `docs/phase2-vm-runbook.md` · backend: `control/core/systemd.py`
(M4a) + supervisor parity (M4b) · GUI: `control/gui/` (M3a–c). Dev-side evidence
below is already recorded; the human half is the GUI-VM pass (§ VM acceptance).

## Dev-box / container evidence (2026-10-26)

- **Unit render + verify**: `tools/systemd-analyze-verify.sh` — 2 services +
  9 targets on the dev box (29 unresolved apps get no unit by design, 10
  wine-hub/cli-tool skipped); `systemd-analyze verify rc=0` inside
  `fedora:45`, systemd 262.1-1.fc45. Full transcript + the ordering-cycle
  design fix it caught: `docs/audit/f45-package-audit.md` § *M4 systemd unit
  verify*.
- **Backend tests**: 96/96 suite green (systemd backend driven through a fake
  `systemctl` shim; rendering, drift, outliving check, launch/stop parity).
- **Tooling gates**: ruff + black + isort clean (`tools/lint.sh`, pre-commit
  hook installed); selfcheck unchanged: apps=41, bundles=9, doctor checks=18.

## VM acceptance (Fedora 45 Workstation, aarch64 — 2026-10-26)

Status: **ACCEPTED** — all 12 gates pass on a real Fedora 45 GUI VM
(GNOME/Wayland, networking removed at the hypervisor).

Environment: VMPal `Fedora 45 Workstation` VM on Apple Silicon
(Virtualization.framework, 9 vCPU / 16 GB; air-gap = NIC detached via
`vmpal set --network off` — `vmpal exec` rides the guest-tools channel, not
the network, so every gate ran air-gapped). Host: macOS 27.2, arm64. Suite:
`git archive` of HEAD (2f93066) + kit `grid-down-kit-20261027-aarch64.tar.zst`
(525 rpms, built `--forcearch` from the same commit). Pre-pass snapshot:
`pre-P2-pass`. Screen evidence: VMPal screenshots + `vmpal ui` for the tile
click-through.

| # | Gate (runbook §) | Expected | Observed |
|---|---|---|---|
| 1 | Kit install incl. js8call + qsstv (§4) | `install complete`; `rpm -q` ×5 ok | manifest verified: 576 files; 28 packages from the kit's own repo (`gd-kit`, sha256-verified); `direwolf-1.8.1-5.fc45 · fldigi-4.2.13-1.fc45 · hamlib-4.6.5-9.fc45 · js8call-2.3.1-2.fc44 · qsstv-9.5.8-27.fc45` + `python3-hamlib` — all ok |
| 2 | `gd doctor` full pass (§5) | fail=0, real PipeWire | `doctor: 9 ok, 6 warn, 0 fail, 3 skip` (first pass; final `11 ok, 5 warn, 0 fail, 2 skip`); warns are stock-VM hardware items; air-gap seen: `internet.present=false` |
| 3 | `gd audio up` (§5) | 3 `grid_down_*` sinks + 3 sources | **finding #1 fixed first**: after the fix — `created: 5, exists: 1`; pactl: 6 sinks (`hf/packet/voice` × `in/out`) + 6 sources (`_out` ones as `.monitor`); doctor `[ok] PipeWire audio: owned endpoints present` |
| 4 | Direct backend launch/stop (§5) | app up → down; packet partial-start notes | packet: `running: hamlib` + `running: direwolf`; qttermtcp errors (`executable not found`) and later members are not attempted (runbook wording corrected); `stop packet` → both stopped, **rigctld refcount-0**. fldigi itself aborts at startup on F45 aarch64 (upstream — see findings); smoke evidenced with direwolf as in P1 |
| 5 | `gd units install` (§6) | services + 9 targets, daemon-reload ok | **8 services + 9 targets**, `daemon-reload: ok`; skipped 10 (wine-hub/cli-tool); unresolved 23 (outside the kit, by design) — 8 = 5 kit apps + flarq (rides the fldigi RPM) + snapshot (ships with Workstation) + yaac (`java` launcher resolves) |
| 6 | **`BindsTo=` stop propagation on the real bus (§6)** | `gd stop sstv` kills qsstv; target + service inactive | `target grid-down-sstv.target stopped / stopped: hamlib`; `pgrep -a qsstv` → **no processes**; `is-active` → `inactive` ×2; rigctld refcount-0 stop; journal: `Stopping… Stopped… Consumed 470ms CPU over 1min21s` |
| 7 | Log capture (§6) | suite log + `journalctl --user` agree | `logs/hamlib.log` carries rigctld stderr from the systemd run (`Terminating application, caught signal 15` via `StandardError=append:`); `journalctl --user -u grid-down-qsstv.service` shows the same lifecycle; `qsstv.log` exists, empty (app writes nothing) |
| 8 | Auto backend resolution (§6) | `gd status` reports `backend: systemd` | while sstv ran: `2 running, 0 stale, 1 failed (backend: systemd)` + tallies (`bundle sstv: hamlib, qsstv (2/2)`); the `1 failed` is yaac — launcher resolves, jar not installed; never started by this pass |
| 9 | Doctor systemd check (§6) | ok — "current with registry/profile" | `[ok] systemd user units (§6.5): 17 units installed, current with registry/profile (8 services, 9 targets); runtime clean (no service outliving its targets)` — **after finding #2's fix** (before it, false drift) |
| 10 | `gd gui --selfcheck` (§7) | real Wayland window, exit 0 | `gui selfcheck ok — apps=41, bundles=9, doctor checks=18, platform=wayland`, rc=0 (session env exported; see caveats) |
| 11 | Five views (§7) | Dashboard / Bundles / Catalog / Doctor / Profiles all render | all five render with real data (screenshots): Dashboard (station header, resolved rigctld argv, 6 endpoints present, Audio up/down, instances, doctor summary), Bundles (9 tiles × Start/Stop), Catalog (filter + 41-app table), Doctor (color-coded rows + Copy as JSON), Profile (read-only YAML) — **after finding #3's fix** (the live window previously fed only the Dashboard) |
| 12 | **Bundle start/stop from the GUI (§7 — the P2 acceptance)** | sstv start+stop via Bundles tiles | sstv tile → Start → confirm → `running: hamlib (pid 20741)` + qsstv pid 20744, **QSSTV 9.5.8 window live on the GNOME desktop**; tile → Stop → confirm → `stopped: qsstv / stopped: hamlib`, processes gone. js8 tile → partial start by design: js8call + rigctld up (JS8Call windows on screen), js8spotter skipped (not installed); tile → Stop → both stopped |

### Findings (fixed in-repo during this pass)

1. **`module-null-source` does not exist in pipewire-pulse** (1.6.10):
   `pactl load-module module-null-source` → `Failure: No such entity`; M2's
   `audio up` had only ever run through injected fake runners. Owned `_out`
   endpoints are now null sinks captured via their `.monitor` (`source_argv`,
   `_owned_listed`, ardop capture preset → `grid_down_hf_out.monitor`);
   commit `3ca8f95`.
2. **Doctor drift check ignored the station profile**: `gd units install`
   renders through a profiled Supervisor (rig flags shape hamlib's
   ExecStart) but the check built a bare one → every profiled install read
   as "drifted". `run_checks` now threads `profile_data` through; regression
   test pins a profiled render as `installed, current`; commit `285c436`.
3. **The live Control Center window fed only the Dashboard** (no window-level
   snapshot plumbing; MainWindow also never stored `services`, so the added
   autorefresh worker died silently with AttributeError). MainWindow now owns
   the timer/worker and fans every snapshot out to all five pages;
   regression tests added; commits `cc037f9` + `2f93066`.
4. **Upstream, not ours**: `fldigi 4.2.13` aborts at startup on F45 aarch64
   (C++ `std::string` assert), independent of config dir, locale or display
   env. Recorded here; the direct-backend smoke stands on direwolf.
5. Doc corrections riding this pass: runbook package name
   (`pipewire-pulseaudio`), packet partial-start wording, §6 expected unit
   counts, profile/§-example capture preset `.monitor`, GUI notes typo
   ("stoping" → "stopping").

### Notes / caveats

- Unit counts differ dev box vs VM by design: services render for every app
  whose launcher resolves (2 on the bare dev box, **8 on the VM**); targets
  always render for all 9 bundles, omitting unresolved members.
- `hamlib` (rigctld) is the suite-owned singleton: a service with no
  `BindsTo=`, reference-counted at bundle stop — it stops with the last
  rig-dependent app (§6.4). Verified three times (direct, systemd, GUI).
- No lingering/`enable-linger` step: user units run under the active GNOME
  session. The session was left auto-logged-in; its idle lock was suspended
  for the unattended pass (`lock-enabled false`, `idle-delay 0`) — re-enable
  if you want locking back.
- `gd audio plan` always prints the pending view (the CLI never wires the
  `exists` probe; status/doctor do) — cosmetic, noted for M5.
- GUI bundle actions run the supervisor directly (M3c wiring: `services.py`
  builds a plain `Supervisor`); the systemd backend is selected via the CLI
  (`--backend systemd` / auto-resolution) and fully covered by gates 5–9.
  A backend flag for the GUI is M5 scope, not a P2 gate.
- Minor: `gd status` prints the backend annotation only when instances
  exist; the empty-state line omits it.

## M5 VM smoke — Fedora 45 Workstation, aarch64 (2026-10-27)

Status: **ACCEPTED** — `tools/vm-smoke-m5.sh`: 7/7 gates green, then a visual
click-through of the M5 GUI surfaces on the same VM. This closes the last P2
backlog item: the §10 GUI-VM pass now covers M1–M5, and P2 is done.

Environment: same VM and air-gap as the 12-gate pass above (`network off`;
`vmpal exec` rides the guest-tools channel). Suite: `git archive` of HEAD
**247300c** (130,011 bytes, sha256 `b99c1f714cb3e98d0999e28d989a2d2b6ff488d58d2911add0802345e2c38408`,
identical host-side and guest-side) extracted to `/var/tmp/grid-down`. The
P2 kit install is untouched — the 8 installed apps are what the real `rpm`
probe sees. GNOME session signed in for the unattended pass via GDM
auto-login (`AutomaticLoginEnable=True` / `AutomaticLogin=matt`, now
persisted in `/etc/gdm/custom.conf`); PipeWire + pipewire-pulse active under
the session.

### Scripted gates — `sh tools/vm-smoke-m5.sh`, exit 0, `M5-SMOKE: PASS`

| # | Gate | Observed |
|---|---|---|
| 1 | `gd apps list --state` (real rpm) | `install state: 8 installed, 33 missing` |
| 2 | `--json` carries `install_state` in the vocabulary | 41 apps, states ⊆ vocabulary (observed: `installed`, `missing`) |
| 3 | `gd winehub status` with no prefix — clean report, no crash | `prefix: ABSENT (/opt/grid-down/wine-prefix)` · `wine: not on PATH` · `com: none mapped` · all 6 VARA apps `stopped` |
| 4 | `gd winehub start vara-hf` without a prefix | refuses cleanly (`… not installed yet`) |
| 5 | `gd winehub map 5 /dev/null` without a prefix | refuses cleanly |
| 6 | `gd doctor` wine-hub line present (skip while prefix absent) | `[skip] VARA wine hub: /opt/grid-down/wine-prefix not installed yet (kit postinst creates it; 'gd winehub status' for detail)` |
| 7 | Offscreen GUI selfcheck (install column + winehub section, `tray=no`) | `gui selfcheck ok — apps=41, bundles=9, doctor checks=18, tray=no, platform=offscreen` |

### Visual pass on the Wayland desktop (VMPal screenshot + tree/OCR)

- `python3 control/cli/gd.py gui` under the GNOME/Wayland session: the
  "Grid Down — Control Center" window comes up focused with a clean log.
- **Catalog install column**: state cells render per app against the real
  rpm probe — yaac, js8call, fldigi, flarq, hamlib (rigctld), dire wolf,
  snapshot → `installed`; the rest (including all six wine-hub apps) →
  `missing`; count line `41 apps`.
- **Wine hub panel**: group box "Wine hub (VARA family, spec §6.4)" with the
  status line (`prefix ABSENT (/opt/grid-down/wine-prefix) · wine NOT on
  PATH · COM: none`) and six per-app rows — vara-chat [Chat.exe],
  vara-fm [FM.exe], vara-hf [VARA.exe], vara-sat [SAT.exe],
  vara-term [Terminal.exe], varac [VarAC.exe] — each `stopped` with gated
  Start/Stop buttons (disabled without prefix/wine, matching
  `test_catalog_winehub_panel_disabled_without_prefix_or_wine`).
- **Tray**: stock Fedora GNOME ships no AppIndicator extension, so the tray
  is availability-gated and no-ops cleanly (selfcheck `tray=no`; no error in
  the GUI log) — the designed behavior on stock/headless desktops.

**P2 closed**: M1–M4 by the 12-gate pass (2026-10-26), M5 by this smoke
(2026-10-27). Next: P3 — VARA/Wine Hub end-to-end with user-supplied
installers, and the Winlink session bundle end-to-end.
