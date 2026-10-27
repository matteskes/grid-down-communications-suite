# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Profiles view (M3c): station profile + rig editing.

M3c shipped the read-only YAML pane; the rig form (model / serial path /
baud) came with the post-P2 pass — the station's most common edits without
a text editor. Saves run in a SnapshotWorker like every other core call;
`core/profile.save_rig` validates and writes atomically (never the repo
template — a user profile is created instead). Fields prefill from the
snapshot only while untouched: the window autorefreshes every 10 s and
must not clobber mid-typing.
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

        form_box = QGroupBox("Rig (radio) — saved to the station profile")
        form = QFormLayout(form_box)
        self.model_edit = QLineEdit(self)
        self.model_edit.setPlaceholderText("numeric hamlib id, e.g. 3073 (empty = Dummy)")
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("/dev/ttyUSB0")
        self.baud_edit = QLineEdit(self)
        self.baud_edit.setPlaceholderText("38400")
        edits = self._edits = (
            ("model", self.model_edit),
            ("path", self.path_edit),
            ("baud", self.baud_edit),
        )
        for name, edit in edits:
            edit.textEdited.connect(lambda _=None, n=name: self._dirty.add(n))
            form.addRow(name.capitalize() + ":", edit)
        self.save_btn = QPushButton("Save rig", self)
        self.save_btn.clicked.connect(self._save)
        form.addRow(self.save_btn)
        self.save_status = QLabel("", self)
        self.save_status.setWordWrap(True)

        self.path_label = QLabel("—")
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))

        lay = QVBoxLayout(self)
        lay.addWidget(form_box)
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
        self._prefill(snap.get("rig") or {})

    def _prefill(self, rig: dict) -> None:
        """Dirty-until-confirmed: untouched fields prefill from the snapshot;
        edited fields are protected until the snapshot matches what's typed
        (i.e. the save round-tripped) — autorefresh never clobbers typing."""
        values = (
            str(rig.get("model") or ""),
            str(rig.get("path") or ""),
            str(rig.get("baud") or ""),
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
