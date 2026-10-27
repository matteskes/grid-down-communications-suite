# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Launcher packaging tests (M5): .desktop keys, SVG shape, installer script."""

from __future__ import annotations

import configparser
import os
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "packaging" / "grid-down-control.desktop"
SVG = ROOT / "packaging" / "grid-down.svg"
SCRIPT = ROOT / "tools" / "install-launcher.sh"


def _entry() -> dict:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # desktop keys are case-sensitive
    parser.read(DESKTOP, encoding="utf-8")
    assert parser.has_section("Desktop Entry"), "missing [Desktop Entry] section"
    return dict(parser["Desktop Entry"])


def test_desktop_entry_core_keys():
    entry = _entry()
    assert entry["Type"] == "Application"
    assert entry["Name"].strip() != ""
    assert "gd gui" in entry["Exec"]
    assert entry["Icon"] == "grid-down"
    assert "HamRadio" in entry["Categories"]
    assert entry.get("Terminal", "true") == "false"


def test_svg_is_well_formed_with_viewbox():
    root = ET.parse(SVG).getroot()
    assert root.tag.endswith("svg")
    assert root.get("viewBox") == "0 0 64 64"
    shapes = [
        el for el in root.iter() if el.tag.rsplit("}", 1)[-1] in ("path", "line", "circle", "rect")
    ]
    assert len(shapes) >= 4, "icon lost its artwork"


def test_installer_script_is_user_local_and_best_effort():
    text = SCRIPT.read_text(encoding="utf-8")
    assert os.access(SCRIPT, os.X_OK), "install-launcher.sh must be executable"
    assert "sudo" not in text, "launcher install is strictly user-local"
    assert "applications" in text and "hicolor" in text
    assert "update-desktop-database" in text  # best-effort DB refresh
    assert text.startswith("#!/bin/sh") and "set -eu" in text
