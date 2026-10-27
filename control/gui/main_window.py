# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Main window (M3a): nav sidebar + stacked pages.

The Dashboard page shipped in M3a; Bundles/Catalog/Doctor/Profile landed in
M3b/M3c (spec §7). The window owns the autorefresh timer and fans each
snapshot out to every page (until the P2 VM pass the live window only fed
the Dashboard, leaving the other pages empty outside --selfcheck).
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QMainWindow, QStackedWidget, QWidget

from gui.services import Services, snapshot
from gui.views.bundles import BundlesView
from gui.views.catalog import CatalogView
from gui.views.dashboard import DashboardView
from gui.views.doctor import DoctorView
from gui.views.profiles import ProfilesView
from gui.workers import SnapshotWorker

PAGES = ("Dashboard", "Bundles", "Catalog", "Doctor", "Profile")


class MainWindow(QMainWindow):
    def __init__(self, services: Services, autostart: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Grid Down — Control Center")
        self.resize(960, 640)

        self.dashboard = DashboardView(services, self)
        self.bundles = BundlesView(services, self)
        self.catalog = CatalogView(services, self)
        self.doctor = DoctorView(services, self)
        self.profiles = ProfilesView(services, self)
        stack = QStackedWidget(self)
        stack.addWidget(self.dashboard)  # Dashboard
        stack.addWidget(self.bundles)  # Bundles (M3c)
        stack.addWidget(self.catalog)  # Catalog (M3b)
        stack.addWidget(self.doctor)  # Doctor (M3b)
        stack.addWidget(self.profiles)  # Profile (M3c)
        self.bundles.changed.connect(self.refresh)

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

        self._worker: SnapshotWorker | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self.refresh)
        if autostart:
            self.start_autorefresh()

    # ------------------------------------------------------------ plumbing
    def start_autorefresh(self, secs: int = 10) -> None:
        self._timer.setInterval(secs * 1000)
        self._timer.start()
        self.refresh()

    def stop_autorefresh(self) -> None:
        self._timer.stop()

    def refresh(self) -> None:
        if self._worker is not None:
            return  # one snapshot in flight at a time
        worker = SnapshotWorker(lambda: snapshot(self.services), parent=self)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _drop_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            for sig in (worker.done, worker.failed):
                try:
                    sig.disconnect()
                except RuntimeError, TypeError:
                    pass

    def _on_done(self, snap: dict) -> None:
        self._drop_worker()
        self.set_snapshot(snap)

    def _on_failed(self, _message: str) -> None:
        self._drop_worker()

    def set_snapshot(self, snap: dict) -> None:
        """Fan one snapshot out to every page. Same shape app.py --selfcheck
        applies; the live autorefresh path is this window's timer/worker."""
        self.dashboard.set_snapshot(snap)
        self.bundles.set_snapshot(snap)
        self.catalog.set_snapshot(snap)
        self.doctor.set_snapshot(snap)
        self.profiles.set_snapshot(snap)
