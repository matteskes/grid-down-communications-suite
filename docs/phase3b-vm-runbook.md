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

> Paste `p3b-loopback.log` here after the §3 run.
