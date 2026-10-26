# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Catalog view (M3b): searchable table of every app in the registry.

Read-only per the M3 plan (install-state columns and launch controls stay
CLI-side); this page answers "what exists, what it is, what's running".
Filtering is a QSortFilterProxyModel over the pure rows from gui.models.
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QSortFilterProxyModel, \
    QModelIndex, Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel,
                               QLineEdit, QTableView, QVBoxLayout, QWidget)

from gui import models
from gui.services import Services

COLUMNS = ("app", "name", "category", "via", "state")


class CatalogModel(QAbstractTableModel):
    def __init__(self, rows: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = list(rows or [])

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return str(row.get(COLUMNS[index.column()], ""))
        if role == Qt.ToolTipRole:
            return str(row.get("tagline")) or None
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None


class CatalogView(QWidget):
    """Registry table + case-insensitive substring filter + count line."""

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.model = CatalogModel(parent=self)

        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)  # match any column
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)

        self.filter_box = QLineEdit(self)
        self.filter_box.setPlaceholderText("filter by app, name or category…")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self._apply_filter)

        self.table = QTableView(self)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)

        self.count_label = QLabel("no snapshot yet")
        bottom = QHBoxLayout()
        bottom.addWidget(self.count_label)
        bottom.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addWidget(self.filter_box)
        lay.addWidget(self.table)
        lay.addLayout(bottom)

    def set_snapshot(self, snap: dict) -> None:
        self.model.set_rows(models.catalog_rows(snap))
        self._apply_filter()

    def _apply_filter(self) -> None:
        self.proxy.setFilterFixedString(self.filter_box.text())
        total, shown = self.model.rowCount(), self.proxy.rowCount()
        if self.filter_box.text():
            self.count_label.setText(f"{total} apps ({shown} shown)")
        else:
            self.count_label.setText(f"{total} apps")
