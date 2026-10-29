# Phase 4 implementation plan (P4)

**Status:** M6a+M6b pinned (2026-10-29); M6c builder + M6d gate next · **Order (spec-mandated):** recipes → adapters → services → mesh
**Baseline:** P3b closed at `cd4e5ba` (3/3 PASS on aarch64 VM; runbook Appendix A).
This file is P4's single source of truth — milestone scope, adopted decisions, and
acceptance gates. Status lines are updated as milestones close.

## M6 — Pin remaining recipes + extend the kit builder (core of P4 start)

16 recipes still carry `PIN-ME` (9 build + 7 tarball). The audit doc
(`docs/audit/f45-package-audit.md`) is the authoritative pin source.

### M6a — schema extension + build-strategy pins (9)
1. **Pre-pass:** build-deps audit round in `fedora:45` `linux/amd64`
   (`dnf list available` per candidate build dep; also verify
   `libarchive-tools` for the station-side bsdtar and `cpio`/`unzip`/`patch`
   fallbacks). Results extend the audit doc. The P0 audit checked runtime
   packages only — build deps were never verified.
2. **Recipe schema grows** (see "Pin format" below): `source.tag`, `source.version`,
   `source.build_deps`, `tarball.arch`, `tarball.format`. `tests/test_registry.py`
   gains the pin contract: no `PIN-ME` left, sha256 shape (`64 hex`), `build_deps`
   required on build recipes, `arch` required on tarball recipes.
3. **Pin:** ardop (pflarue/ardopcf), wsjtx-improved, qttermtcp (g8bpq), paracon
   (mfncooper), sdrpp (AlexandreRouma/SDRPlusPlus), dump1090 (fork decided by the
   license check — Flags #3), flwrap (w1hkj), xygrib (opengribs), osm-navit
   (navit-gps). Exact tag + archive sha256 each; `TBD-VERIFY-AT-PIN` licenses
   (qttermtcp Flags #1, dump1090 Flags #3, yaac Flags #6, gridtracker Flags #2)
   resolved upstream at pin time.

### M6b — tarball pins (7)
pat (v1.0.0 — amd64 + arm64 tar.gz, arch-neutral pin), chattervox (npm tarball,
brannondorsey), yaac (jar from aprs.org; runtime `java-latest-openjdk-headless`),
gridtracker (Linux artifact format confirmed at pin), js8spotter (webapp payload),
artemis (AresValley v4.2.0 `.deb`, x86_64-only, 190 MB — arch-SKIP elsewhere),
meshtasticd (latest **stable** firmware release; deb-or-rpm with bsdtar fallback).
Blanket policy: the artifact format is confirmed upstream at pin time (.deb / .rpm /
tarball / npm) — extraction follows the recorded `format:`, never spec wording.

### M6c — kit builder + air-gap helpers
- `build-kit.sh` resolver: per-strategy plans (dnf / build / tarball /
  pip-vendored / static); refuses to pack while any selected recipe still has
  `PIN-ME` or `TBD-VERIFY-AT-PIN`, listing offenders loudly.
- `container-build.sh` phases: download + verify → in-container builds
  (`kit/opt/<id>/`) → `pip download meshtastic==<ver>` → `kit/wheels/` →
  `kit/static/<id>/` → tarball staging. Manifest gains `src` and `ver` columns
  (`nvra` flavor for dnf rows — G5 wine note); x86_64 guard per "Arch policy";
  `patches/` escape hatch for F45-toolchain build failures.
- `install/lib/kit.sh` + `bootstrap.sh --kit`: bsdtar extraction of deb/rpm
  artifacts into `/opt/grid-down/<id>`; offline wheel install
  (`pip --no-index --find-links kit/wheels/`); static payload placement;
  Doctor WARN rows for arch-SKIP and region-pending static payloads.

### M6d — gate
`tests/test_registry.py` pin-contract extensions + new `tests/test_kit_builder.py`;
container round-trip with one app per strategy (`--no-pack`).
**Gate:** full-strategy kit build, zero `PIN-ME`, complete `manifest.sha256`.
(The 3×`P4: PASS` E2E discipline starts at M10, not here.)

## M7 — Registry-driven adapters
Adapter framework following the `control/core/adapters/vara.py` pattern:
ardop-cfg, pat-conf, qttermtcp-cfg, paracon-cfg, sdrpp-conf, gridtracker-conf,
yaac-prefs, meshtasticd-yaml, chattervox-json, xygrib-conf, navit-xml,
artemis-paths. Registry `state: stub → active` per app as its adapter lands.

## M8 — Services
js8spotter localhost service (`php -S 127.0.0.1:8091` — php-cli verified
available); the two display services per spec §7; systemd units for
managed-service apps (ardop, dump1090, meshtasticd) + M4 verify round; port
map documented.

## M9 — Mesh profile
meshtasticd ships but stays **off** by default (ride-along #3: USB handset +
`meshtastic` CLI is the default path); mesh bundle wiring (`bundles.yaml`);
config adapter; Doctor checks. Node firmware builds are user-side, not kit-side.

## M10 — Acceptance + corrections
E2E on the x86_64 station profile (3×PASS discipline); spec §8 wording
corrections applied together with the audit doc (artemis ".rpm" → .deb,
meshtasticd "RPM" → deb-or-rpm, recipes README "Phase 3" → P4);
phase4-plan status closure; push.

## Arch policy (adopted default)
P4 kits target x86_64, matching the Phase 0 station decision (audit
"Lesson learned"): VARA requires wine, which Fedora ships on x86_64 only, and
Artemis/GridTracker ship x86_64-only artifacts. The build-strategy apps are
arch-capable (the same git tag compiles under either container arch); a future
aarch64 kit would additionally require an arm64 build-deps audit round,
per-arch in-container builds, and per-arch `pip download` — a deliberate plan
change gated by this section; the container-build arch guard fails with a
pointer here rather than assuming. Recipes and Doctor semantics stay
arch-aware regardless (`arch:` fields, arch-SKIP + WARN). Exception:
`--arch aarch64` stays valid for **dnf-only** subsets (S4 `--forcearch`
spike) — it is the full-kit guard that is x86_64-only in P4. An aarch64 kit
today would still lack VARA/artemis/gridtracker (upstream gaps), which is why
"both" is not the default.

## Recon corrections (2026-10-29, refined at M6a/M6b pin time)
- **pat v1.0.0** ships amd64 AND arm64 tar.gz release assets with GitHub-API
  sha256 digests → arch-neutral pin (both URLs; the builder picks by target
  arch).
- **meshtasticd**: the `latest` release endpoint returns Betas and the newest
  **non-prerelease** (v2.7.26.54e0d8d) ships NO deb/rpm assets at all (device
  firmware + a src zip only). The daemon's distribution channels are: Fedora
  **COPR `@meshtastic/beta`** (chroots f44/f43 listed at pin; f45 chroot to
  verify) and Debian/**OBS** repos
  (`download.opensuse.org/repositories/network:/Meshtastic:/beta/Debian_13/`).
  Pin rule = newest build artifact from the beta channel with a fixed URL +
  sha256; deb-or-rpm decided by what the F45 station can consume. spec §8
  "meshtasticd (RPM)" corrected at M10. Ships in the kit, stays OFF at
  bootstrap.
- **artemis v4.2.0** ships x86_64 `.deb` AND `.rpm` AND Arch package (the
  earlier "no .rpm" recon was wrong; the spec's ".rpm" wording was right) —
  no aarch64 → pin the `.rpm` (native for the F45 station), arch-SKIP + WARN
  elsewhere. M10 keeps the x86_64-only caveat in the acceptance write-up.
- **gridtracker** ships x86_64 AND arm64 artifacts (deb/rpm/AppImage/tar.gz) —
  the "x86_64-only upstream" assumption was wrong; P4 pins the x86_64
  tarball row per arch policy regardless.
- **yaac**: aprs.org removed its YAAC pages (404); upstream is now the
  author's own site `https://www.ka2ddo.org/ka2ddo/YAAC.html` (AUR confirms
  the 1.0betaN version line). Pin from the author site artifact.
- **sdR++/wsjtx-improved anchoring:** SDR++ has no tagged releases since
  1.0.4 → pinned by master commit; WSJT-X Improved ("WS" v3.2.1, Qt6,
  GPL-3.0-or-later) publishes SF source drops → pinned by drop + sha256,
  corroborated by the Fedora COPR packager's ws.spec.
- **Digests:** GitHub release-asset API digests are preferred; codeload/source
  archives carry no upstream sha256 → compute once at pin and commit.

## Adopted defaults (from the plan review, 2026-10-29)
- **Region ride-along:** GRIB/OSM/ZIM `static:` payloads are optional kit rows
  with a Doctor WARN until regions are picked (M8+).
- **Wheels:** `meshtastic` CLI + transitive deps, downloaded inside the F45
  amd64 container (same ABI as the station), version-pinned in the recipe;
  resolved set enumerated at M6c.
- **kit-staging/** is untracked scratch from P1 dry runs; cleaned before gate
  runs, never committed.
- **Station-side bsdtar:** `libarchive-tools` availability verified in the M6a
  pre-pass; `install/lib/kit.sh` falls back to `rpm2cpio | cpio` if absent.

## Pin format (recipe schema, M6a)

```yaml
recipe:
  id: <registry id>            # must match registry install.recipe (tested)
  strategy: build|tarball      # must match registry install.strategy (tested)
  license: <SPDX>              # TBD-VERIFY-AT-PIN forbidden after pin (tested)
  source:                      # build strategy
    git: <repo URL>            # https git URL, no PIN-ME
    tag: <exact tag>           # the pinned upstream tag
    version: <short version>   # manifest ver column
    sha256: <64 hex>           # codeload archive for the tag
    build_container: fedora:45
    build_deps: [<dnf pkg>, …] # verified by the M6a pre-pass
  tarball:                     # tarball strategy
    url: <exact artifact URL>  # amd64 row; arm64 rows in tarball.arch_urls
    sha256: <64 hex>
    dest: /opt/grid-down/<id>
    arch: x86_64|any           # x86_64 → arch-SKIP + Doctor WARN elsewhere
    format: tar.gz|deb|rpm|npm # extraction follows this, never the URL
```
