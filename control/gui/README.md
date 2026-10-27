# Control Center GUI (Phase 2, spec §7)

PySide6 application. **M3a:** skeleton + read-only Dashboard (station header,
profile-resolved rigctld command, owned PipeWire endpoints, supervisor
instances, doctor summary). **M3b:** Catalog (searchable table over the
41-app registry) + Doctor (color-coded checks, copy-as-JSON). **M3c (v1,
0.4.0-m3):** Bundles start/stop tiles (confirm dialog + notes pane), audio
up/down actions on the Dashboard, read-only Profiles view. **Post-P2:** rig
model/path/baud form on Profiles (atomic `save_rig`; repo template untouched)
plus a station-identity form (callsign / Maidenhead grid, atomic
`save_station`), an audio form (backend / rig capture / ardop
capture-playback presets, atomic `save_audio`), and a mesh form — device
type/path/host (QComboBox serial/tcp/ble + line edits), node identity
(long/short name, region, modem preset, channel URL) and the meshtasticd
daemon switch (QCheckBox), atomic `save_mesh`; the node config itself is
applied later by the Phase 4 `meshtastic configure` adapter.
**M4:** no view
changes — views are backend-neutral; the Dashboard's instance panel reflects
whatever `status()` reports, including the `backend` key (direct|systemd)
introduced with the systemd user-unit backend (`core/systemd.py`).
**M5 (0.6.0-m5):** Catalog gains the `install` column — install-state probes
from `core/install_state.py` (batched `rpm -q` for dnf apps, `shutil.which`
on the launch binary for build/tarball/pip-vendored, docroot check for
static, shared-prefix check for wine; tooltip = probe detail) — plus the
**Wine hub panel** (§6.4): prefix/wine/COM status line and start/stop per
wine-hub app through the same worker-thread pattern as Bundles, buttons
disabled while the prefix or wine is missing. The system tray
(`tray.py`, `TrayController`) is availability-gated — Show/Hide/Quit menu,
tooltip mirroring the doctor tally, clean no-op headless or with
`gd gui --no-tray` — and `tools/install-launcher.sh` installs a user-local
`.desktop` launcher + `packaging/grid-down.svg` icon.

## Layout

- `services.py` — headless service layer; the seam widgets use to reach core
  (in-process, spec §7/G1 — never via the `gd` CLI). Tests inject fakes here.
- `models.py` — pure snapshot → display rows (unit-testable without Qt).
- `workers.py` — `SnapshotWorker(QThread)` so core calls never block the UI.
- `tray.py` — `TrayController`: tray icon + menu when the desktop offers
  one; permanent no-op otherwise.
- `views/` — one module per spec §7 view (`dashboard`, `bundles`, `catalog`,
  `doctor`, `profiles`).
- `main_window.py` — nav sidebar + stacked pages (+ tray lifecycle).
- `app.py` — QApplication entry; `--selfcheck` builds the window, applies one
  real snapshot and exits (headless-safe); `--no-tray` opts out of the tray.

## Run / test

    gd gui                          # launch (or: python3 control/gui/app.py)
    gd gui --selfcheck              # headless smoke through real core data
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_gui_app.py -q

## Platform

- Production target: Fedora 45 **x86_64**, GNOME/Wayland — PySide6 comes from
  the distro (`sudo dnf install python3-pyside6`); Qt6 selects Wayland there.
- Dev/CI: any OS; tests force `QT_QPA_PLATFORM=offscreen`. pip wheels
  (`PySide6-Essentials`) are dev-only and never referenced by install recipes.
