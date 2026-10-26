# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Profiles view (M3c): read-only station profile (YAML).

Deliberately read-only for M3: core profile.py is load-only and the M3 plan
defers form-driven editing. The view renders the raw profile document and
names its path so the operator can open it in an editor.
"""
from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from gui import models
from gui.services import Services


class ProfilesView(QWidget):
    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.path_label = QLabel("—")
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        lay = QVBoxLayout(self)
        lay.addWidget(self.path_label)
        lay.addWidget(self.text)

    def set_snapshot(self, snap: dict) -> None:
        prof = snap.get("profile") or {}
        self.path_label.setText(f"station profile: {prof.get('path', '?')}"
                                f"  (station: {prof.get('id') or '(unset)'})")
        self.text.setPlainText(models.profile_text(snap))
