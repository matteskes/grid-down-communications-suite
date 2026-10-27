#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# M5 VM smoke (guest-side driver v5) — run INSIDE the Fedora 45 guest after a
# `git archive` sync of the M5 tree (no pytest needed on the guest):
#
#   git archive --format=tar.gz -o /tmp/grid-down-m5.tar.gz HEAD
#   scp /tmp/grid-down-m5.tar.gz <user>@<vm>:/var/tmp/
#   # on the guest:
#   mkdir -p /var/tmp/grid-down && tar -xzf /var/tmp/grid-down-m5.tar.gz -C /var/tmp/grid-down
#   sh /var/tmp/grid-down/tools/vm-smoke-m5.sh
#
# Gates: install-state columns (real rpm), winehub no-prefix paths + clean
# refusals, doctor wine line, offscreen GUI selfcheck with tray=no.
# Exits non-zero on the first failed gate; prints M5-SMOKE: PASS at the end.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
gd() { python3 "$here/control/cli/gd.py" "$@"; }

echo "== gate 1: gd apps list --state (real rpm on the guest)"
gd apps list --state | tail -1
gd apps list --state | grep -q 'STRATEGY.*STATE'
gd apps list --state | grep -q 'install state:'

echo "== gate 2: --json carries install_state in vocabulary"
python3 - "$here" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1] + "/control")
from core import install_state, registry
from pathlib import Path
apps = registry.load_registry(Path(sys.argv[1]) / "control" / "registry" / "apps")
out = install_state.app_states(apps)
assert set(out) == {a["id"] for a in apps}, "row per app"
vocab = {"installed", "partial", "missing", "prefix", "unknown"}
assert all(r["state"] in vocab for r in out.values()), out
print(f"install_state: {len(out)} apps, states={sorted({r['state'] for r in out.values()})}")
PYEOF

echo "== gate 3: gd winehub status without a prefix (clean report, no crash)"
gd winehub status
gd winehub status | grep -q 'prefix: ABSENT'

echo "== gate 4: gd winehub start refuses cleanly without a prefix"
if gd winehub start vara-hf 2>/tmp/m5-err; then
    echo "FAIL: start should refuse without a prefix" >&2; exit 1
fi
grep -q 'not installed yet' /tmp/m5-err
rm -f /tmp/m5-err

echo "== gate 5: gd winehub map refuses cleanly without a prefix"
if gd winehub map 5 /dev/null 2>/tmp/m5-err; then
    echo "FAIL: map should refuse without a prefix" >&2; exit 1
fi
grep -q 'not installed yet' /tmp/m5-err
rm -f /tmp/m5-err

echo "== gate 6: doctor wine line present (skip while prefix absent)"
gd doctor | grep -i 'VARA wine hub'

echo "== gate 7: offscreen GUI selfcheck (install column + winehub section + tray=no)"
selfcheck=$(QT_QPA_PLATFORM=offscreen python3 "$here/control/gui/app.py" --selfcheck)
echo "$selfcheck"
echo "$selfcheck" | grep -q 'tray=no'
echo "$selfcheck" | grep -q 'apps=41'

echo "M5-SMOKE: PASS"
