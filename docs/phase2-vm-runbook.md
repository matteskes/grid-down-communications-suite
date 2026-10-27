# Phase 2 VM runbook — GUI + systemd user-unit acceptance in a Fedora 45 GUI VM

Goal (spec §11 P2 acceptance): **bundle start/stop from the Control Center on a
real Fedora 45 desktop with networking removed**, carried by the systemd
user-unit backend — the "real `systemctl --user` acceptance" that M4a deferred
here (see `control/core/systemd.py` header + spec §10). Any hypervisor, either
arch — x86_64 is the fleet default; aarch64 kits build via `--forcearch`.

Builds directly on `docs/phase1-vm-runbook.md` (same VM, same kit mechanics).
What is new in this pass:

| Lane | P1 | P2 (this runbook) |
|---|---|---|
| Kit apps | direwolf, fldigi, hamlib | + js8call, qsstv (one fully-installable bundle: `sstv`) |
| Suite code | not needed | `control/` tree travels to the VM (CLI + GUI) |
| GUI deps | — | `python3-pyside6` from Fedora repos (distro lane, never pip) |
| Audio | desktop plays sound | `gd audio up` owns `grid_down_*` PipeWire endpoints |
| Supervision | direct backend only | direct smoke **and** `--backend systemd` end-to-end |
| Doctor | n/a | full 18-check pass (real PipeWire; fail=0 is the gate) |

Three facts shape the steps below:

1. `build-kit.sh` is **dnf-strategy-only** (build/tarball/pip/wine apps are
   rejected with a clear list — P3 scope). Scanning `install/recipes/`, the only
   bundle whose members are *all* dnf is **`sstv`** (hamlib + qsstv). `js8` and
   `packet` are included anyway to demonstrate the by-design **partial start**
   (installed members launch; the rest are skipped with operator-facing notes).
2. The kit installs *apps only* — it does not carry the suite. The `control/`
   tree moves separately (step 2), before the network goes away.
3. Runtime Python deps come from the distro while still online:
   `python3-pyside6` (GUI, audited available — `docs/audit/f45-package-audit.md`
   § *GUI dependency check*), `python3-pyyaml` (registry/profile loading), and
   `pipewire-pulse` (provides `pactl`, which `core/audio.py` drives).

## 1. Build the kit (on the Mac, network on)

```sh
cd grid-down

# optional container pre-flight for the unit set (already green on the dev box —
# audit M4 §): renders units + systemd-analyze verify inside fedora:45
tools/systemd-analyze-verify.sh

# the kit — match the VM's arch; add qsstv+js8call to the P1 set:
install/bundle-builder/build-kit.sh --arch x86_64 --apps "hamlib,direwolf,fldigi,js8call,qsstv"
#   → kit/grid-down-kit-<date>-<arch>.tar.zst
```

Expected builder echo: `apps : hamlib,direwolf,fldigi,js8call,qsstv`,
`packages : fedora=[...] rpmfusion=[]`, then `kit ready: manifest N files`.

## 2. Move the kit *and the suite* into the VM (while still online)

```sh
# suite tree (CLI + GUI + registry) as a git archive of HEAD:
git archive --format=tar.gz -o /tmp/grid-down-suite.tar.gz HEAD

scp kit/grid-down-kit-*.tar.zst /tmp/grid-down-suite.tar.gz <user>@<vm>:/var/tmp/
```

## 3. Patch the VM, add the GUI lane, then air-gap

Same rationale as P1 step 3 (patch to the kit's snapshot date first), plus the
distro GUI/audio lane — dnf cannot install these after the NIC goes down:

```sh
sudo dnf -y update && sudo reboot                        # while online
sudo dnf install -y python3-pyside6 python3-pyyaml pipewire-pulse
python3 -c 'import PySide6, yaml; print("gui lane ok")'  # sanity
sudo nmcli networking off                                # or detach the NIC
```

## 4. Install the kit

Identical to P1 step 4:

```sh
cd /var/tmp
tar --zstd -xf grid-down-kit-*.tar.zst
KIT=$(ls -d grid-down-kit-*/ | head -1)
sudo "./$KIT/install/bootstrap.sh" --kit "./$KIT" --dry-run   # inspect the plan
sudo "./$KIT/install/bootstrap.sh" --kit "./$KIT"             # install
rpm -q direwolf fldigi hamlib js8call qsstv                    # all installed
```

## 5. CLI acceptance — doctor, audio, direct backend

All suite commands run from the un-packed tree with system Python
(`python3 ~/grid-down/control/cli/gd.py …`; abbreviated as `gd` below).

```sh
gd doctor            # full pass: 18 checks, real PipeWire — record the output
gd audio plan        # 3 radio roles → grid_down_{hf,packet,voice}_{in,out}
gd audio up          # creates the owned endpoints (pactl over PipeWire)
pactl list short sinks | grep grid_down      # 3 sinks; `sources` shows 3 more
gd launch fldigi --backend direct            # "running: fldigi (pid …) — log: …"
gd status            # "1 running, 0 stale (backend: direct)"
gd stop fldigi --backend direct
gd launch packet     # partial start BY DESIGN: hamlib + direwolf up;
                     # qttermtcp/paracon/chattervox skipped with notes (not in kit)
```

Doctor expectations: **fail=0 is the gate.** Warns are VM-hardware dependent
(e.g. no serial ports/dial-out group on a stock VM) and skips are `wine` +
`deferred` by design. The `systemd` check reads *"no grid-down units installed
… optional: `gd units install`"* until step 6. Copy-as-JSON output goes into
the evidence doc.

## 6. systemd backend acceptance (the M4a gate)

```sh
gd units install
```

Expected (5 kit apps resolve; targets always render for all 9 bundles):

```text
unit dir:   /home/<user>/.config/systemd/user
services:   5 installed
targets:    9 installed
skipped:    <wine-hub/cli-tool ids> (wine-hub/cli-tool are not daemonized, spec §6.5)
unresolved: <N> (no unit — app missing or placeholder unresolvable; fix and re-run)
daemon-reload: ok
```

(`unresolved` = everything outside the 5-app kit — expected in this pass; the
dev-box baseline was 2 services + 9 targets, audit M4 §.)

Now the real-bus proof — **`BindsTo=` stop propagation**:

```sh
systemctl --user list-units 'grid-down-*'          # 14 loaded units
gd launch sstv --backend systemd                   # rigctld first (singleton), then qsstv
pgrep -a qsstv                                     # pid present
pgrep -a rigctld                                   # pid present
gd stop sstv --backend systemd
pgrep -a qsstv                                     # empty — the service DIED with its target
systemctl --user is-active grid-down-sstv.target grid-down-qsstv.service   # inactive ×2
gd status                                          # backend: systemd (auto-resolved)
gd units list                                      # every unit "installed, current" — no drift
gd doctor                                          # systemd check now ok: "…(5 services, 9 targets)"
```

Also verify the suite-owned log capture and stop-refcount semantics:

```sh
tail ~/.local/state/grid-down/logs/qsstv.log       # ExecStart stdout/stderr landed here
journalctl --user -u grid-down-qsstv.service       # same content via the journal
gd launch packet --backend systemd && gd stop packet --backend systemd
# → note: rigctld left running only if another rig-dependent app still needs it;
#   after the last rig-dependent stop it stops too (refcount 0)
```

## 7. GUI acceptance (the P2 gate)

```sh
gd gui --selfcheck      # builds the real window on Wayland, applies one real snapshot, exits 0
gd gui &                # Control Center on the GNOME/Wayland desktop
```

Walk the five views (spec §7), in order:

| View | What to exercise / record |
|---|---|
| Dashboard | station header, profile-resolved rigctld command, owned `grid_down_*` endpoints (audio up/down buttons work), instance panel showing `backend: systemd`, doctor summary |
| **Bundles** | **the acceptance itself**: start `sstv` from its tile (confirm dialog → notes pane shows rigctld + qsstv), verify `pgrep qsstv` again, stop from the tile, verify it died; note `js8`/`packet` tiles show partial-start notes |
| Catalog | searchable 41-app table renders with install states |
| Doctor | color-coded checks match step 5; use copy-as-JSON for evidence |
| Profiles | read-only YAML renders (form editing is post-M3 scope) |

Evidence for the GUI half: paste the selfcheck output + a description (or
screenshot) of each view's content into the evidence doc.

## 8. Report

Paste all outputs into `docs/audit/phase2-vm-acceptance.md`
(§ *VM acceptance*) — same pattern as P1's round-trip doc — and flip the
README status line. That closes P2 (spec §11); P3 (VARA/Wine hub) then waits
on the user-supplied VARA/VarAC installers and licenses (spec §12).
