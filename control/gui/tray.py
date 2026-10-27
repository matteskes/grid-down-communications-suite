# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""System tray (M3 note, shipped M5): availability-gated TrayController.

The tray is a convenience, never a dependency: when no system tray is
available (headless/offscreen runs, GNOME sessions without an appindicator
host, `gd gui --no-tray`) the controller stays a clean no-op — the window
works exactly as before and selfcheck reports `tray=no`. No appindicator
dependency is added (spec §7/G1 spirit: Qt only).

Menu: Show/Hide (toggles the main window), Quit. Tooltip mirrors the
doctor tally from each snapshot, so a glance at the tray is a glance at
station health (§6.7).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

ICON_SVG = Path(__file__).resolve().parents[2] / "packaging" / "grid-down.svg"


class TrayController:
    """Owns the QSystemTrayIcon when (and only when) the desktop offers one."""

    def __init__(self, window, enabled: bool = True):
        self.window = window
        self.tray: QSystemTrayIcon | None = None
        if not enabled:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return  # offscreen tests / trayless desktop: clean no-op
        if ICON_SVG.exists():
            icon = QIcon(str(ICON_SVG))
        else:  # standard-icon fallback (SVG missing on a dev checkout)
            style = window.style()
            icon = style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(icon, window)
        self.tray.setToolTip("Grid Down — Control Center")

        menu = QMenu()
        self.act_toggle = QAction("Hide", menu)
        self.act_toggle.triggered.connect(self._toggle_window)
        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(self.act_toggle)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.show()

    @property
    def available(self) -> bool:
        return self.tray is not None

    def _toggle_window(self) -> None:
        win = self.window
        if win.isVisible():
            win.hide()
            self.act_toggle.setText("Show")
        else:
            win.show()
            win.raise_()
            self.act_toggle.setText("Hide")

    def update(self, snap: dict) -> None:
        """Refresh the tooltip from one dashboard snapshot (doctor tally)."""
        if self.tray is None:
            return
        try:
            counts = ((snap.get("doctor") or {}).get("counts")) or {}
            tally = ", ".join(f"{counts.get(k, 0)} {k}" for k in ("ok", "warn", "fail", "skip"))
            self.tray.setToolTip(f"Grid Down — doctor: {tally}")
        except Exception:  # tooltip is best-effort; never break the snapshot
            pass
