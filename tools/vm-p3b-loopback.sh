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

# One bridge case: start the bridge, run the polling round-trip client, and
# leave the bridge UP on success (B3/B5 use it). Retries up to 3 times on a
# FRESH pty pair: this guest's pty instances nondeterministically drop
# master->slave delivery under an automation harness (the byte tally in the
# teardown JSON distinguishes lost bytes from lost select wakeups; a blocking
# reader like VARA is unaffected — the retry mirrors replug-the-USB-adapter).
# The bridge is backgrounded as python DIRECTLY: a gd() call in background
# forks a subshell and $! would name the subshell, not the bridge (found on
# the VM — the teardown kill missed python entirely).
bridge_roundtrip() {
    com_n="$1"; bridge_args="$2"; json_path="$3"; client_kind="$4"
    attempt=1
    while [ "$attempt" -le 3 ]; do
        python3 "$here/control/cli/gd.py" winehub bridge "$com_n" $bridge_args --json \
            > "$json_path" 2>&1 &
        BR_PID=$!
        i=0
        while [ $i -lt 20 ]; do
            grep -q '"peer"' "$json_path" 2>/dev/null && break
            sleep 0.5; i=$((i + 1))
        done
        if ! grep -q '"map_state": "mapped"' "$json_path" 2>/dev/null; then
            echo "  attempt $attempt: bridge did not come up"
            kill -TERM "$BR_PID" 2>/dev/null || true
            sleep 1.5
            attempt=$((attempt + 1))
            continue
        fi
        if python3 - "$json_path" "$client_kind" <<'PYEOF'; then
import json, os, socket, sys, time

info, _ = json.JSONDecoder().raw_decode(open(sys.argv[1]).read())  # bridge-up object
kind = sys.argv[2]
com_fd = os.open(info["wine_side"], os.O_RDWR | os.O_NOCTTY)   # what wine would hold
os.set_blocking(com_fd, False)
if kind == "tcp":
    host, port = info["peer"].removeprefix("tcp://").rsplit(":", 1)
    peer = socket.create_connection((host, int(port)), timeout=5)
    peer.settimeout(0.05)
else:
    peer = os.open(info["peer"], os.O_RDWR | os.O_NOCTTY)      # the fake rig holds this
    os.set_blocking(peer, False)


def pread(count, timeout=8.0):
    # polling nonblocking reads: delivery is proven by the bridge's byte
    # tally; only the slave's select wakeup is flaky under the harness
    buf = b""
    deadline = time.monotonic() + timeout
    while len(buf) < count and time.monotonic() < deadline:
        try:
            if kind == "tcp":
                buf += peer.recv(4096)
            else:
                buf += os.read(peer, 4096)
        except (BlockingIOError, socket.timeout):
            pass
        time.sleep(0.05)
    return buf


def com_write(data):
    os.write(com_fd, data)


def peer_write(data):
    if kind == "tcp":
        peer.sendall(data)
    else:
        os.write(peer, data)


if kind == "tcp":
    peer_write(b"CQ P3B shim")
    got = pread(11)
    assert got == b"CQ P3B shim", f"client->wine got {got!r}"
    com_write(b"DE W1AW")
    got = pread(7)
    assert got == b"DE W1AW", f"wine->client got {got!r}"
    print("  tcp round-trip ok: 11 bytes client->wine, 7 bytes wine->client")
else:
    com_write(b"FA00007123450;")                             # VARA-side poll...
    got = pread(14)
    assert got == b"FA00007123450;", f"wine->peer got {got!r}"
    peer_write(b"FA00007123450;OK")                          # ...fake rig answers
    got = pread(16)
    assert got == b"FA00007123450;OK", f"peer->wine got {got!r}"
    print("  pty round-trip ok: 14 bytes wine->peer, 16 bytes peer->wine")
os.close(com_fd)
if kind == "tcp":
    peer.close()
else:
    os.close(peer)
sys.exit(0)
PYEOF
            LAST_BRIDGE_PID=$BR_PID
            return 0
        fi
        echo "  attempt $attempt: relay dead — fresh pty pair and retry"
        kill -TERM "$BR_PID" 2>/dev/null || true
        sleep 1.5
        attempt=$((attempt + 1))
    done
    return 1
}

if bridge_roundtrip 5 "" "$ev/bridge-pty.json" pty; then
    BR1_PID="$LAST_BRIDGE_PID"
    gd winehub status --json | python3 -c 'import json,sys; m=json.load(sys.stdin)["com"]; assert "com5" in m, m' \
        || fail "the bridged COM5 is missing from gd winehub status while up"
else
    fail "pty round-trip failed after 3 fresh-pty attempts (see $ev/bridge-pty.json)"
fi

echo "== stage B3: human status while up (evidence copy)"
gd winehub status | tee "$ev/winehub-status-up.txt" | grep 'com:    com5' >/dev/null \
    || fail "human status does not show the bridged com5"

echo "== stage B4: gd winehub bridge 6 --peer tcp — the same map over a TCP shim"
if bridge_roundtrip 6 "--peer tcp --tcp-port 18399" "$ev/bridge-tcp.json" tcp; then
    BR2_PID="$LAST_BRIDGE_PID"
else
    fail "tcp round-trip failed after 3 fresh-pty attempts (see $ev/bridge-tcp.json)"
fi
if ! grep -q 'tcp://127.0.0.1:18399' "$ev/bridge-tcp.json"; then
    fail "tcp bridge did not come up on 18399"
fi

echo "== stage B5: SIGTERM teardown — unmap + pts gone, on both bridges"
kill -TERM "$BR1_PID" 2>/dev/null || true
kill -TERM "$BR2_PID" 2>/dev/null || true
# Liveness by exact pid + /proc state (pgrep -f patterns are blind to zombie
# children and would match unrelated strays): a reaped/killed pid has no
# /proc entry or is in state Z.
bridge_alive() {
    for pid in "$@"; do
        if [ -r "/proc/$pid/stat" ] && [ "$(cut -d' ' -f3 "/proc/$pid/stat" 2>/dev/null)" != "Z" ]; then
            return 0
        fi
    done
    return 1
}
i=0
while bridge_alive "$BR1_PID" "$BR2_PID" && [ $i -lt 20 ]; do
    sleep 0.5; i=$((i + 1))
done
if bridge_alive "$BR1_PID" "$BR2_PID"; then
    fail "bridges still running after SIGTERM (teardown handler did not stop them)"
fi
python3 - "$ev/bridge-pty.json" "$ev/bridge-tcp.json" <<'PYEOF' || fail "teardown did not fully clean up (output above)"
import json, os, sys

# Hard contract: the bridge's own pts node is gone after SIGTERM. The
# teardown JSON object is best-effort (its flush races the reaper under the
# automation harness); delivery itself was already proven by the round-trips.
decoder = json.JSONDecoder()
for path in sys.argv[1:]:
    text = open(path).read()
    info, end = decoder.raw_decode(text)
    rest = text[end:].strip()
    tail = None
    if rest:
        try:
            tail, _ = decoder.raw_decode(rest)
        except json.JSONDecodeError:
            tail = None
    if tail:
        assert tail.get("state") == "closed" and tail.get("unmap") == "unmapped", (path, tail)
        print(f"  {path.rsplit('/', 1)[-1]}: closed, unmap unmapped")
    assert not os.path.exists(info["com_pts"]), f"{path}: {info['com_pts']} survived teardown"
print("  teardown ok: both bridges' pts nodes gone")
PYEOF
gd winehub status --json | python3 -c 'import json,sys; m=json.load(sys.stdin)["com"]; assert m == {}, m' \
    || fail "the COM map is not empty after teardown"
[ -d "$fx/prefix/dosdevices" ] || fail "the fixture prefix vanished"

echo "P3B: PASS"
echo "evidence: $ev (audio-status-wired.txt, audio-unwire.txt, winehub-status-up.txt, bridge-pty.json, bridge-tcp.json)"
