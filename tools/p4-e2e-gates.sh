#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
#
# tools/p4-e2e-gates.sh — Phase 4 M10 container E2E: gate stages.
#
# Runs INSIDE the linux/amd64 station container (Rosetta, Docker Desktop),
# after `bootstrap.sh --kit /kit` installed the full x86_64 kit offline.
# tools/container-p4-e2e.sh is the host-side orchestrator that mounts
#   /kit  — the kit staging tree, read-only (repo: repodata, payloads, manifest)
#   /gd   — the operator checkout, read-only (tools/, control/, install/lib)
# and runs this script with the network switched OFF (`--network none`).
#
# Gate set mirrors the M9 VM round (docs/phase4-plan.md M9 evidence) with the
# mesh bundle's no-handset skip standing in for the live handset session
# (hardware-gated; phase4-plan M10 caveat). `set -euo pipefail`, first failure
# exits nonzero via fail(); the final line on success is exactly "P4: PASS",
# which the orchestrator greps per run (3x PASS discipline).
set -euo pipefail

GD_ROOT=/gd            # operator checkout (read-only mount)
KIT=/kit               # kit staging tree (read-only mount)
BACKEND=direct         # no systemd user session in a container (spec §6.5)

fail() { printf 'P4-FAIL: %s\n' "$*" >&2; exit 1; }
stage() { printf '\n== %s\n' "$*"; }
gd() { python3 "$GD_ROOT/control/cli/gd.py" "$@"; }

# --- fresh-station profile (P3 e2e values; GRID_DOWN_PROFILE, §4) ------------
export GRID_DOWN_PROFILE=/tmp/p4-e2e-profile.yaml
printf 'profile:\n  callsign: W1AW\n  grid: FN31pr\n' > "$GRID_DOWN_PROFILE"

stage "G0 — arch guard + mounts"
[ "$(uname -m)" = "x86_64" ] || fail "arch is $(uname -m), want x86_64 (P4 kits are x86_64-only)"
[ -d "$KIT/repo/repodata" ] || fail "kit repo missing at $KIT/repo"
[ -d "$GD_ROOT/control/registry/apps" ] || fail "operator checkout missing at $GD_ROOT"

stage "G1 — kit manifest verify (the shipped verifier, strict)"
GD_ROOT="$GD_ROOT" . "$GD_ROOT/install/lib/common.sh"
. "$GD_ROOT/install/lib/kit.sh"
gd_kit_verify "$KIT"

stage "G2 — kit dnf set: installer tooling + app runtime rows"
command -v bsdtar >/dev/null || fail "bsdtar absent — installer tooling must ride the kit (M10 finding #4)"
command -v python3.14 >/dev/null || fail "python3.14 absent — meshtastic's dnf row must ride the kit (M10 finding #5)"
printf '  bsdtar: %s · python3.14: %s\n' "$(bsdtar --version | cut -d' ' -f1-2)" "$(python3.14 --version)"

stage "G3 — payload placement: build-strategy ELF, tarball, static, wheels"
for p in /opt/grid-down/js8spotter \
         /opt/grid-down/meshtastic/venv/bin/meshtastic \
         /opt/grid-down/meshtastic-web; do
    [ -e "$p" ] || fail "payload missing: $p"
done
[ -e /opt/grid-down/js8spotter/js8spotter.py ] || fail "js8spotter payload incomplete (js8spotter.py missing — flatten?)"
[ -e /opt/grid-down/js8spotter/azure.tcl ] || fail "js8spotter payload incomplete (azure.tcl missing)"
[ -n "$(ls -A /opt/grid-down/meshtastic-web)" ] || fail "meshtastic-web docroot empty (operator content missing)"
ARDOPCF="$(python3 - "$GD_ROOT" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "control"))
from core import registry
apps = registry.load_registry(Path(sys.argv[1]) / "control" / "registry" / "apps")
print(next(a["launch"]["cmd"].split()[0] for a in apps if a["id"] == "ardop"))
PY
)"
[ -x "$ARDOPCF" ] || fail "build payload binary missing at $ARDOPCF (kit/opt placement)"
file "$ARDOPCF" | grep -q "ELF 64-bit" || fail "not ELF: $(file -b "$ARDOPCF")"
file "$ARDOPCF" | grep -q "x86-64" || fail "not x86-64: $(file -b "$ARDOPCF")"
printf '  elf: %s — %s\n' "$ARDOPCF" "$(file -b "$ARDOPCF" | cut -c1-60)"
printf '  /opt/grid-down: %s payload dirs\n' \
    "$(find /opt/grid-down -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"

stage "G4 — meshtastic real CLI (kit venv), offline pins"
MT=/opt/grid-down/meshtastic/venv/bin/meshtastic
"$MT" --version | grep -q "2\.7\.11" || fail "meshtastic --version is not the pin (2.7.11): $("$MT" --version)"
MT_HELP="$(python3 - "$GD_ROOT" <<'PY'
import subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "control"))
from core import adapters, registry
apps = {a["id"]: a for a in registry.load_registry(root / "control" / "registry" / "apps")}
ada = adapters.get_adapter(apps["meshtastic"], install_root="/opt/grid-down")
cli = ada.resolve_cli()
assert cli is not None, "meshtastic CLI not resolved"
help_text = subprocess.run([str(cli), "configure", "--help"], capture_output=True, text=True)
print((help_text.stdout or "") + (help_text.stderr or ""))
PY
)"
for flag in --seturl --port --host; do
    printf '%s' "$MT_HELP" | grep -q -- "$flag" \
        || fail "meshtastic configure surface missing $flag (live-E2E pin — update _target_args/_commands if upstream renamed it):
$MT_HELP"
done
printf '  %s — configure surface ok (--seturl --port --host)\n' "$("$MT" --version)"

stage "G5 — adapter render, spawn-free (dry_run): target args, payload, PSK mask"
python3 - "$GD_ROOT" <<'PY' || fail "adapter pins failed"
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "control"))
from core import adapters, registry  # noqa: E402

apps = {a["id"]: a for a in registry.load_registry(root / "control" / "registry" / "apps")}
ada = adapters.get_adapter(apps["meshtastic"], install_root="/opt/grid-down")
assert str(ada.resolve_cli()) == "/opt/grid-down/meshtastic/venv/bin/meshtastic", ada.resolve_cli()

profile = {
    "profile": {"callsign": "W1AW", "grid": "FN31pr"},
    "mesh": {
        "device": {"type": "tcp", "host": "127.0.0.1"},
        "node": {
            "long_name": "P4 E2E",
            "short_name": "P4E2",
            "region": "UNSET",
            "modem_preset": "LONG_FAST",
            "channel_url": "https://meshtastic.org/e/#CgcIAQgBEBAQGgMQAw",
        },
    },
}
dry = ada.apply(profile, dry_run=True)  # spawn-free by contract
assert dry["dry_run"] and dry["changed"] is False, dry
assert dry["target"] == "tcp 127.0.0.1", dry["target"]
cmds = dry["commands"]
assert any("--host" in c and "127.0.0.1" in c for c in cmds), cmds
assert any("--seturl ***" in c for c in cmds), cmds
assert not any("CgcIAQgBEBAQ" in c for c in cmds), "PSK leaked into display"
assert dry["payload"]["config"]["user"]["long_name"] == "P4 E2E", dry["payload"]
assert dry["payload"]["config"]["lora"]["region"] == "UNSET", dry["payload"]
print("  dry-run render ok: tcp 127.0.0.1, --seturl masked, payload 2 sections")

for bad, why in (
    ({"profile": {"callsign": "W1AW"}}, "no mesh block"),
    ({
        "profile": {"callsign": "W1AW"},
        "mesh": {"device": {"type": "serial"}, "node": {"long_name": "x"}},
    }, "serial without path"),
    ({
        "profile": {"callsign": "W1AW"},
        "mesh": {
            "device": {"type": "tcp", "host": "127.0.0.1"},
            "daemon": {"enabled": True},
            "node": {"long_name": "x"},
        },
    }, "daemon.enabled true"),
):
    try:
        ada.apply(bad, dry_run=True)
        raise SystemExit(f"FAIL: apply accepted {why}")
    except adapters.ConfigError as exc:
        print(f"  refusal ok ({why}): {str(exc)[:72]}…")
PY

stage "G6 — config surface: gd config show meshtastic"
gd config show meshtastic 2>&1 | tee /tmp/g6.out | sed 's/^/  /'
grep -q "venv/bin/meshtastic" /tmp/g6.out || fail "config show: kit venv CLI not probed"

stage "G7 — js8spotter refuses as a service (Tcl/Tk desktop app; M8 php contract corrected)"
if gd launch --backend "$BACKEND" js8spotter 2>/tmp/g7.err; then
    fail "js8spotter launched as a service — it is a desktop app (M10 correction)"
fi
sed 's/^/  /' /tmp/g7.err
grep -q "interactive CLI tool" /tmp/g7.err || fail "js8spotter refusal text unexpected (want the cli-tool refusal)"

stage "G8 — mesh bundle: no-handset skip-with-notes, web :8092 200"
MESH_OUT="$(gd launch --backend "$BACKEND" mesh)" || fail "gd launch mesh exited nonzero"
printf '%s\n' "$MESH_OUT" | sed 's/^/  /'
printf '%s' "$MESH_OUT" | grep -q "skip meshtastic" || fail "expected a 'skip meshtastic' note (no handset in a container)"
curl -fsS -o /dev/null http://127.0.0.1:8092/ || fail "meshtastic-web :8092 no 200"
printf '  :8092 200 ok\n'
gd stop --backend "$BACKEND" mesh
if curl -fsS -o /dev/null --max-time 2 http://127.0.0.1:8092/; then
    fail "meshtastic-web still answering after stop"
fi

stage "G9 — meshtasticd refuses while its upstream payload is not installed"
if gd launch --backend "$BACKEND" meshtasticd 2>/tmp/g9.err; then
    fail "meshtasticd launched without its daemon installed"
fi
sed 's/^/  /' /tmp/g9.err
grep -Eq "not found|not installed" /tmp/g9.err || fail "meshtasticd refusal text unexpected"

stage "G10 — doctor: 0 fail"
# no --backend on doctor: with no XDG_RUNTIME_DIR / user bus, auto resolves direct
gd doctor || fail "gd doctor exited nonzero"

stage "G11 — status snapshot + stop all"
gd status --backend "$BACKEND" | sed 's/^/  /'
gd stop --backend "$BACKEND" all || true

printf '\nP4: PASS\n'
