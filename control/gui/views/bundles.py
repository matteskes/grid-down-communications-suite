# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Bundles view (M3c): start/stop tiles per §6.3 session bundle + notes pane.

Actions run in a SnapshotWorker (never the UI thread) and confirm first —
the QMessageBox is guarded behind _confirm so offscreen tests inject the
answer. After an action the view emits `changed`; the main window refreshes
the other views from it. rigctld handling (singleton, shared-dependency
rules) stays in core/supervisor — the GUI only asks.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from gui import models
from gui.services import Services
from gui.workers import SnapshotWorker


class BundlesView(QWidget):
    changed = Signal()

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self._busy = False
        self._worker: SnapshotWorker | None = None
        self._tiles: dict[str, tuple[QLabel, QLabel]] = {}

        self.tiles_box = QGroupBox("Session bundles (spec §6.3)")
        self.grid = QGridLayout(self.tiles_box)

        self.notes = QPlainTextEdit(self)
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText("start/stop action notes appear here")
        self.notes.setMaximumHeight(140)

        lay = QVBoxLayout(self)
        lay.addWidget(self.tiles_box)
        lay.addWidget(QLabel("Notes:"))
        lay.addWidget(self.notes)

    # ------------------------------------------------------------- display
    def set_snapshot(self, snap: dict) -> None:
        for i, row in enumerate(models.bundle_rows(snap)):
            if row["name"] not in self._tiles:
                self._add_tile(row, i)
            count, detail = self._tiles[row["name"]]
            count.setText(f"{row['running']}/{row['total']} running")
            detail.setText(row["description"])

    def _add_tile(self, row: dict, index: int) -> None:
        name = row["name"]
        tile = QGroupBox(name)
        count = QLabel("–/– running")
        detail = QLabel(row["description"])
        detail.setWordWrap(True)
        start = QPushButton("Start")
        stop = QPushButton("Stop")
        start.clicked.connect(lambda _=False, n=name: self._start(n))
        stop.clicked.connect(lambda _=False, n=name: self._stop(n))
        btns = QHBoxLayout()
        btns.addWidget(start)
        btns.addWidget(stop)
        btns.addStretch(1)
        lay = QVBoxLayout(tile)
        lay.addWidget(count)
        lay.addWidget(detail)
        lay.addLayout(btns)
        self.grid.addWidget(tile, index // 2, index % 2)
        self._tiles[name] = (count, detail)

    # ------------------------------------------------------------- actions
    def _start(self, name: str) -> None:
        self._run(name, start=True)

    def _stop(self, name: str) -> None:
        self._run(name, start=False)

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def _run(self, name: str, start: bool) -> None:
        if self._busy:
            return
        verb = "Start" if start else "Stop"
        if not self._confirm(f"{verb} bundle {name}?", f"{verb} bundle '{name}' now?"):
            self.notes.appendPlainText(f"cancelled: {verb.lower()} {name}")
            return
        self._busy = True
        self.notes.appendPlainText(f"{verb.lower()}ing {name}…")
        svc = self.services
        fn = (lambda: svc.bundle_start(name)) if start else \
             (lambda: svc.bundle_stop(name))
        worker = SnapshotWorker(fn, parent=self)
        worker.done.connect(self._done)
        worker.failed.connect(self._failed)
        self._worker = worker
        worker.start()

    def _done(self, out: object) -> None:
        self._busy = False
        notes = list((out or {}).get("notes") or [])
        self.notes.appendPlainText("\n".join(notes) or "done")
        self.changed.emit()

    def _failed(self, message: str) -> None:
        self._busy = False
        self.notes.appendPlainText(f"FAILED: {message}")
        self.changed.emit()
