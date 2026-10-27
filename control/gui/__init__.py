# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Grid Down Control Center (PySide6, spec §7).

Package layout:
- services.py     headless service layer — the DI seam to core modules
- models.py       pure snapshot -> display-row builders (no Qt)
- workers.py      QThread wrapper so core calls never block the UI
- tray.py         availability-gated system-tray controller (M5)
- views/          one module per spec §7 view (dashboard first)
- main_window.py  nav + stacked pages
- app.py          QApplication entry — `gd gui` / `python3 control/gui/app.py`

Keep this __init__ free of Qt imports: `gd gui` imports the package just to
read VERSION even when PySide6 is missing, to render a helpful error.
"""

# Keep in sync with VERSION in control/cli/gd.py until a shared version
# module lands (deliberate duplication; both bump together per milestone).
VERSION = "0.6.0-m5"
