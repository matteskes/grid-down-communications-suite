# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Main window (M3a): nav sidebar + stacked pages.

M3a ships the Dashboard page; the Bundles/Catalog/Doctor/Profile nav entries
are placeholders that land in M3b/M3c per the milestone plan (spec §7).
"""
from __future__ import annotations

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from gui.services import Services
from gui.views.dashboard import DashboardView

PAGES = ("Dashboard", "Bundles", "Catalog", "Doctor", "Profile")
PENDING = {"Bundles": "M3c", "Catalog": "M3b", "Doctor": "M3b", "Profile": "M3c"}


class MainWindow(QMainWindow):
    def __init__(self, services: Services, autostart: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Grid Down — Control Center")
        self.resize(960, 640)

        self.dashboard = DashboardView(services, self)
        stack = QStackedWidget(self)
        stack.addWidget(self.dashboard)
        for name in PAGES[1:]:
            stack.addWidget(self._placeholder(name))

        self.nav = QListWidget(self)
        for name in PAGES:
            self.nav.addItem(name)
        self.nav.setFixedWidth(170)
        self.nav.currentRowChanged.connect(stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        central = QWidget(self)
        lay = QHBoxLayout(central)
        lay.addWidget(self.nav)
        lay.addWidget(stack, 1)
        self.setCentralWidget(central)

        if autostart:
            self.dashboard.start_autorefresh()

    def _placeholder(self, name: str) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        note = QLabel(f"{name} view lands in {PENDING[name]} (spec §7).")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return page
