# Control Center GUI (Phase 2, spec §7)

PySide6 application. **M3a (landed):** application skeleton + read-only
Dashboard — station header, profile-resolved rigctld command, owned PipeWire
endpoints, supervisor instances, doctor summary. Catalog + Doctor views land
in M3b; Bundles start/stop, audio actions + Profiles in M3c.

## Layout

- `services.py` — headless service layer; the seam widgets use to reach core
  (in-process, spec §7/G1 — never via the `gd` CLI). Tests inject fakes here.
- `models.py` — pure snapshot → display rows (unit-testable without Qt).
- `workers.py` — `SnapshotWorker(QThread)` so core calls never block the UI.
- `views/` — one module per spec §7 view (`dashboard.py` in M3a).
- `main_window.py` — nav sidebar + stacked pages.
- `app.py` — QApplication entry; `--selfcheck` builds the window, applies one
  real snapshot and exits (headless-safe).

## Run / test

    gd gui                          # launch (or: python3 control/gui/app.py)
    gd gui --selfcheck              # headless smoke through real core data
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_gui_app.py -q

## Platform

- Production target: Fedora 45 **x86_64**, GNOME/Wayland — PySide6 comes from
  the distro (`sudo dnf install python3-pyside6`); Qt6 selects Wayland there.
- Dev/CI: any OS; tests force `QT_QPA_PLATFORM=offscreen`. pip wheels
  (`PySide6-Essentials`) are dev-only and never referenced by install recipes.
