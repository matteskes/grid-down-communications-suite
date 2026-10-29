# Phase 3 VM runbook — VARA wine hub + config adapters in an x86_64 Fedora 45 VM

Goal (spec §10/§11 P3, §6.2/§6.4): install the user-supplied VARA HF/FM + VarAC
kits into the shared wine prefix (`/opt/grid-down/wine-prefix`), capture the
exe-adjacent INI inventory, pin the declared keys, and prove the §6.2 adapter
contract — masked read, declared-key apply with diff, timestamped backup and
atomic write — **from an air-gapped VM**.

**Carrier decision (locked 2026-10-28): x86_64 only.** wine 11.20 ships
x86_64/i686 packages and Fedora 45 carries no aarch64 wine
(`docs/audit/f45-package-audit.md` § *Wine availability*; spec §10). This Mac's
hypervisor runs arm guests only, so the live E2E is **deferred** until an
x86_64 carrier exists — this pass delivers the runbook, the guest driver, and
a rehearsal of every file-I/O piece on the existing aarch64 VM (`--rehearsal`,
appendix A). Nothing here is gated on the deferral except §5.

Builds on `docs/phase2-vm-runbook.md` (same VM mechanics, same evidence
pattern). What is new in this pass:

| Lane | P2 | P3 (this runbook) |
|---|---|---|
| Carrier arch | any (aarch64 reference VM) | **x86_64 only** (no aarch64 wine in F45) |
| Apps installed | dnf kit via `build-kit.sh` | user-supplied `install/kit-import/` zips (G6 — never committed) |
| Wine | absent | wine 11.20 (updates-testing) + one shared prefix for the VARA family |
| Install path | `build-kit.sh` at kit boot | `gd winehub init` + `gd winehub install` (silent Inno / portable zip) |
| Config | n/a | `gd config show\|apply` — exe-adjacent INIs, declared-key writes |
| Suite transfer | `scp` | `git archive` + **`vmpal cp`** (scp still works) |
| Out of scope here | — | COM loopback mapping (→ delivered by P3b: `docs/phase3b-vm-runbook.md`), Winlink handshake → next step after the live E2E |

Driver: `tools/vm-p3-wine-e2e.sh` — live mode (stages 0–7, §5) and
`--rehearsal` mode (R1–R4, §4, any arch, no wine).

## 1. Prep the guest (online: dnf + profile)

On the x86_64 Fedora 45 guest, while the network is still up:

```sh
sudo dnf -y update && sudo reboot                                # patch first (P1 convention)
sudo dnf install -y --enablerepo=updates-testing wine winetricks
rpm -q wine        # record the NVR in the evidence doc (audit convention)
sudo mkdir -p /opt/grid-down && sudo chown "$USER" /opt/grid-down
```

`winetricks` is installed for operator convenience only — the suite drives
`wine`/`wineboot` directly (`core/winehub.py`). `/opt/grid-down` must exist and
be user-writable *before* `gd winehub init` (the prefix lives at
`/opt/grid-down/wine-prefix`; the driver's stage 1 refuses with this exact fix
if not).

Station profile for the apply demo (never edit `profiles/default.yaml`;
`~/.config/grid-down/profiles/*.yaml` wins, newest by name):

```sh
mkdir -p ~/.config/grid-down/profiles
cat > ~/.config/grid-down/profiles/p3-e2e.yaml <<'EOF'
profile:
  id: p3-e2e
  callsign: "W1AW"
  grid: "FN31pr"
EOF
python3 control/cli/gd.py profile      # after §2's sync: must show callsign W1AW
```

## 2. Move the suite *and the three zips* into the VM (online)

The kit artifacts are gitignored (G6), so `git archive` does **not** carry
them — they travel separately, straight from the Mac's staging folder:

```sh
# on the Mac:
cd grid-down
git archive --format=tar.gz -o /tmp/grid-down-suite.tar.gz HEAD
vmpal cp /tmp/grid-down-suite.tar.gz "Fedora 45 Workstation":/var/tmp/

# on the guest (suite first, so install/kit-import/ exists):
mkdir -p /var/tmp/grid-down && tar -xzf /var/tmp/grid-down-suite.tar.gz -C /var/tmp/grid-down

# back on the Mac — the three zips land beside the tree's README (exact names,
# double space in the VARA HF one; sha256s in install/kit-import/README.md):
vmpal cp install/kit-import/. "Fedora 45 Workstation":/var/tmp/grid-down/install/kit-import/
```

(`vmpal cp <vm-name>:<abs-path>` needs the VM running and its agent control
on; plain `scp … <user>@<vm>:/var/tmp/` is the alternate. The driver's stage 1
verifies all three sha256s before anything runs.)

## 3. Air-gap

```sh
sudo nmcli networking off        # or detach the NIC — same gate as P1/P2
ping -c1 -W2 fedoraproject.org   # must fail
```

Wine needs nothing online at run time; installs come from the staged zips.

## 4. Rehearse on any VM (`--rehearsal`) — what it proves

Runs today on the existing aarch64 VM (and on the dev box):

```sh
sh /var/tmp/grid-down/tools/vm-p3-wine-e2e.sh --rehearsal 2>&1 | tee /var/tmp/p3-rehearsal.log
```

| Stage | Proves |
|---|---|
| R1 | fixture prefix builds: dummy `VARA.exe`/`VarAC.exe` + INIs (registration-shaped keys) + candidate-pin recipe copies |
| R2 | the real adapters: `read()` masks `Reg*` values, dry-run diff shows `+MyCall=W1AW`, apply writes with a timestamped backup, re-apply is "already in sync", empty-callsign profile refuses |
| R3 | `tools/gen-registry-stubs.py --check` still reports the baseline DIFF count (37 at 2026-10-28) — recipes stay generator-owned |
| R4 | the live arch guard refuses a non-x86_64 guest with exit 2 and the runbook pointer |

Expected tail: `P3-REHEARSAL: PASS`. The transcript from the reference aarch64
VM is in appendix A; paste yours there when re-running.

## 5. Live E2E (x86_64 carrier only — deferred)

### 5a. Pin the declared keys *first*

`gd config apply` writes only what `wine.ini_set` declares, and the recipes are
generated: add the per-app rows to the wine block of
`tools/gen-registry-stubs.py` (its `WINE_META` area), regenerate, and keep the
gates green — hand-editing `install/recipes/*.yaml` alone trips `--check`:

```sh
python3 tools/gen-registry-stubs.py            # regenerate with the pinned rows
python3 tools/gen-registry-stubs.py --check    # baseline DIFF count unchanged (37)
python3 -m pytest -q                           # 178 passed (count as of 2026-10-28)
git add -p && git commit                       # generator + recipes together
```

The key names come from stage 5's inventory (`p3-key-inventory/*.json`), never
from guesswork — rows template `{callsign}`/`{grid}` from the station profile.

### 5b. Run the driver

```sh
sh /var/tmp/grid-down/tools/vm-p3-wine-e2e.sh 2>&1 | tee /var/tmp/p3-e2e.log
```

Stages and their expected outputs:

```text
stage 0  arch: x86_64                                   (aarch64 → REFUSED, exit 2)
stage 1  wine-11.20 + 3× sha256sum OK + /opt writable
stage 2  gd winehub status → "prefix: present"
stage 3  install: vara-hf installed / vara-fm installed / varac installed
stage 4  vara-hf, varac: "INI present beside the exe" (≤ 600 s poll each;
         the app's window may appear on the VM desktop — stop closes it)
stage 5  p3-key-inventory/{vara-hf,vara-fm,varac}.{txt,json} under
         /var/tmp/grid-down-evidence/
stage 6  dry-run diff → "gd: wrote …(backup: …)" → "already in sync"
stage 7  doctor's "VARA wine hub" line + final status → P3-E2E: PASS
```

Stage 6 runs only once §5a's pins are in the recipes; on a pre-pin tree it
prints `SKIPPED` and the run still passes (everything else is pin-independent).

## 6. Report

Copy the evidence back and paste everything into
`docs/audit/phase3-wine-acceptance.md` (create it, same pattern as P1/P2):

```sh
vmpal cp "Fedora 45 Workstation":/var/tmp/grid-down-evidence /tmp/
vmpal cp "Fedora 45 Workstation":/var/tmp/p3-e2e.log /tmp/        # live run only
```

Record: `rpm -q wine` NVR, the three kit sha256s, the INI inventory, the
pinned `ini_set` rows with the apply diff, and the deferral note if the live
half ran later than this pass. Flip the README status line when the live E2E
closes P3 (spec §11). **The COM loopback half of that "next step" is
delivered by P3b** (`gd winehub bridge` + `gd audio wire`,
`docs/phase3b-vm-runbook.md`); the Winlink handshake stays with the live E2E.

## Appendix A — rehearsal transcript (aarch64 reference VM, 2026-10-28)

`git archive HEAD` → `vmpal cp` → extracted at `/var/tmp/grid-down` on the
Fedora 45 aarch64 VM; `sh tools/vm-p3-wine-e2e.sh --rehearsal` (identical run
also passes on the dev box — the mode is arch-agnostic by design):

```text
== rehearsal: any arch, no wine — fixture prefix + the real adapter code
arch: aarch64 (live mode refuses anything but x86_64 — stage 0)
fixture: /var/tmp/gd-p3-rehearsal.BBGXlH (removed at exit)
== R1: fixture prefix + candidate-pin recipes (dummy exes/INIs, masked reg keys)
  fixture ready: 2 exes, 2 INIs, 2 candidate-pin recipes
== R2: adapters on the fixture — read/mask, dry-run diff, write+backup, idempotence
  vara-hf: read ok — VARA.ini (2 masked keys)
  vara-hf: dry-run diff ok (11 lines, nothing written)
  vara-hf: empty-profile refusal ok
  vara-hf: wrote ok — backup 20261029-041335-VARA.ini keeps the old values
  vara-hf: idempotent ok
  varac: read ok — VarAC.ini (1 masked keys)
  varac: dry-run diff ok (10 lines, nothing written)
  varac: empty-profile refusal ok
  varac: wrote ok — backup 20261029-041335-VarAC.ini keeps the old values
  varac: idempotent ok
== R3: generator --check baseline (recipes stay generator-owned; dev-box baseline: 37 DIFFs)
  DIFF files: 37 (expected 37)
== R4: self-test — the live arch guard refuses this arch
== stage 0: arch guard (wine 11.20 is x86_64/i686 only — audit §wine)
arch: aarch64
  refused cleanly (exit 2, runbook pointer present)
P3-REHEARSAL: PASS
```
