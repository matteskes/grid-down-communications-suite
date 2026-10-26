# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Worker threads (M3a): run core calls off the UI thread.

Widgets stay responsive while doctor checks probe ports or the supervisor
spawns processes; results come back as Qt signals (queued to the UI thread).
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal


class SnapshotWorker(QThread):
    """Run a zero-arg callable; emit `done(result)` or `failed(message)`."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # worker thread
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI as text
            self.failed.emit(f"{type(exc).__name__}: {exc}")
