# Phase 5 implementation plan (P5)

**Status:** P5 in progress — M11a (`1e6485e`) + M11b (`0cc3bfc`) + M11c (`5b1393d`) + M11d (`d40877d`) + M12 (`19fb0f2`) + M13 (automated leg) landed · next: M14 ·
**Order:** installer completion → USB packaging → clean-host drill → acceptance closure
**Baseline:** P4 closed at `e0950c5` (M10 body `00a38b3`; 3×`P4: PASS` container E2E,
2026-10-30; audit `docs/audit/p4-container-e2e.md`).
This file is P5's single source of truth — milestone scope, adopted decisions, and
acceptance gates. Status lines are updated as milestones close.

Scope (spec §11): **USB kit, bootstrap hardening, drills** — acceptance:
**full offline rebuild from USB kit on a clean host** (spec §10: automated in a
`fedora:45` container with networking removed + human in a Fedora 45 GUI VM,
any arch).

## Drill-kit scope (adopted, 2026-10-30)

The acceptance drill builds and installs the **stageable full kit**: all 41
registry apps; the wine subset limited to what `install/kit-import/` carries
today (VARA HF v4.9.0, FM v4.4.0, portable VarAC v16.1.8 — G6 keeps
SAT/Chat/Term out until staged); region static slots (ZIM/OSM/GRIB) build
empty with Doctor WARNs recorded as drill caveats. Nothing in M11–M13 blocks
on operator input; each staged region/artifact lands later as a kit rebuild +
drill re-run (the meshtastic-web pattern from P4).

## M11 — Installer completion + postinst (bootstrap grows to spec §5)

Research facts this milestone builds on:

- Every recipe already declares `postinst: []` ("systemd units, udev rules,
  adapter hooks — Phase 1+") — M11 defines the vocabulary and flips keys;
  no schema-field work is needed.
- `gd units install` is user-bus-bound (`core/systemd.py`: `user_unit_dir()`,
  daemon-reload) while `bootstrap.sh` runs as root — the units postinst step
  must install unit *files* into the operator user's dirs root-side, treating
  `daemon-reload --user` as best-effort (Doctor reports drift, so the
  first-login reload is safe and observable).
- The wine-prefix cached tarball promised by spec §5 does not exist yet —
  `core/winehub.py`'s refusal strings already name the contract ("created by
  the kit postinst from the cached prefix tarball").
- `bootstrap.sh` still carries the P1 fail-closed `gd_die` on any recipe that
  declares hooks (lines ~90–96) — the gate for removal is the executor being
  real.

### M11a — postinst vocabulary + executor — **LANDED** (`1e6485e`, 2026-10-30)

Shipped as planned, with two container-check-caught corrections recorded
below. Validation: suite 261 green (was 250); ruff/black/isort clean;
`bash -n` ×3; resolver CLI smoke; fedora:45 mechanism check (hand-made kit,
no dnf): `--dry-run` plan incl. the postinst line + operator, real
`gd_kit_postinst` — dialout WARN-skips without an operator, udev rule
installed 0644 into `/etc/udev/rules.d`, units M12 WARN, wine-prefix passes
the x86_64 gate then dies naming the M11b tarball. Full offline round-trip
with a real kit stays M11d.

Corrections the check caught (both folded into `1e6485e`):
- `postinst.tsv` is the first kit TSV with optional columns — tab-`read`
  collapses empty cells and shifts columns (the M6c `\x1f` rationale, met on
  the air-gap read side). Builder normalizes to `1`/`-` sentinels; no empty
  cells ever.
- `gd_postinst_udev` had the M10 `local` self-reference class again
  (`dest` expanding `rel` from the same declaration — unbound under `set -u`);
  split declarations, and the TSV carries the kit-relative path
  (`install/udev/…`) instead of the recipe-relative one.
1. `postinst:` grows a typed list (see "Postinst format" below): `units`,
   `udev`, `dialout`, `wine-prefix`, `wine-install`. `tests/test_registry.py`
   gains the contract: known keys only, udev paths under the kit, declarative
   steps only (the executor interprets; recipes never embed shell).
2. `install/lib/kit.sh` gains the executor (`gd_kit_postinst`), run after
   payload placement per selected app; failures stop loud with the step named
   and re-runs resume. `bootstrap.sh` drops the P1 `gd_die` once the executor
   is real.
3. Steps:
   - `units` → render + install the `grid-down-*` user units into the operator
     user's `~/.config/systemd/user` (operator = `SUDO_UID` of the invoker, or
     `--user` on bootstrap); best-effort `daemon-reload --user` with a printed
     first-login note.
   - `udev` → copy kit rule files to `/etc/udev/rules.d/` + `udevadm` reload.
   - `dialout` → ensure the operator user is in `dialout` when the group
     exists (a stock container may lack it: warn, never fail).
   - `wine-prefix` → unpack `kit/wine-prefix.tar.zst` into
     `/opt/grid-down/wine-prefix` (x86_64-gated: loud skip on aarch64).
   - `wine-install` → staged installers per `wine.installer` (`setup` = silent
     Inno run under wine; `portable` = unpack to `wine.install_dir`), success
     decided by the same per-app probe as `gd winehub install` (`wine.exes`
     candidates under `drive_c`).

### M11b — builder side: prefix tarball — **LANDED** (`0cc3bfc`, 2026-10-31)
`container-build.sh` builds the shared prefix once in the x86_64 container
(wine from the kit dnf set + the staged `kit-import/` installers), packs
`kit/wine-prefix.tar.zst` + manifest rows. G6 unchanged: installers ride the
kit to the operator's own station only; licenses stay applied in-app.

**Shipped:**
- Resolver: wine apps resolve (the M11a-era refusal is gone) into
  `plan["wine"]` rows `{id, file, glob, mode, install_dir, sha256}`.
  Fail-closed G6 guards: `wine.installer_glob` must match exactly one staged
  `install/kit-import/` file, and the new `wine.installer_sha256` pin (64-hex,
  M6 discipline) must match the staged bytes — 0 or ≥2 matches, or drift,
  exits 2 naming the fix (re-stage or re-pin). `installer:` is `setup`
  (default) or `portable` (requires `install_dir`). The arch guard moved
  ahead of the strategy loop — a wrong-arch plan fails before any staging
  I/O. Wine itself joins the kit dnf set with zero resolver code: the
  recipes carry `dnf: packages: [wine]` and the M10-proven any-strategy
  dnf-row tail picks it up (same wine stack builds the prefix and runs it).
- Builder: `kit/wine/<id>/<artifact>` + `kit/wine.tsv` (id, kit path, mode,
  install_dir, glob); prefix built once with `wineboot -i` under winehub's
  suppress env (`WINEDLLOVERRIDES="mscoree,mshtml="`, `WINEDEBUG=-all`),
  packed Rosetta-safe (python tarfile → zstd — GNU tar is broken under
  Rosetta) with top-level `wine-prefix/` so the executor's
  `bsdtar -xf -C /opt/grid-down` lands it at the documented default.
  Manifest rows carry the wine version (11.20) + the G6 source note. No new
  container mounts — `kit-import` already rides `/install-src`.
- Air-gap side unchanged: M11a's `gd_postinst_wine_prefix` already consumed
  exactly this ball (its missing-tarball guard now only trips on pre-M11b
  kits); `wine-install` stays suite-delegated (M12 wires `gd winehub install`).

**Validation:** 269 passed (was 261; +9 wine staging tests incl. the G6
fail-closed cases, −1 flipped refusal test), lint clean, `bash -n` ×4.
fedora:45 mechanism check green end-to-end: host resolver smoke over the
real recipes + staged pins; container build with a one-app (varac) plan —
real wine closure download (41 wine rpms into the kit repo), `wineboot -i`
prefix init + 996 MB ball, wine.tsv / zip-sha / postinst / manifest
assertions, and the executor's bsdtar round-trip unpack in-container
(`.update-timestamp` + `drive_c` present).

### M11c — hardening — **LANDED** (`5b1393d`, 2026-10-31)
- **Arch guard:** the kit records its target arch (builder side writes it);
  `bootstrap.sh` refuses an arch mismatch before touching dnf — the P1
  finding #1 failure mode dies up front instead of in the solver.
- **Idempotency proof:** re-running a completed install exits 0 and changes
  nothing (dnf no-op, payload re-placement no-op, state preserved); asserted
  in the round-trip harness (spec §5 claims it; M11 proves it).
- **Partial failure:** stop loud with the failing step named; a re-run resumes
  (dnf is transactional; payload steps are copy/extract idempotent).
- **State:** `state.json` gains `kit_arch`, `kit_manifest_lines`,
  `postinst_done` (per-step watermarks).

**Shipped:**
- Arch guard: `container-build.sh` writes `kit/ARCH` (one line) before the
  manifest pass, so the target arch is manifest-hashed like every other kit
  file; `build-kit.sh` prints it in the kit-ready summary. `bootstrap.sh`
  adds `ARCH` to its completeness list and calls `gd_kit_arch_check` right
  after the strict manifest verify — before the dry-run block and anything
  mutating — so missing (pre-M11c kit), garbage, or mismatched ARCH dies
  naming the fix, on `--dry-run` too. The P1 finding #1 refusal is one line.
- Idempotency + resume: `postinst_done` became per-step watermarks
  (`app/step` tokens in a deduplicated state list) driven by
  `gd_postinst_step`: step exit 0 = applied (marked; re-runs skip it),
  non-zero = self-skip with the follow-up printed (no mark — a fixed
  condition + re-run applies it), `gd_die` = loud stop naming app/step. The
  operator-skip paths in dialout/units/wine-install return non-zero so they
  never watermark. `installed_at` freezes across same-kit re-runs, so a
  completed install's re-run exits 0 and leaves `state.json` byte-identical.
  `kit_arch` + `kit_manifest_lines` land right after dnf — the first point
  the python3-based state helpers are guaranteed to work (a bare container's
  pre-dnf writes would only warn; a crash before it is a rolled-back dnf
  transaction the re-run redoes). Phase names ride `$PHASE` into an ERR trap
  so unguarded failures stop loud naming the phase and the resume contract.
- State helpers: `gd_state_get` / `gd_state_mark` / `gd_state_marked` in
  `common.sh` (read-back, deduplicated list append migrating M11a's
  `postinst_done: "yes"` sentinel, membership probe).

**Validation:** suite 269 green (no python behavior change); `bash -n` ×5;
lint clean; host probes of the state helpers (dedup + sentinel migration +
missing-key) and the arch guard (match/mismatch/garbage/missing).
fedora:45 container check of record (gd-f45-current, `--network none`, real
direwolf+hamlib kit, one green run from a clean kit build): dry-run arch
line; install with an operator (dialout watermarked ×2, units M12-warn-skip
unmarked); re-run exits 0 with state byte-identical and dnf "Nothing to
do."; tampered-ARCH (aarch64) refused before repo setup with state
untouched — and refused on `--dry-run`; missing ARCH refused; a
bogus-udev-row manifest-consistent tamper stops loud at
`postinst[zz-bogus/udev]` with earlier watermarks intact, and the repaired
re-run resumes past them to completion. Battery-iteration lessons folded
into the check: the runner passed extra args through (its "dry-run" first
ran a full install), `cmp`/`diff` don't exist in the f45 container (exit-127
masquerading as a state mutation — byte-compare in python3 instead), and
bash ERR traps skip guarded commands (a `|| gd_die` path never prints the
phase line; the ERROR step line is the assertion there). M11d re-asserts
the idempotency proof inside its round-trip harness.

### M11d — gate — **LANDED** (`d40877d`, 2026-10-31)
Container round-trip (`--network none`) with postinst assertions: unit files
present + drift-clean vs a fresh render, udev rule placed, dialout handled,
prefix path sane (x86_64) / loud skip (aarch64), re-run is a no-op.
**Gate:** `ROUNDTRIP OK` incl. postinst; suite + lint green.

**Shipped:**
- `tools/roundtrip-dnf.sh` — the P1 harness grew into the P5 gate
  ("ROUNDTRIP OK — incl. postinst"): builds the kit (`--with-wine` adds
  varac — the M11b-proven leg), augments it with the **M11d udev probe**
  (real rule file + direwolf `postinst.tsv` row flip + full manifest re-hash
  — the M6c edit-re-hash discipline as a positive test; retires when a
  recipe ships a real rule), then runs ONE air-gapped container that
  installs with an operator (`bootstrap.sh --user`) and asserts:
  - **A** — dialout gid via `id -nG`, probe rule placed 0644 with content
    intact, units loud-skip lines present and UNMARKED, applied steps
    watermarked in `state.postinst_done`, `kit_manifest_lines` ==
    on-disk manifest rows;
  - **W** — the shared prefix unpacks to the documented
    `GRID_DOWN_WINE_PREFIX` path (`drive_c` + `.update-timestamp`), chowned
    to the operator, `varac/wine-install` loud-skips naming
    `gd winehub install varac`;
  - **G** — a PATH-shimmed `uname -m` (aarch64) makes the prefix step DIE
    naming x86_64 (exit ≠ 0, `state.json` byte-identical) — a die, not a
    skip;
  - **N** — re-run is a no-op: dnf reports installed packages, watermarks
    skip, self-skips re-attempt, `state.json` byte-identical (python3
    byte-compare — no cmp/diff in f45).
- `install/lib/kit.sh` — wine-prefix fix the gate caught on its first
  `--with-wine` run: the cached ball packs the prefix dir itself
  (top-level `wine-prefix/` component; the builder's unpack target is
  documented as `-C /opt/grid-down`), but `gd_postinst_wine_prefix` used
  `-C "$dest"` — `drive_c` landed one level deep, the re-run guard never
  matched, and every re-bootstrap re-unpacked ~1 GB. Unpack now uses
  `--strip-components=1` so the contents land at `$dest`
  (`GRID_DOWN_WINE_PREFIX` overrides stay correct); the guard + watermark
  work as designed.

**Bullet-vs-code corrections (M11c style):** units asserts the loud-skip
contract only (the step needs `$GD_ROOT/control/cli/gd.py`, M12 — presence +
drift moves to M12/M13-G5); udev is a harness probe (no shipped recipe
declares a rule); the aarch64 prefix leg is a loud `gd_die`, asserted as
such (not a skip).

**Validation:** lean + `--with-wine` round-trips green on the final tree
(66 s / 232 s, one run each, gd-f45-current, `--network none`); suite 269
green; lint + pre-commit clean.

### M12 — USB kit packaging — **LANDED** (`19fb0f2`, 2026-10-31)

Every kit now carries its own control suite, and the P4 transport loop is
a gate: build → pack to FAT32 parts → wipe the staging tree → restore
from the USB layout alone → tamper probe → air-gap install from the
restored tree.
**Gate:** `ROUNDTRIP OK — usb pack/unpack (M12 gate)` (77 s); `ROUNDTRIP
OK — incl. postinst` re-asserts delegation (lean 78 s / `--with-wine`
265 s, `install: varac  installed` under wine as the operator, air-gapped);
`P4: PASS` ×3 with the E2E gates reading the suite + installer from $KIT;
suite 274 green; lint clean.

**Shipped:**
- `install/bundle-builder/` — the kit packs control/ and profiles/
  (`/control-src` + `/profiles-src` mounts, bytecode pruning, manifest
  source strings; a missing mount is a FATAL). `resolve-kit.py` pins
  util-linux + python3-yaml into the station set (runuser + the suite's
  yaml import) — the kit carries its own runtime deps.
- `install/lib/kit.sh` — the M11d loud-skips become the pre-M12 refusal
  ("control suite missing from this kit (M12+ kits pack control/)");
  with the suite present, `units` delegates (`gd units install` under
  the operator, daemon-reload soft-skipping headless) and `wine-install`
  delegates (`gd winehub install <id>` — the silent kit-import installer
  runs at install time, G6 honored: it rides the kit and runs on the
  operator's station).
- `control/core/winehub.py` — `resolve_installer` falls back to the kit
  layout (`<root>/wine/<id>/`, the M11b manifest-pinned staging) with
  the same recipe glob, exactly one match; the executor still never
  parses the recipe's wine block.
- `tools/make-usb-kit.sh` + `tools/unpack-usb-kit.sh` — FAT32-safe
  split parts + `usb-manifest.sha256` sidecar (plain tar; why, in
  docs/audit/p4-usb-roundtrip.md); restore = per-part verify →
  reassemble → extract → kit-manifest re-hash (refuses any layout the
  installer would refuse). Host hashing takes sha256sum(1) or shasum(1).
- `tools/roundtrip-usb.sh` — the P4 gate (the loop above + tamper probe
  + install assertions R/A/N). Evidence: docs/audit/p4-usb-roundtrip.md.
- `tools/p4-e2e-gates.sh` — reads the suite + installer from $KIT (G0
  asserts kit/control/); the /gd checkout mount drops to convenience;
  both E2E scripts gain their executable bit.
- doctor — `gd doctor --kit DIR`: strict per-file manifest re-hash plus
  the control//profiles/ presence contract (pre-M12 kits FAIL with a
  rebuild pointer); the M1-era kit-check DEFERRED line lands.

**Findings the gate caught (five, first delegation runs, each fixed in
the tree it exposed):** runuser absent (util-linux-core doesn't ship it
— resolver pins util-linux); yaml absent on a bare station (resolver
pins python3-yaml); the profile fallback is ROOT-relative (the kit now
carries profiles/); the historic uid-11 `operator` system account
satisfied the harnesses' `id operator` guard and landed HOME in /root
(they now replace uid < 1000 operators before useradd); the suite
resolved installers repo-only (winehub kit-layout fallback). Details in
the commit message.

**Bullet-vs-code corrections:** the plan's `.tar.zst` + README-on-the-
stick became split parts + sidecar manifest (FAT32's 4 GiB file cap;
no compression win on an already-compressed payload; the README lives
in the tool headers + the audit doc); the plan had make-usb-kit assemble
the suite snapshot — the suite rides the KIT from the builder
(delegation is a kit property, not a transport one); bootstrap takes the
restored dir verbatim, as planned.

**Validation:** suite 274 green (+5 doctor kit-check tests; M10 result
count 20→21); ruff/black/isort clean; synthetic USB smoke round-trips
multi-part byte-clean with `doctor --kit` ok and the live tamper probe
refusing; round-trips lean 78 s / wine 265 s; USB gate 77 s.

## M13 — Clean-host drill (automated + human, per spec §10)

**Status: LANDED** (automated leg; human leg pending — runbook ready). The
drill shipped as the P4 harness split: `tools/drill-clean-rebuild.sh` (host:
full 38-app build → USB pack → **kit-staging wiped** → N rounds) +
`tools/p5-drill-gates.sh` (container: the gate chain, final line exactly
`P5: PASS`).

- Gates as planned, with the honest adjustments the first runs forced:
  - G1 — the wrong-arch probe is a **self-consistent** kit (ARCH flipped *and*
    re-signed) so the refusal provably comes from the arch guard, not the
    earlier manifest guard; asserts no state written.
  - G3 — canaries are recipe-truth (18 rpms + the flarq binary; Fedora's
    fldigi `Provides:` flarq — there is no flarq rpm; winetricks/nodejs/java
    turned out not to be kit rows at all).
  - G5 — dialout + rendered user units + the VARA HF/FM/VarAC exes on the
    shared prefix; **no recipe declares a udev row yet** (the mechanism is
    specified; the first rule-carrying recipe lands with the handset work).
  - G8 — mesh adapter dry-run (target argv, `--seturl ***` PSK mask, refusals)
    + wine-hub config surface + units listing; the VARA start argv is not a
    suite render surface, so the wine side asserts the config read instead.
- **Findings fixed at this milestone** (details in
  `docs/audit/p5-clean-rebuild.md`):
  1. wine cannot silently run VARA's Inno installers headless (ShellExecuteEx
     dies in wine's `start` before `/VERYSILENT` matters; P3's VM E2E used
     fixture dummies, so this was the first automated attempt anywhere) —
     winehub gained `wine.installer: innoextract` (unpacks the setup exe; the
     VARA HF/FM recipes pin it + `install_dir`; resolver enforces it). A field
     win too: VARA installs no longer need a GUI.
  2. `gd_kit_extract_artifacts` re-runs were not idempotent for
     directory-bearing tarballs (`mv` into a non-empty dest; gridtracker's
     `locales/`) — the dest is now replaced wholesale from the sha-pinned
     artifact. Unexercised before M13: M11d's re-runs had no such artifacts.
  3. python bytecode litter (`__pycache__`) in a writable restored tree breaks
     G6's kit-manifest re-verify (M12's roundtrip mounted the kit read-only) —
     the drill sets `PYTHONDONTWRITEBYTECODE=1`.
- **Evidence:** 3/3 `P5: PASS` in 382 s (126–129 s per round: unpack 17 s,
  repo-only dnf, payloads, postinst, re-run no-op, doctor 9 ok / 6 warn /
  0 fail); 2-part 3.8 G layout; 278 tests green (+4 winehub innoextract),
  lint clean.
- `docs/phase5-runbook.md` — the human half, written and ready: fresh
  **x86_64** F45 GUI VM, NIC detached before unpack, layout via `vmpal cp` (or
  a FAT32 stick), the kit's own unpacker + installer, checks H1–H8, transcripts
  back into the audit doc. **Gate remainder:** runbook executed once, evidence
  committed (solo, or driven in a follow-up session on a UTM/VMPal VM).

## M14 — Acceptance + closure

- Audit doc `docs/audit/p5-clean-rebuild.md`: runs + carriers, gate table,
  findings that shaped the design, caveats (empty region slots, wine subset,
  hardware-gated leftovers).
- Spec §12 sweep; phase5-plan closure; status banner; commit + push via the
  `.git/COMMIT_MSG.txt` discipline.
- **Gate:** spec §10 P5 acceptance met and evidenced.

## Arch policy (carried over from P4)

- x86_64 = full kit incl. wine (prefix tarball + `wine-install` steps).
- aarch64 = dnf + arch-safe strategies; wine steps skip **loudly** (printed
  per-app skip + Doctor WARN), never silently.
- The drill container is pinned `linux/amd64` (the P0/P1 `--platform` lesson).

## Operator-input register (non-blocking for M11–M13)

| Item | Unblocks | Lands as |
|---|---|---|
| Region content (Kiwix ZIM, OSM extract, GRIB) → `kit-static/` | full static payloads | kit rebuild + drill re-run |
| VARA SAT/Chat/Term installers + licenses | full wine subset | kit rebuild (G6 fail-closed until then) |
| meshtasticd channel/HAT decision | mesh bundle completeness | recipe flip + rebuild |
| Hardware round (handset + BLE pins, wine pins, on-air Winlink, meshtasticd install) | spec §10 hardware-gated leftovers | acceptance addenda, not kit work |

## Postinst format (recipe schema, M11a — shipped shape)

```yaml
recipe:
  postinst:                  # [] stays valid (most dnf apps need nothing)
    - units                  # render + install grid-down-* user units (operator's dirs)
    - udev: udev/99-meshtastic.rules   # file under install/udev/ -> /etc/udev/rules.d/
    - dialout                # ensure the operator user is in dialout (group permitting)
    - wine-prefix            # unpack kit/wine-prefix.tar.zst (x86_64-gated)
    - wine-install           # staged installer per wine.installer (setup|portable)
```

Differences from the planning sketch: bare step names (no `key: value` forms
— values carried no information), and `udev` paths are anchored at
`udev/<name>.rules` (a file that must exist under `install/udev/`; the
builder renders the kit-relative `install/udev/…` into `postinst.tsv`).
Canonical application order: dialout → udev → units → wine-prefix →
wine-install.

Executor rule: declarative steps only — a recipe never runs shell; unknown
keys fail the resolver (exit 2) and the registry tests.

## Adopted defaults (from the plan review, 2026-10-30)

- **Drill kit:** stageable full kit (see top); unstaged VARA artifacts and
  region content are recorded caveats, not blockers.
- **Numbering:** M-numbers continue across phases (M11–M14).
- **Runbook timing:** `docs/phase5-runbook.md` is written at M13, not up front.
- **kit-staging/** stays untracked scratch; cleaned before gate runs, never
  committed.

