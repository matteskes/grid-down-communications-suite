# Phase 5 implementation plan (P5)

**Status:** P5 in progress — M11a (`1e6485e`) + M11b (`0cc3bfc`) landed · next: M11c ·
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

### M11c — hardening
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

### M11d — gate
Container round-trip (`--network none`) with postinst assertions: unit files
present + drift-clean vs a fresh render, udev rule placed, dialout handled,
prefix path sane (x86_64) / loud skip (aarch64), re-run is a no-op.
**Gate:** `ROUNDTRIP OK` incl. postinst; suite + lint green.

## M12 — USB kit packaging

- `tools/make-usb-kit.sh`: assembles a USB layout from a built kit —
  `grid-down-kit-<date>.tar.zst` + `.sha256` sidecar, the `control/` suite
  snapshot, README with restore/verify steps (the two-end sha256 discipline
  carried over verbatim from the P3 transfer recipe). exFAT-safe layout: no
  symlinks, no reliance on POSIX perms on the stick.
- `bootstrap.sh` accepts a USB mount path verbatim — no special-casing; the
  strict manifest re-hash + file-count equality already catches truncated or
  partial copies. M12 adds the drills' unpack-from-mount step and documents
  the mount-agnostic checks in the runbook.
- **Gate:** build → write layout → mount path → `bootstrap.sh --kit <mount>`
  offline with the manifest verified *from the mount*; a tamper probe (one
  flipped byte) is refused loudly.

## M13 — Clean-host drill (automated + human, per spec §10)

- `tools/drill-clean-rebuild.sh` — the P4 harness pattern: fresh `fedora:45`
  `linux/amd64` image, `--network none`, kit unpacked **from the M12 USB
  layout**, timed, gate list, `P5: PASS` banner, evidence dir. Gates:
  - G0 — layout + sidecar verify (USB content, not a bare staging tree)
  - G1 — arch guard refuses a mismatched kit up front
  - G2 — strict manifest verify from the mount
  - G3 — dnf install from the kit repo only (no network-capable repo)
  - G4 — payloads placed (build/tarball/pip/static per TSVs)
  - G5 — postinst effects asserted (units/udev/dialout; wine subset on x86_64)
  - G6 — re-run is a no-op
  - G7 — `gd doctor` fail=0 (warns recorded as caveats: empty region slots,
    hardware-absent checks)
  - G8 — bundle render sanity: targets/units render, rig argv previews
    (headless container: no GUI spawn)
  Discipline: **3×`P5: PASS`** fresh runs (P4 precedent); logs kept in
  `$TMPDIR`, outcomes recorded in the audit doc.
- `docs/phase5-runbook.md` — the human half (written at M13): fresh Fedora 45
  GUI VM (any arch), USB layout in via image mount/transfer, `bootstrap.sh
  --kit`, `gd doctor` fail=0, one bundle start (Control Center or `gd launch`);
  evidence pattern identical to the P1–P3b runbooks.
- **Gate:** 3× drill PASS + runbook executed once, evidence committed.

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

