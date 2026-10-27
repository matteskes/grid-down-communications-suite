#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Control Center entry point (M3a).

Launch: `gd gui` or `python3 control/gui/app.py` (spec §7, PySide6).
Headless-safe: QT_QPA_PLATFORM=offscreen gd gui --selfcheck. Production
target is Fedora 45 x86_64 (GNOME/Wayland) — Qt6 picks Wayland there;
dev boxes exercise the identical code via the offscreen platform. M5 adds
the system tray (availability-gated; `--no-tray` opts out) and the
packaging/ launcher (desktop file + icon via tools/install-launcher.sh).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # control/

from gui.main_window import MainWindow  # noqa: E402
from gui.services import default_services, snapshot  # noqa: E402

ICON_SVG = Path(__file__).resolve().parents[2] / "packaging" / "grid-down.svg"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gd gui", description="Grid Down Control Center (spec §7)")
    ap.add_argument(
        "--selfcheck",
        action="store_true",
        help="build the window, apply one snapshot, exit (headless-safe)",
    )
    ap.add_argument(
        "--no-tray",
        action="store_true",
        help="disable the system tray icon (M5; default: on when a tray exists)",
    )
    args = ap.parse_args(argv)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    app.setApplicationName("Grid Down Control Center")
    if ICON_SVG.exists():
        app.setWindowIcon(QIcon(str(ICON_SVG)))  # window + tray share it
    win = MainWindow(
        default_services(),
        autostart=not args.selfcheck,
        use_tray=not args.no_tray,
    )
    win.show()
    if args.selfcheck:
        snap = snapshot(win.dashboard.services)
        win.set_snapshot(snap)  # fans out to every page + the tray
        app.processEvents()
        print(
            "gui selfcheck ok — "
            f"apps={win.catalog.model.rowCount()}, "
            f"bundles={len(win.bundles._tiles)}, "
            f"doctor checks={win.doctor.tree.topLevelItemCount()}, "
            f"tray={'yes' if win.tray.available else 'no'}, "
            f"platform={app.platformName()}"
        )
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
