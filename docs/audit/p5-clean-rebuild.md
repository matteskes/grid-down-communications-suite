# P5 clean-host rebuild — M13 gate evidence

**Date:** 2026-11-01 (M13). Automated leg on the macOS host via Docker Desktop
(`--platform linux/amd64`, Rosetta): full 38-app kit build → FAT32 USB pack →
staging wiped → N air-gapped fedora:45 rebuilds. Human leg: `docs/phase5-runbook.md`
(fresh x86_64 F45 GUI VM; pending — see the end of this file).

## The gate

`tools/drill-clean-rebuild.sh` (host orchestrator) + `tools/p5-drill-gates.sh`
(container gate chain). The kit is never handed to the drill directly:
`build-kit.sh --no-pack` (38 registry ids minus the unstaged VARA imports) →
`make-usb-kit.sh` (FAT32 parts + sidecar) → **kit-staging wiped** → per round,
a fresh container with only the layout mounted read-only:

| stage | assert |
| --- | --- |
| G0 | sidecar rows == part files; layout unpacked with the kit's own unpacker (per-part sha256) to `/restore` |
| G1 | a **self-consistent** wrong-arch kit (ARCH flipped *and* re-signed) is refused by the arch guard before any mutation; no state written |
| G2 | the kit's own strict verifier accepts the restored tree (2676 rows) |
| — | real install: `bootstrap.sh --kit --user operator` (repo-only dnf, payloads, postinst) |
| G3 | 18 rpm canaries across every strategy family + the flarq binary; air-gap is the container itself |
| G4 | payload placement: ardop x86-64 ELF (registry-resolved), pat binary at the recipe dest, meshtastic pip pin 2.7.11, meshtastic-web docroot, yaac jar |
| G5 | dialout, 21 rendered `grid-down-*` user units, VARA HF/FM/VarAC exes on the shared prefix |
| G6 | re-run: dnf skips, watermarks honored, `state.json` byte-identical, kit manifest still clean |
| G7 | `gd doctor` exit 0 (warn-level caveats below) |
| G8 | spawn-free renders: mesh adapter dry-run (target argv, `--seturl ***` PSK mask, 3 profile refusals), wine-hub config surface, units listing |

Final line per round: exactly `P5: PASS`; the orchestrator greps it.

## Results (2026-11-01, x86_64, Rosetta)

| stage | result |
| --- | --- |
| build | 38 apps, 2676 kit files (3.9 G staging) |
| pack | 2 parts ≤ 3800 MiB (3.8 G layout) + sidecar |
| round | 126–129 s each: unpack 17 s → install → G0–G8 |
| chain | **3/3 `P5: PASS` in 382 s** (`DRILL OK`) |
| doctor | 9 ok, 6 warn, 0 fail, 6 skip |
| install log | `install: vara-fm installed exe: …/VARA FM/VARAFM.exe mode: innoextract` (also vara-hf, varac) |

## Findings (each fixed at this milestone)

1. **VARA's Inno installers cannot be silently run by wine headless** — the
   drill's canary. Wine routes the launch through its `start` program and dies
   in `ShellExecuteEx` before `/VERYSILENT` matters (traced: `err:start:fatal_error`;
   no `requestedExecutionLevel` in the manifest bytes — a wine loader/start
   handoff, not a UAC request we could satisfy). The real installers had never
   been silently run anywhere: P3's VM E2E used fixture dummy exes
   (`gd-p3-rehearsal-dummy`) and a human GUI install. Fix: winehub gained
   `wine.installer: innoextract` — unpack the Inno Setup exe with innoextract
   (rides the kit dnf set; VARA HF/FM recipes pin it + `install_dir`), same
   files on disk, no display, no elevation, probe still decides success.
   `setup` mode stays for display-bearing hosts. Also a field win: VARA
   installs no longer need a GUI at all.
2. **Artifact re-extraction was not idempotent** — G6's re-run died in
   `gd_kit_extract_artifacts`: the tarball flatten path `mv`s payload dirs into
   an existing dest (`mv: cannot overwrite …/locales: Directory not empty`).
   Unexercised until now: M11d's re-runs had no directory-bearing tarball
   artifacts. Fix: the dest is replaced wholesale (`rm -rf` + fresh `mv` out of
   a sibling temp) — the artifact is sha-pinned, so its content is the truth.
   `cp`-based build/static placement was already merge-safe.
3. **Bytecode litter breaks the kit manifest on a re-run** — G6 re-verifies the
   kit manifest and found 2678 files vs 2676 rows: `python3
   $KIT/control/cli/gd.py` drops `__pycache__/` into the restored tree. M12's
   roundtrip never saw it (its kit was mounted read-only; a restored tree is
   writable). Fix: the drill exports `PYTHONDONTWRITEBYTECODE=1` for every
   python use, delegation included.

Harness-only corrections (drill scripts, no kit/suite change): `flarq` has no
rpm (Fedora's fldigi `Provides:` it — canary the binary); `winetricks`,
`nodejs`, `java-25-openjdk` are not kit dnf rows (plan assumptions — the
recipes are the truth); the G5 wine probe reads `wine.exes` from the kit's
`install/recipes/` (not `control/`, which carries none); the app-set banner
undercounted by one (`$(...)` strips the newline `wc -l` counts).

## Doctor caveats (warn classes on a bench host, recorded not fixed)

`root not in dialout` · `no serial devices` · `pactl not found (pipewire-pulse)`
· `rig serial path unset` · `air-gap master switch not false` · `rig.model
unset` — all hardware/field-config gated, zero fails. `gd units list` inside
the drill reports the rendered set as "not installed" because it diffs the
calling user's (root's) unit dir — the render evidence is G5's 21 units for
`operator`.

## Companion gates (same tree, same day)

- 278 pytest tests pass (was 274: +4 winehub innoextract tests; setup-mode
  tests pin their recipe explicitly now) · lint clean.
- Resolver accepts the new mode (`WINE_MODES`) and requires `install_dir` for
  it, same as portable.

## Human leg (pending)

The runbook is ready (`docs/phase5-runbook.md`): fresh x86_64 F45 GUI VM, NIC
detached before unpack, layout in via `vmpal cp` (or a FAT32 stick), the kit's
own unpacker + installer, the H1–H8 checks, transcripts back into this file.
To be executed once — solo, or driven in a follow-up session on a UTM/VMPal VM.
