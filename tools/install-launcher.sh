#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Install the Grid Down Control Center launcher (M5, spec §7).
#
# User-local only — no root, no system dirs:
#   ~/.local/share/applications/grid-down-control.desktop
#   ~/.local/share/icons/hicolor/scalable/apps/grid-down.svg
#
# Best effort throughout: a desktop environment without
# update-desktop-database still gets a working launcher.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
src_desktop="$here/packaging/grid-down-control.desktop"
src_icon="$here/packaging/grid-down.svg"

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
dest_apps="$data_home/applications"
dest_icons="$data_home/icons/hicolor/scalable/apps"

[ -f "$src_desktop" ] || { echo "install-launcher: missing $src_desktop" >&2; exit 1; }
[ -f "$src_icon" ] || { echo "install-launcher: missing $src_icon" >&2; exit 1; }

mkdir -p "$dest_apps" "$dest_icons"
install -m 644 "$src_desktop" "$dest_apps/grid-down-control.desktop"
install -m 644 "$src_icon" "$dest_icons/grid-down.svg"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$dest_apps" 2>/dev/null || true
fi

echo "installed: $dest_apps/grid-down-control.desktop"
echo "installed: $dest_icons/grid-down.svg"
echo "launcher expects 'gd' on PATH (control/cli/gd.py wrapper or your own alias)"