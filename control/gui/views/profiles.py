# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Profiles view (M3c): station profile + rig editing.

M3c shipped the read-only YAML pane; the post-P2 passes added the two
everyday forms without a text editor: rig model / serial path / baud and
station identity (callsign / Maidenhead grid). Saves run in a SnapshotWorker
like every other core call; `core/profile.save_rig` / `save_station` validate
and write atomically (never the repo template — a user profile is created
instead). Fields prefill from the snapshot only while untouched: the window
autorefreshes every 10 s and must not clobber mid-typing.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import models
from gui.services import Services
from gui.workers import SnapshotWorker


class ProfilesView(QWidget):
    changed = Signal()

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self._busy = False
        self._worker: SnapshotWorker | None = None
        self._dirty: set[str] = set()

        rig_box = QGroupBox("Rig (radio) — saved to the station profile")
        form = QFormLayout(rig_box)
        self.model_edit = QLineEdit(self)
        self.model_edit.setPlaceholderText("numeric hamlib id, e.g. 3073 (empty = Dummy)")
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("/dev/ttyUSB0")
        self.baud_edit = QLineEdit(self)
        self.baud_edit.setPlaceholderText("38400")
        rig_edits = (
            ("model", self.model_edit),
            ("path", self.path_edit),
            ("baud", self.baud_edit),
        )
        self.save_btn = QPushButton("Save rig", self)
        self.save_btn.clicked.connect(self._save)

        station_box = QGroupBox("Station identity — saved to the station profile")
        station_form = QFormLayout(station_box)
        self.callsign_edit = QLineEdit(self)
        self.callsign_edit.setPlaceholderText("W1AW (empty = unset)")
        self.grid_edit = QLineEdit(self)
        self.grid_edit.setPlaceholderText("Maidenhead square, e.g. FN31pr")
        station_edits = (
            ("callsign", self.callsign_edit),
            ("grid", self.grid_edit),
        )
        self.station_save_btn = QPushButton("Save station", self)
        self.station_save_btn.clicked.connect(self._save_station)

        edits = self._edits = rig_edits + station_edits
        for name, edit in edits:
            edit.textEdited.connect(lambda _=None, n=name: self._dirty.add(n))
        for name, edit in rig_edits:
            form.addRow(name.capitalize() + ":", edit)
        form.addRow(self.save_btn)
        for name, edit in station_edits:
            station_form.addRow(name.capitalize() + ":", edit)
        station_form.addRow(self.station_save_btn)
        self.save_status = QLabel("", self)
        self.save_status.setWordWrap(True)

        self.path_label = QLabel("—")
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))

        lay = QVBoxLayout(self)
        lay.addWidget(rig_box)
        lay.addWidget(station_box)
        lay.addWidget(self.save_status)
        lay.addWidget(self.path_label)
        lay.addWidget(self.text)

    # ------------------------------------------------------------- display
    def set_snapshot(self, snap: dict) -> None:
        prof = snap.get("profile") or {}
        self.path_label.setText(
            f"station profile: {prof.get('path', '?')}"
            f"  (station: {prof.get('id') or '(unset)'})"
        )
        self.text.setPlainText(models.profile_text(snap))
        self._prefill(snap)

    def _prefill(self, snap: dict) -> None:
        """Dirty-until-confirmed: untouched fields prefill from the snapshot;
        edited fields are protected until the snapshot matches what's typed
        (i.e. the save round-tripped) — autorefresh never clobbers typing."""
        rig = snap.get("rig") or {}
        head = snap.get("profile") or {}
        values = (
            str(rig.get("model") or ""),
            str(rig.get("path") or ""),
            str(rig.get("baud") or ""),
            str(head.get("callsign") or ""),
            str(head.get("grid") or ""),
        )
        for (name, edit), value in zip(self._edits, values):
            if edit.text() == value:
                self._dirty.discard(name)  # snapshot confirms the typed value
            elif name not in self._dirty:
                edit.setText(value)

    # -------------------------------------------------------------- action
    def _save(self) -> None:
        if self._busy:
            return
        model, path, baud = (
            self.model_edit.text().strip(),
            self.path_edit.text().strip(),
            self.baud_edit.text().strip(),
        )
        self._busy = True
        self.save_status.setText("saving rig…")
        svc = self.services
        worker = SnapshotWorker(lambda: svc.rig_save(model, path, baud or None), parent=self)
        worker.done.connect(self._save_done)
        worker.failed.connect(self._save_failed)
        self._worker = worker
        worker.start()

    def _save_done(self, out: object) -> None:
        self._busy = False
        data = out or {}
        where = data.get("path", "?")
        verb = "created" if data.get("created") else "updated"
        self.save_status.setText(
            f"rig {verb}: {where} — applies on the next rig start; re-run"
            " `gd units install` if units are installed (hamlib ExecStart re-renders)."
        )
        self.changed.emit()

    def _save_failed(self, message: str) -> None:
        self._busy = False
        self.save_status.setText(f"FAILED: {message}")
        self.changed.emit()

    def _save_station(self) -> None:
        if self._busy:
            return
        callsign = self.callsign_edit.text().strip()
        grid = self.grid_edit.text().strip()
        self._busy = True
        self.save_status.setText("saving station identity…")
        svc = self.services
        worker = SnapshotWorker(lambda: svc.station_save(callsign, grid), parent=self)
        worker.done.connect(self._station_save_done)
        worker.failed.connect(self._save_failed)
        self._worker = worker
        worker.start()

    def _station_save_done(self, out: object) -> None:
        self._busy = False
        data = out or {}
        where = data.get("path", "?")
        verb = "created" if data.get("created") else "updated"
        prof = data.get("profile") or {}
        call = str(prof.get("callsign") or "(unset)")
        square = str(prof.get("grid") or "(unset)")
        self.save_status.setText(
            f"station identity {verb}: {where} — callsign={call} grid={square};"
            " the Doctor profile check flips to [ok]."
        )
        self.changed.emit()
