#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# P3b loopback rehearsal (guest-side driver) — run INSIDE the guest after a
# `git archive` sync of the tree (docs/phase3b-vm-runbook.md):
#
#   git archive --format=tar.gz -o /tmp/grid-down-suite.tar.gz HEAD
#   vmpal cp /tmp/grid-down-suite.tar.gz "<vm>":/var/tmp/      # or scp
#   # on the guest (inside the graphical session — PipeWire's user bus):
#   mkdir -p /var/tmp/grid-down && tar -xzf /var/tmp/grid-down-suite.tar.gz -C /var/tmp/grid-down
#   sh /var/tmp/grid-down/tools/vm-p3b-loopback.sh 2>&1 | tee /var/tmp/p3b-loopback.log
#
# One mode, any arch — that is the point of P3b: no wine, no x86_64 carrier,
# no new packages (pactl comes with pipewire-pulse, already on the F45 GUI VM).
#
# Stage A (rig-audio wiring, core/audio.py): a stand-in "rig card" (a null
# sink + its monitor), a fixture profile naming it via audio.rig_capture /
# audio.rig_playback, then `gd audio up | wire | status | unwire | down` —
# including the safety proof that a user's own loopback and null sink are
# never touched by unwire/down.
#
# Stage B (pty-pair COM harness, core/ptybridge.py): GRID_DOWN_WINE_PREFIX
# points at a fixture prefix (no wine), `gd winehub bridge 5` maps COM5 to a
# real pty through the wine hub's map_serial, a script opens BOTH slave ends
# and proves byte round-trips both directions, `gd winehub status` shows the
# map while up, SIGTERM tears it down (unmap + ptys gone), and a TCP-peer
# bridge gets the same treatment on COM6.
#
# Exits non-zero on the first failed gate; prints P3B: PASS at the end.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
gd() { python3 "$here/control/cli/gd.py" "$@"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

ev=/var/tmp/grid-down-evidence/p3b
fx=/var/tmp/gd-p3b-fixture
RIG_MOD=""; USER_SINK_MOD=""; USER_LB_MOD=""; BR1_PID=""; BR2_PID=""

cleanup() {
    if [ -n "$BR1_PID" ]; then kill -TERM "$BR1_PID" 2>/dev/null; fi
    if [ -n "$BR2_PID" ]; then kill -TERM "$BR2_PID" 2>/dev/null; fi
    if [ -n "$RIG_MOD" ]; then pactl unload-module "$RIG_MOD" 2>/dev/null; fi
    if [ -n "$USER_SINK_MOD" ]; then pactl unload-module "$USER_SINK_MOD" 2>/dev/null; fi
    if [ -n "$USER_LB_MOD" ]; then pactl unload-module "$USER_LB_MOD" 2>/dev/null; fi
    python3 "$here/control/cli/gd.py" audio unwire >/dev/null 2>&1
    python3 "$here/control/cli/gd.py" audio down >/dev/null 2>&1
    rm -rf "$fx"
    return 0
}
trap cleanup EXIT INT TERM

echo "== stage 0: sanity (python3, pactl/PipeWire, arch — any arch passes)"
echo "arch: $(uname -m)"
command -v python3 >/dev/null || fail "python3 not on PATH"
command -v pactl >/dev/null || fail "pactl not found — dnf install pipewire-pulse (runbook §1)"
pactl info >/dev/null 2>&1 || fail "no reachable PipeWire/Pulse session — run inside the graphical session (runbook §1)"
mkdir -p "$ev"

echo "== stage A1: stand-in rig card (null sink + its .monitor source)"
RIG_MOD=$(pactl load-module module-null-sink sink_name=p3b_rig_stand_in node.name=p3b_rig_stand_in)
pactl list short sources | grep -q p3b_rig_stand_in.monitor || fail "stand-in rig monitor did not appear"
echo "  rig card: p3b_rig_stand_in (+ .monitor), pactl module $RIG_MOD"

echo "== stage A2: fixture profile (GRID_DOWN_PROFILE — the repo template is never touched)"
mkdir -p "$fx"
cat > "$fx/profile.yaml" <<EOF
profile:
  id: p3b-loopback
  callsign: "W1AW"
  grid: "FN31pr"
audio:
  backend: pipewire
  rig_capture: p3b_rig_stand_in.monitor
  rig_playback: p3b_rig_stand_in
  loopback_roles: [hf]
EOF
export GRID_DOWN_PROFILE="$fx/profile.yaml"
gd profile >/dev/null || fail "fixture profile did not load"
echo "  profile: $fx/profile.yaml (rig_capture + rig_playback -> the stand-in card)"

echo "== stage A3: gd audio up | wire | status — the owned graph, then the rig loopbacks"
gd audio up | tail -2
gd audio status | grep '^missing: (none)' >/dev/null || fail "gd audio up did not create all six endpoints (status above)"
wired=$(gd audio wire | grep -c '^wired:' || true)
[ "$wired" = "2" ] || fail "expected 2 wired loopbacks (hf.rx + hf.tx), got $wired"
gd audio wire | grep -q 'already wired' && echo "  re-wire is idempotent" \
    || fail "gd audio wire is not idempotent"
gd audio status | tee "$ev/audio-status-wired.txt" | grep -c '^loopback: module-loopback' \
    | grep -q '^2$' || fail "audio status does not report the two wired loopbacks"
gd audio plan | grep 'hf.rx' | grep -q planned || fail "audio plan does not show the planned rows"
echo "  wired: hf.rx (rig_capture -> grid_down_hf_out), hf.tx (grid_down_hf_in.monitor -> rig_playback)"

echo "== stage A4: teardown safety — a user's own loopback and sink are never touched"
USER_SINK_MOD=$(pactl load-module module-null-sink sink_name=p3b_user_desktop node.name=p3b_user_desktop)
USER_LB_MOD=$(pactl load-module module-loopback source=p3b_rig_stand_in.monitor sink=p3b_user_desktop)
gd audio unwire | tee "$ev/audio-unwire.txt" | grep -q 'unloaded loopback modules: ' \
    || fail "gd audio unwire produced no unload line"
gd audio unwire | grep -q 'unloaded loopback modules: (none)' || fail "unwire is not idempotent"
pactl list short modules | grep -q 'sink_name=p3b_user_desktop' || fail "unwire touched the user null sink"
pactl list short modules | grep -q 'sink=p3b_user_desktop' || fail "unwire touched the user loopback"
gd audio down >/dev/null
pactl list short sinks | grep -q grid_down_ && fail "gd audio down left owned endpoints behind"
pactl list short modules | grep -q 'sink_name=p3b_user_desktop' || fail "down touched the user null sink"
pactl list short modules | grep -q 'sink=p3b_user_desktop' || fail "down touched the user loopback"
echo "  safety: unwire/down unloaded only grid_down_ modules — user gear untouched"

echo "== stage B1: fixture wine prefix (GRID_DOWN_WINE_PREFIX — no wine on this box)"
mkdir -p "$fx/prefix/dosdevices"
export GRID_DOWN_WINE_PREFIX="$fx/prefix"
gd winehub status | grep -q 'prefix: present' || fail "GRID_DOWN_WINE_PREFIX was not honoured"

echo "== stage B2: gd winehub bridge 5 (pty peer) — map, round-trip, live map"
# Background python DIRECTLY (not via the gd() function): a function call in
# background forks a subshell, and $! would name that subshell instead of the
# bridge — the teardown kill would miss python entirely (found live on the VM).
python3 "$here/control/cli/gd.py" winehub bridge 5 --json > "$ev/bridge-pty.json" 2>&1 &
BR1_PID=$!
i=0
while [ $i -lt 20 ]; do
    grep -q '"peer"' "$ev/bridge-pty.json" 2>/dev/null && break
    sleep 0.5; i=$((i + 1))
done
grep -q '"map_state": "mapped"' "$ev/bridge-pty.json" || fail "bridge did not map COM5 (see $ev/bridge-pty.json)"
gd winehub status --json | python3 -c 'import json,sys; m=json.load(sys.stdin)["com"]; assert "com5" in m, m' \
    || fail "the bridged COM5 is missing from gd winehub status while up"
python3 - "$ev/bridge-pty.json" <<'PYEOF' || fail "pty round-trip failed (script output above)"
import json, os, select, sys, time

info, _ = json.JSONDecoder().raw_decode(open(sys.argv[1]).read())  # first object = bridge-up
com_fd = os.open(info["wine_side"], os.O_RDWR | os.O_NOCTTY)   # what wine would hold
rig_fd = os.open(info["peer"], os.O_RDWR | os.O_NOCTTY)        # what the fake rig holds


def read_exact(fd, count, timeout=5.0):
    buf = b""
    deadline = time.monotonic() + timeout
    while len(buf) < count:
        left = deadline - time.monotonic()
        if left <= 0:
            raise SystemExit(f"FAIL: timed out reading {count} bytes (got {len(buf)})")
        r, _, _ = select.select([fd], [], [], min(left, 0.5))
        if r:
            buf += os.read(fd, count - len(buf))
    return buf


os.write(com_fd, b"FA00007123450;")            # VARA-side grid locator poll ...
assert read_exact(rig_fd, 14) == b"FA00007123450;"
os.write(rig_fd, b"FA00007123450;OK")          # ... fake rig answers
assert read_exact(com_fd, 16) == b"FA00007123450;OK"
os.close(com_fd)
os.close(rig_fd)
print("  pty round-trip ok: 14 bytes wine->peer, 16 bytes peer->wine")
PYEOF

echo "== stage B3: human status while up (evidence copy)"
gd winehub status | tee "$ev/winehub-status-up.txt" | grep 'com:    com5' >/dev/null \
    || fail "human status does not show the bridged com5"

echo "== stage B4: gd winehub bridge 6 --peer tcp — the same map over a TCP shim"
python3 "$here/control/cli/gd.py" winehub bridge 6 --peer tcp --tcp-port 18399 --json \
    > "$ev/bridge-tcp.json" 2>&1 &
BR2_PID=$!
i=0
while [ $i -lt 20 ]; do
    grep -q '"peer"' "$ev/bridge-tcp.json" 2>/dev/null && break
    sleep 0.5; i=$((i + 1))
done
grep -q 'tcp://127.0.0.1:18399' "$ev/bridge-tcp.json" || fail "tcp bridge did not come up on 18399"
python3 - "$ev/bridge-tcp.json" <<'PYEOF' || fail "tcp round-trip failed (script output above)"
import json, os, select, socket, sys, time

info, _ = json.JSONDecoder().raw_decode(open(sys.argv[1]).read())
com_fd = os.open(info["wine_side"], os.O_RDWR | os.O_NOCTTY)
client = socket.create_connection(("127.0.0.1", 18399), timeout=5)


def read_exact(fd, count, timeout=5.0):
    buf = b""
    deadline = time.monotonic() + timeout
    while len(buf) < count:
        left = deadline - time.monotonic()
        if left <= 0:
            raise SystemExit(f"FAIL: timed out reading {count} bytes (got {len(buf)})")
        r, _, _ = select.select([fd], [], [], min(left, 0.5))
        if r:
            buf += os.read(fd, count - len(buf))
    return buf


client.sendall(b"CQ P3B shim")
assert read_exact(com_fd, 11) == b"CQ P3B shim"
os.write(com_fd, b"DE W1AW")
client.settimeout(5)
assert client.recv(7) == b"DE W1AW"
os.close(com_fd)
client.close()
print("  tcp round-trip ok: 11 bytes client->wine, 7 bytes wine->client")
PYEOF

echo "== stage B5: SIGTERM teardown — unmap + pts gone, on both bridges"
kill -TERM "$BR1_PID"; kill -TERM "$BR2_PID"
wait "$BR1_PID" 2>/dev/null || true
wait "$BR2_PID" 2>/dev/null || true
BR1_PID=""; BR2_PID=""
python3 - "$ev/bridge-pty.json" "$ev/bridge-tcp.json" <<'PYEOF' || fail "teardown did not fully clean up (output above)"
import json, os, sys

decoder = json.JSONDecoder()
for path in sys.argv[1:]:
    text = open(path).read()
    info, end = decoder.raw_decode(text)          # bridge-up object
    tail, _ = decoder.raw_decode(text[end:].lstrip())  # teardown object
    assert tail["state"] == "closed" and tail["unmap"] == "unmapped", (path, tail)
    for pts in (info["com_pts"], info["peer"]):
        assert not os.path.exists(pts), f"{path}: {pts} survived teardown"
print("  teardown ok: both bridges closed with unmap: unmapped, both pts nodes gone")
PYEOF
gd winehub status --json | python3 -c 'import json,sys; m=json.load(sys.stdin)["com"]; assert m == {}, m' \
    || fail "the COM map is not empty after teardown"
[ -d "$fx/prefix/dosdevices" ] || fail "the fixture prefix vanished"

echo "P3B: PASS"
echo "evidence: $ev (audio-status-wired.txt, audio-unwire.txt, winehub-status-up.txt, bridge-pty.json, bridge-tcp.json)"
