# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Dashboard view (M3a): station header, rig endpoint, owned audio endpoints,
supervisor instances, doctor summary. Read-only; refresh via worker thread.

Display logic lives in gui.models (pure); this widget only wires snapshots to
labels, so offscreen tests call set_snapshot() directly with fake payloads.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from gui import models
from gui.services import Services, snapshot
from gui.workers import SnapshotWorker


class DashboardView(QWidget):
    """Read-only station dashboard; polls Services via SnapshotWorker."""

    refreshed = Signal(dict)

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.snapshot_data: dict = {}
        self._worker: SnapshotWorker | None = None

        layout = QVBoxLayout(self)

        self.header_labels: dict[str, QLabel] = {}
        form = QFormLayout()
        for title in ("Version", "Profile", "Station", "Callsign", "Grid"):
            label = QLabel("—")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.header_labels[title] = label
            form.addRow(f"{title}:", label)
        station_box = QGroupBox("Station")
        station_box.setLayout(form)
        layout.addWidget(station_box)

        self.rig_label = self._mono_label()
        layout.addWidget(self._box("Rig — rigctld (suite-owned, G4)", self.rig_label))

        self.audio_label = self._wrap_label()
        layout.addWidget(self._box("Audio — owned PipeWire endpoints (G4)",
                                   self.audio_label))

        self.instances_label = self._wrap_label()
        layout.addWidget(self._box("Instances — supervisor state",
                                   self.instances_label))

        self.doctor_label = self._wrap_label()
        layout.addWidget(self._box("Doctor — preflight summary", self.doctor_label))

        self.errors_label = self._wrap_label()
        self.errors_label.setStyleSheet("color: #a06000;")
        self.errors_label.hide()
        layout.addWidget(self.errors_label)

        self.status_label = QLabel("no snapshot yet")
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        bottom.addWidget(refresh_btn)
        layout.addLayout(bottom)
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self.refresh)
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
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self.status_label.setText("refreshing…")
        worker.start()

    def _drop_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            for sig in (worker.done, worker.failed):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass

    def _on_done(self, snap: dict) -> None:
        self._drop_worker()
        self.set_snapshot(snap)

    def _on_failed(self, message: str) -> None:
        self._drop_worker()
        self.status_label.setText(f"refresh failed: {message}")
        self.status_label.setStyleSheet("color: #b02020;")

    # ------------------------------------------------------------- display
    def set_snapshot(self, snap: dict) -> None:
        """Apply one payload to the labels. Safe to call directly in tests."""
        self.snapshot_data = snap
        values = dict(models.header_rows(snap))
        for title, label in self.header_labels.items():
            label.setText(values.get(title, "—"))
        self.rig_label.setText("\n".join(models.rig_lines(snap)) or "—")
        self.audio_label.setText("\n".join(
            f"{name} — {state}" for name, state in models.audio_rows(snap)) or "—")
        self.instances_label.setText("\n".join(
            f"{name} — {state}" for name, state in models.instance_rows(snap)) or "—")
        self.doctor_label.setText("\n".join(models.doctor_lines(snap)) or "—")
        errs = models.error_lines(snap)
        self.errors_label.setText("\n".join(errs))
        self.errors_label.setVisible(bool(errs))
        self.status_label.setText(f"snapshot at {time.strftime('%H:%M:%S')}")
        self.status_label.setStyleSheet("")
        self.refreshed.emit(snap)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _box(title: str, inner: QLabel) -> QGroupBox:
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.addWidget(inner)
        return box

    @staticmethod
    def _wrap_label() -> QLabel:
        label = QLabel("—")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    @staticmethod
    def _mono_label() -> QLabel:
        label = DashboardView._wrap_label()
        label.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        return label
