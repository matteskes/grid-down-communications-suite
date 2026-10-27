# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Doctor view (M3b): color-coded preflight checks + copy-as-JSON (§6.7).

Row building stays in gui.models (pure); this widget only colors and shows,
so offscreen tests can drive it with dict-shaped fake results.
"""

from __future__ import annotations

import json

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import models
from gui.services import Services

STATUS_COLOR = {"ok": "#1a7f37", "warn": "#9a6700", "fail": "#b02020", "skip": "#6e7781"}


class DoctorView(QWidget):
    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self._results: list = []

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(("status", "check", "detail"))
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 70)
        self.tree.setColumnWidth(1, 280)

        self.summary_label = QLabel("no snapshot yet")
        copy_btn = QPushButton("Copy as JSON")
        copy_btn.clicked.connect(self._copy_clicked)

        bottom = QHBoxLayout()
        bottom.addWidget(self.summary_label)
        bottom.addStretch(1)
        bottom.addWidget(copy_btn)

        lay = QVBoxLayout(self)
        lay.addWidget(self.tree)
        lay.addLayout(bottom)

    def set_snapshot(self, snap: dict) -> None:
        self.tree.clear()
        self._results = list(((snap.get("doctor") or {}).get("results")) or [])
        for status, title, detail in models.doctor_rows(snap):
            item = QTreeWidgetItem([models.DOCTOR_GLYPH.get(status, status), title, detail])
            item.setForeground(0, QColor(STATUS_COLOR.get(status, "#000000")))
            self.tree.addTopLevelItem(item)
        self.summary_label.setText("\n".join(models.doctor_lines(snap)) or "—")

    def copy_json(self) -> str:
        """Serialize current results (doctor.to_json shape); also to clipboard."""
        payload = json.dumps({"checks": [self._asdict(r) for r in self._results]}, indent=2)
        QApplication.clipboard().setText(payload)
        return payload

    @staticmethod
    def _asdict(r) -> dict:
        if isinstance(r, dict):
            return r
        return {"id": r.id, "title": r.title, "status": r.status, "detail": r.detail}

    def _copy_clicked(self) -> None:
        self.copy_json()
