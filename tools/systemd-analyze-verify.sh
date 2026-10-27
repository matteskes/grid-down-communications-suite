#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Verify the generated systemd user units with `systemd-analyze verify` inside
# a fedora:45 container — the §10 container proof for the M4 backend (no
# systemd host needed on the dev machine).
# Usage: bash tools/systemd-analyze-verify.sh [units-dir]
#   Without an argument, renders the current registry via `gd units install`
#   into a temp dir first (host python; daemon-reload is skipped without a
#   user session, which is fine — verify works on the files).
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNITS="${1:-}"

if [ -z "$UNITS" ]; then
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    mkdir -p "$TMP/bin" "$TMP/run"
    printf '#!/bin/sh\nexec sleep 60\n' > "$TMP/bin/rigctld"  # hamlib resolves
    chmod +x "$TMP/bin/rigctld"
    PY="$GD_ROOT/.venv/bin/python"
    [ -x "$PY" ] || PY=python3
    (cd "$GD_ROOT" && \
     XDG_CONFIG_HOME="$TMP/config" XDG_RUNTIME_DIR="$TMP/run" \
     GRID_DOWN_STATE_DIR="$TMP/state" HOME="$TMP" PATH="$TMP/bin:$PATH" \
     "$PY" control/cli/gd.py units install --json > "$TMP/install.json")
    "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); \
print(f\"rendered: {len(d['installed'])} services + \
{sum(1 for w in d['written'] if w.endswith('.target'))} targets; \" \
f\"{len(d['unresolved'])} unresolved, {len(d['skipped'])} skipped\")" "$TMP/install.json"
    UNITS="$TMP/config/systemd/user"
fi
[ -d "$UNITS" ] || { echo "ERROR: units dir not found: $UNITS" >&2; exit 1; }
echo "units dir: $UNITS ($(ls "$UNITS" | wc -l | tr -d ' ') files)"

# Same systemd (262.x) either way; pick the native arch so verify's
# ExecStart probe runs without Rosetta/fexecve quirks (amd64-under-Rosetta
# reports EINVAL for every command, even stock ones like /usr/bin/true).
case "$(uname -m)" in
    arm64|aarch64) PLATFORM=linux/arm64 ;;
    *)             PLATFORM=linux/amd64 ;;
esac
echo "verify platform: $PLATFORM"

docker run --rm --platform "$PLATFORM" \
    -v "$UNITS:/units:ro" \
    -v "$GD_ROOT/tools/systemd-verify-inner.sh:/inner.sh:ro" \
    fedora:45 bash /inner.sh
