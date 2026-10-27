# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Catalog view (M3b + M5): registry table with install state + wine hub.

M3b shipped the searchable table ("what exists, what it is, what's
running"). M5 adds the `install` column (probe from core/install_state,
tooltip = probe detail) and the Wine hub panel (§6.4): prefix/wine/COM
status line plus start/stop per wine-hub app, run through the same
worker-thread pattern as Bundles — never on the UI thread — with buttons
disabled when the prefix or wine is missing. Filtering is a
QSortFilterProxyModel over the pure rows from gui.models.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from gui import models
from gui.services import Services
from gui.workers import SnapshotWorker

COLUMNS = ("app", "name", "category", "via", "install", "state")


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
            if COLUMNS[index.column()] == "install":
                detail = str(row.get("install_detail") or "")
                if detail:
                    return f"{row.get('app', '?')}: {detail}"
                return str(row.get("tagline")) or None
            return str(row.get("tagline")) or None
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None


class CatalogView(QWidget):
    """Registry table + filter + count line + Wine hub panel (M5)."""

    changed = Signal()

    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self._busy = False
        self._worker: SnapshotWorker | None = None
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

        # --- Wine hub panel (§6.4, M5): status line + per-app start/stop
        self.winehub_box = QGroupBox("Wine hub (VARA family, spec §6.4)")
        wh_lay = QVBoxLayout(self.winehub_box)
        self.winehub_label = QLabel("no snapshot yet")
        self.winehub_label.setWordWrap(True)
        self.winehub_btns = QHBoxLayout()
        wh_lay.addWidget(self.winehub_label)
        wh_lay.addLayout(self.winehub_btns)

        lay = QVBoxLayout(self)
        lay.addWidget(self.filter_box)
        lay.addWidget(self.table)
        lay.addLayout(bottom)
        lay.addWidget(self.winehub_box)

    def set_snapshot(self, snap: dict) -> None:
        self.model.set_rows(models.catalog_rows(snap))
        self._apply_filter()
        self._set_winehub(snap)

    def _apply_filter(self) -> None:
        self.proxy.setFilterFixedString(self.filter_box.text())
        total, shown = self.model.rowCount(), self.proxy.rowCount()
        if self.filter_box.text():
            self.count_label.setText(f"{total} apps ({shown} shown)")
        else:
            self.count_label.setText(f"{total} apps")

    # ------------------------------------------------------------ wine hub
    def _set_winehub(self, snap: dict) -> None:
        wh = snap.get("winehub") or {}
        err = (snap.get("errors") or {}).get("winehub")
        if err:
            self.winehub_label.setText(f"unavailable — {err}")
        elif not wh:
            self.winehub_label.setText("unavailable — no winehub section in snapshot")
        else:
            com = wh.get("com") or {}
            com_txt = ", ".join(f"{c}->{d}" for c, d in sorted(com.items())) or "none"
            self.winehub_label.setText(
                f"prefix {'present' if wh.get('prefix') else 'ABSENT'} "
                f"({wh.get('prefix_path', '?')}) · "
                f"wine {'on PATH' if wh.get('wine') else 'NOT on PATH'} · COM: {com_txt}"
            )
        # Rebuild the per-app button row: running state + the prefix/wine
        # gate change per snapshot, and tests index buttons by id.
        while self.winehub_btns.count():
            item = self.winehub_btns.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        enabled = bool(wh.get("prefix")) and bool(wh.get("wine"))
        for a in wh.get("apps") or []:
            app_id = str(a.get("id", "?"))
            exe = str(a.get("exe") or "?")
            state = "running" if a.get("running") else "stopped"
            lbl = QLabel(f"{app_id} [{exe}] {state}")
            start = QPushButton("Start")
            stop = QPushButton("Stop")
            start.setEnabled(enabled and not a.get("running"))
            stop.setEnabled(enabled and bool(a.get("running")))
            start.clicked.connect(lambda _=False, n=app_id: self._wine(n, start=True))
            stop.clicked.connect(lambda _=False, n=app_id: self._wine(n, start=False))
            self.winehub_btns.addWidget(lbl)
            self.winehub_btns.addWidget(start)
            self.winehub_btns.addWidget(stop)
        self.winehub_btns.addStretch(1)

    def _wine(self, app_id: str, start: bool) -> None:
        if self._busy:
            return
        self._busy = True
        self.winehub_label.setText(f"{'starting' if start else 'stopping'} {app_id}…")
        svc = self.services
        fn = (lambda: svc.winehub_start(app_id)) if start else (lambda: svc.winehub_stop(app_id))
        worker = SnapshotWorker(fn, parent=self)
        worker.done.connect(self._wine_done)
        worker.failed.connect(self._wine_failed)
        self._worker = worker
        worker.start()

    def _wine_done(self, out: object) -> None:
        self._busy = False
        res = out or {}
        self.winehub_label.setText(f"{res.get('state', 'done')}: {res.get('id', '?')}")
        self.changed.emit()

    def _wine_failed(self, message: str) -> None:
        self._busy = False
        self.winehub_label.setText(f"FAILED: {message}")
        self.changed.emit()
