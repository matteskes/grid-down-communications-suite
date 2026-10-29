# Phase 3b VM runbook — rig-audio wiring + the pty-pair COM harness on the aarch64 VM

Goal (spec §6.4/§6.6 P3b): close every arm-testable piece of P3 — the PipeWire
rig-audio loopbacks (`gd audio wire|unwire`) and the pty-pair COM harness
(`gd winehub bridge`) — **on the existing aarch64 Fedora 45 VM**, with no wine,
no x86_64 carrier, and no new packages. The on-air Winlink E2E stays x86_64-gated
(`docs/phase3-vm-runbook.md` §0); nothing else of P3 is blocked on it after this
pass.

Builds on `docs/phase3-vm-runbook.md` (same VM, same transfer discipline, same
evidence pattern). What is different:

| Lane | P3 wine E2E | P3b (this runbook) |
|---|---|---|
| Carrier arch | **x86_64 only** (no aarch64 wine) | **any** — no wine anywhere in the path |
| Mode | live + `--rehearsal` | one mode (the whole point) |
| Wine hub role | installs + INI adapters | COM map only: `map_serial`/`unmap` via `gd winehub bridge` |
| Audio role | n/a | `gd audio wire` loops the rig card into the owned endpoints |
| New deps | wine 11.20 | none (pactl ships with pipewire-pulse) |
| Still open after | COM loopback, Winlink handshake | on-air Winlink E2E only |

Driver: `tools/vm-p3b-loopback.sh` — stage A (audio) + stage B (pty), one PASS
banner. Run it **inside the graphical session** (PipeWire's user bus lives
there; over SSH without a session bus, `pactl info` fails with a clear message).

## 1. Prep (nothing to install)

The F45 aarch64 GUI VM from the P2/P3 passes already has everything:

```sh
pactl info                 # must print Server Name: ... (session bus check)
python3 --version          # 3.13 on F45
uname -m                   # aarch64 — recorded in the transcript
```

## 2. Move the suite into the VM (reuse the P3 recipe)

```sh
# on the Mac:
cd grid-down
git archive --format=tar.gz -o /tmp/grid-down-suite.tar.gz HEAD
sha256sum /tmp/grid-down-suite.tar.gz                 # copy for the two-end check
vmpal cp /tmp/grid-down-suite.tar.gz "Fedora 45 Workstation":/var/tmp/
# on the guest:
sha256sum /var/tmp/grid-down-suite.tar.gz             # must match the Mac's line
mkdir -p /var/tmp/grid-down && tar -xzf /var/tmp/grid-down-suite.tar.gz -C /var/tmp/grid-down
```

Never unpack with macOS bsdtar into the VM and never skip the two sha256s —
the P3 transfer discipline carries over verbatim.

## 3. Run the driver

```sh
cd /var/tmp/grid-down
sh tools/vm-p3b-loopback.sh 2>&1 | tee /var/tmp/p3b-loopback.log
```

Stages and their expected outputs:

```text
stage 0  arch: aarch64 + pactl session check
stage A1 rig card stand-in: p3b_rig_stand_in (+ .monitor), pactl module N
stage A2 fixture profile via GRID_DOWN_PROFILE (repo template never touched)
stage A3 gd audio up (6 endpoints) → wire (2: hf.rx + hf.tx) → status shows
         both loopback lines; re-wire is idempotent
stage A4 unwire + down leave a user null sink and a user loopback untouched
stage B1 GRID_DOWN_WINE_PREFIX fixture prefix: "prefix: present", no wine
stage B2 gd winehub bridge 5 --json: COM5 -> pts mapped; script opens BOTH
         slave ends and round-trips 14 bytes each way; gd winehub status
         shows com5 while up
stage B3 human status evidence copy
stage B4 bridge 6 --peer tcp 18399: 11/7-byte round-trip through a socket shim
stage B5 SIGTERM both bridges: "unmap: unmapped", pts nodes gone, COM map empty
P3B: PASS
```

On the Mac dev box the same script also passes (it is arch-agnostic by
design); the VM run is the acceptance evidence.

## 4. Report

```sh
vmpal cp "Fedora 45 Workstation":/var/tmp/p3b-loopback.log /tmp/
vmpal cp "Fedora 45 Workstation":/var/tmp/grid-down-evidence/p3b /tmp/   # 5 files
```

Paste the transcript into Appendix A below and flip the README status line.
**What remains of P3 after this pass:** the on-air Winlink E2E only — VARA
install + INI pins + handshake on an x86_64 carrier (`docs/phase3-vm-runbook.md`
§5–§6), unchanged.

## Appendix A — VM transcript (aarch64 reference VM, 2026-10-28)

Suite sync: `git archive` of the P3b head, sha256
`ca7f21b6f570e836ea69a6acaf14da6264e7be022178dc2f75d47ceff72a504e`,
verified identical on the Mac and on the guest before extraction.
Full pytest on the Mac: **192 passed**, ruff/black/isort clean.

**Run 3 of 3** (runs 1–3 all `P3B: PASS`, `DRIVER_EXIT=0`; run 1 and 3
printed identically from stage 0 through teardown, run 2 tail-verified with
the script's exit status preserved through `${PIPESTATUS}`):

```text
== stage 0: sanity (python3, pactl/PipeWire, arch — any arch passes)
arch: aarch64
== stage A1: stand-in rig card (null sink + its .monitor source)
  rig card: p3b_rig_stand_in (+ .monitor), pactl module 536870919
== stage A2: fixture profile (GRID_DOWN_PROFILE — the repo template is never touched)
  profile: /var/tmp/gd-p3b-fixture/profile.yaml (rig_capture + rig_playback -> the stand-in card)
== stage A3: gd audio up | wire | status — the owned graph, then the rig loopbacks
created: grid_down_voice_in (pactl module 536870926)
created: grid_down_voice_out (pactl module 536870925)
  re-wire is idempotent
  wired: hf.rx (rig_capture -> grid_down_hf_out), hf.tx (grid_down_hf_in.monitor -> rig_playback)
== stage A4: teardown safety — a user's own loopback and sink are never touched
  safety: unwire/down unloaded only grid_down_ modules — user gear untouched
== stage B1: fixture wine prefix (GRID_DOWN_WINE_PREFIX — no wine on this box)
== stage B2: gd winehub bridge 5 (pty peer) — map, round-trip, live map
  pty round-trip ok: 14 bytes wine->peer, 16 bytes peer->wine
== stage B3: human status while up (evidence copy)
== stage B4: gd winehub bridge 6 --peer tcp — the same map over a TCP shim
  tcp round-trip ok: 11 bytes client->wine, 7 bytes wine->client
== stage B5: SIGTERM teardown — unmap + pts gone, on both bridges
  bridge-pty.json: closed, unmap unmapped
  bridge-tcp.json: closed, unmap unmapped
  teardown ok: both bridges' pts nodes gone
P3B: PASS
evidence: /var/tmp/grid-down-evidence/p3b (audio-status-wired.txt, audio-unwire.txt, winehub-status-up.txt, bridge-pty.json, bridge-tcp.json)
DRIVER_EXIT=0
```

Evidence files (guest `/var/tmp/grid-down-evidence/p3b/`, verbatim):

```text
--- audio-status-wired.txt ---
present: grid_down_hf_in, grid_down_hf_out, grid_down_packet_in, grid_down_packet_out, grid_down_voice_in, grid_down_voice_out
missing: (none)
loopback: module-loopback source=grid_down_hf_in.monitor sink=p3b_rig_stand_in
loopback: module-loopback source=p3b_rig_stand_in.monitor sink=grid_down_hf_out

--- audio-unwire.txt ---
unloaded loopback modules: 536870923, 536870924

--- winehub-status-up.txt ---
prefix: present (/var/tmp/gd-p3b-fixture/prefix)
wine:   not on PATH
com:    com5 -> /dev/pts/0
app:    vara-chat        stopped  [Chat.exe not installed (gd winehub install vara-chat)]
app:    vara-fm          stopped  [FM.exe not installed (gd winehub install vara-fm)]
app:    vara-hf          stopped  [VARA.exe not installed (gd winehub install vara-hf)]
app:    vara-sat         stopped  [SAT.exe not installed (gd winehub install vara-sat)]
app:    vara-term        stopped  [Terminal.exe not installed (gd winehub install vara-term)]
app:    varac            stopped  [VarAC.exe not installed (gd winehub install varac)]

--- bridge-pty.json (up, then SIGTERM teardown with byte tally) ---
{"com": "com5", "wine_side": "/var/tmp/gd-p3b-fixture/prefix/dosdevices/com5",
 "com_pts": "/dev/pts/0", "peer": "/dev/pts/1", "peer_kind": "pty", "map_state": "mapped"}
{"state": "closed", "com": "com5", "unmap": "unmapped",
 "bytes": {"wine_to_peer": 14, "peer_to_wine": 16}}

--- bridge-tcp.json (up, then SIGTERM teardown with byte tally) ---
{"com": "com6", "wine_side": "/var/tmp/gd-p3b-fixture/prefix/dosdevices/com6",
 "com_pts": "/dev/pts/2", "peer": "tcp://127.0.0.1:18399", "peer_kind": "tcp", "map_state": "mapped"}
{"state": "closed", "com": "com6", "unmap": "unmapped",
 "bytes": {"wine_to_peer": 7, "peer_to_wine": 11}}
```

> Note from the first pass attempt (same day, fixed before the recorded
> runs): stage B2 failed deterministically — not a lost slave-side select
> wakeup this time, but a harness-client bug: the merged round-trip reader
> read the peer fd on both legs. The bridge's SIGTERM tally in
> `bridge-pty.json` (`peer_to_wine: 16`) proved the bridge had delivered,
> which localized the fault to the client in one step — the tally doing
> exactly the job it was built for. The fresh-pty-per-attempt retry stays:
> it remains the designed answer to the genuinely lost slave-side select
> wakeups root-caused on macOS (blocking pty masters, driver-side retry;
> see §2 and `control/core/ptybridge.py`).

