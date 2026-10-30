# Phase 4 M10 — container E2E acceptance (x86_64 station profile)

Date: 2026-10-30 · Carrier: Docker Desktop `desktop-linux`, `--platform
linux/amd64` (Rosetta) · Scope: **container only** (no VM, no hardware).

## Result — 3×`P4: PASS`

Three independent air-gapped rounds, each a fresh container
(`--network none`) running the full-kit offline install + the M10 gate set:

| run | bootstrap | gates | outcome |
|---|---|---|---|
| 1 | 417 rpms + payloads from the kit repo only | G0–G11 | **P4: PASS** |
| 2 | fresh container, same kit | G0–G11 | **P4: PASS** |
| 3 | fresh container, same kit | G0–G11 | **P4: PASS** |

Driver: `tools/container-p4-e2e.sh` (host orchestrator) +
`tools/p4-e2e-gates.sh` (container-side gates; first failure exits nonzero,
final line `P4: PASS`). Logs: `$TMPDIR/gd-p4-e2e-logs/run-{1,2,3}.log`
(transient — outcomes are recorded here).

Kit: `build-kit.sh --arch x86_64` with all 19 pinned apps (ardop, artemis,
chattervox, commstatone, dump1090, flwrap, gridtracker, js8spotter, meshtastic,
meshtastic-web, meshtasticd, osm-navit, paracon, pat, qttermtcp, sdrpp,
wsjtx-improved, xygrib, yaac) — every install strategy, zero PIN-ME.
Manifest: **2190 files** (strict verify in every round, G1).

## Carrier (what the "station" is)

- `gd-p4-station:f45` = `fedora:45` **upgraded to current** + `python3
  python3-yaml file curl`. The upgrade matters: a stale base (libxml2 2.13.9)
  makes the air-gap-only kit transaction unsolvable — the installed libdnf5
  pins symbol versions the kit's libxml2 dropped, and with every repo but the
  kit disabled there is no resolver escape. Same lesson as
  `tools/roundtrip-dnf.sh`'s `UPD_IMG` carrier: a kit-installed station is a
  *maintained* F45 install. (M10 finding A.)
- The kit carries payloads + its own installer snapshot; the control suite is
  the operator checkout, bind-mounted read-only at `/gd` — matching the
  station model (suite = operator checkout, kit = payloads).
- Rounds: `docker run --rm --platform linux/amd64 --network none -v
  kit-staging:/kit:ro -v <repo>:/gd:ro` → `bash /kit/install/bootstrap.sh
  --kit /kit && bash /gd/tools/p4-e2e-gates.sh`.
- Build cost: full 19-app kit ≈ 15 min under Rosetta; rounds ≈ 3–4 min each.

## Gate set (per round)

| gate | assertion | evidence (run 1) |
|---|---|---|
| G0 | `uname -m` = x86_64; kit + checkout mounted | — |
| G1 | strict manifest verify (shipped `gd_kit_verify`) | 2190 files, no extras |
| G2 | installer tooling + runtime rows in the kit dnf set | bsdtar 3.8.9, Python 3.14.10 |
| G3 | payload placement + arch | ardopcf = ELF 64-bit x86-64; 19 dirs in /opt/grid-down; js8spotter.py + azure.tcl flat at docroot; meshtastic-web non-empty |
| G4 | real meshtastic CLI offline pins | `meshtastic --version` = 2.7.11; `configure --help` has `--seturl --port --host` |
| G5 | adapter spawn-free render | tcp 127.0.0.1 target, `--seturl ***` masked (raw PSK absent), payload config.user/config.lora; 3 refusals (no mesh block / serial w/o path / daemon.enabled) |
| G6 | `gd config show meshtastic` | kit venv CLI probed |
| G7 | js8spotter refuses as a service | `interactive CLI tool — run it in your terminal` |
| G8 | mesh bundle no-handset skip + web | `skip meshtastic (cli-tool: …)`; meshtastic-web :8092 → 200; stop → connection refused |
| G9 | meshtasticd refuses not-installed | `executable not found: meshtasticd — is the app installed?` |
| G10 | `gd doctor` 0 fail | ok + 3 WARNs (dialout group, no serial devices, no pipewire — container-appropriate) |
| G11 | status + `stop all` clean | — |

## Findings fixed by this E2E (the reason acceptance exists)

1. **`kit/opt` build payloads had no air-gap placement** — bootstrap placed
   dnf/tarball/wheels/static but never the 9 build-strategy payloads; M8's VM
   `/opt` content had been staged by hand. Fix: `gd_kit_place_opt()` in
   `install/lib/kit.sh` (+ bootstrap call).
2. **`local kit="$1" tsv="$kit/…"` self-reference** — all three placement
   helpers expanded `$kit` from the *caller's* scope: empty under bootstrap's
   `set -u` (fatal), wrong paths (`/artifacts.tsv`) otherwise. Split
   declarations. (This is what made (1) invisible: the helpers never really
   ran.)
3. **Recipe `dnf:` rows were read only for dnf/build strategies** — pip
   meshtastic's `python3.14` and tarball apps' runtime rows silently dropped
   from the kit set. `resolve-kit.py` now honors the row for every strategy;
   `bsdtar` (installer tooling) rides the kit like createrepo_c/python3-pip.
4. **js8spotter's "php -S :8091" service contract was a misreading** —
   upstream v1.09b is a Python/Tcl-Tk **desktop app** (no PHP, no web root);
   the M8 "happy path deferred to M10" never existed. Corrections: registry
   (`via: cli-tool`, honest cmd/tagline), supervisor (php special-case +
   PORTS entry removed; js8 bundle skips with note), port-map §8091
   (unassigned/reserved), spec §8 dnf list (php-cli out), tests updated.
   The kit-side `php-cli` row added earlier in this milestone was removed
   with it (no consumer left).
5. **Wrapped release tarballs** flattened at extraction (gridtracker/
   js8spotter/pat ship a top-level dir; `yaac`'s zip does not) — the recipe
   dest is now really the payload root; plus `--no-xattrs` (pat's tarball
   carries xattrs a container fs can't restore — restoring them is not part
   of the payload contract).

Suite after corrections: **250 passed**; black/ruff/isort clean.

## Operator-supplied static content (spec §6.8)

`kit-static/meshtastic-web/` staged by the orchestrator from the pinned
upstream release: `meshtastic/web` **v2.7.2** asset `build.tar`, sha256
`62657b85b4c24af4d44da2932b64143abdbf6e65a79fcb51fd0801d9540616e2` (verified
before staging; 320 files ride the kit manifest). Upstream ships only
precompressed assets (`index.html.gz`, `*.js.gz` — built for gzip_static
servers); the staging step decompresses on stage because the suite serves
plain `http.server` (port-map §8092) — gzip is transport encoding, content
bytes unchanged. `commstatone`'s slot stays empty by design (Doctor WARNs).

## Caveats (hardware-gated, carried)

- **Live handset session**: the mesh bundle's `meshtastic` member skips
  (no device in a container). BLE target flags stay unverified without a
  handset (M9 caveat stands).
- **VARA family / wine-hub pins, YAAC, gridtracker-on-GUI**: user-supplied
  installers / GUI + hardware paths — out of container scope by design
  (spec G6; P3's deferred on-air Winlink gate stands).
- **meshtasticd**: ships as the OBS `.deb` payload, stays off by default; the
  daemon itself is not dpkg-installed air-gap side yet (launch refuses,
  by design — ride-along #3). Its drop-in config adapter is Phase 4+.
- **Rosetta**: builder workarounds (no `statx` → python tarfile, host bind
  mounts) remain required; compile times ≈ 4–6× native.
- Station-baseline prep (not kit content): `python3 python3-yaml file curl`
  on a maintained F45 install — the suite (operator checkout) needs them.


