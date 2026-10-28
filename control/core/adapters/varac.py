# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""VarAC config adapter (``varac-ini``): VarAC.ini beside VarAC.exe.

VarAC's portable zip ships only reference INIs (VarAC_cat_commands.ini,
VarAC_templates.ini, VarAC_UI_languages.ini); the station's own VarAC.ini
appears beside VarAC.exe on first run. Resolution, masking, declared-key
writes: all inherited from :class:`adapters.vara.VaraIniAdapter` — only the
registry wiring (``adapter: varac-ini``) and this documentation differ.
"""

from __future__ import annotations

from .vara import VaraIniAdapter


class VaracIniAdapter(VaraIniAdapter):
    """Same exe-adjacent contract, derived name: VarAC.exe -> VarAC.ini."""
