# Phase 1 round-trip — dnf-only kit, online → air-gap

Spec §11 P1. Builder: `install/bundle-builder/build-kit.sh` · payload:
`install/bundle-builder/container-build.sh` · installer: `install/bootstrap.sh`
(+ `install/lib/kit.sh`) · harness: `tools/roundtrip-dnf.sh`.

## dnf5 spike findings (tools/spike-dnf5-offline.sh, fedora:45 linux/amd64, 2026-10-26)

| # | Question | Finding |
|---|---|---|
| S1 | dnf flavor | **dnf5 5.4.3.0** |
| S1 | closure completeness | `dnf download --resolve --alldeps` → **421 rpms incl. `glibc-2.44-2.fc45`** — complete clean-host closure. Plain `--resolve` → 276 (skips base-system rpms; insufficient). **Builder uses `--resolve --alldeps`.** |
| S2 | createrepo_c | **not in the base image**; must be installed explicitly (loudly — a silent install failure was observed once). |
| S3 | offline install flags | **`--cacheonly` is unsafe**: it resolved from stale system-cache metadata of network repos (air-gap leak) and silently skipped the uncached kit repo. **Correct: `--disablerepo='*' --enablerepo=gd-kit --setopt=gd-kit.skip_if_unavailable=0`** (no network-capable repo enabled; broken kit fails loudly). |
| S4 | aarch64 kits | `dnf --forcearch=aarch64 download` works from the amd64 container (`alsa-lib-1.2.16.1-2.fc45.aarch64.rpm` et al.) → aarch64 targets (any Fedora 45 ARM host) are servable as-is. |

## Kit layout (spec §6.8, dnf-only subset for P1)

```
kit/
├── repo/                 merged local dnf repo (fedora + rpmfusion rpms + repodata)
├── install/              bootstrap.sh + lib/{common,kit}.sh + recipes/ snapshot
├── apps.txt              selected app ids
├── packages.txt          resolved package specs for offline dnf install
└── manifest.sha256       TSV: sha256 · relpath · source · version
```

## Automated round-trip (container, `--network none`)

**PASS — 2026-10-26, round 1 (dnf-only, x86_64):**

```
== [1/3] build kit (online side)      apps: direwolf,fldigi,hamlib
[kit-build] repo: 510 rpms + repodata · manifest: 561 files hashed
  size 309M kit-staging
== [2/3] air-gap install (--network none, linux/amd64, image gd-f45-current)
[grid-down] kit manifest verified: 561 files
[grid-down] offline install: 38 words of package specs from kit repo only
== [3/3] assertions
direwolf-1.8.1-5.fc45.x86_64 / fldigi-4.2.13-1.fc45.x86_64
hamlib-4.6.5-9.fc45.x86_64 / python3-hamlib-4.6.5-9.fc45.x86_64
binary: direwolf OK · binary: fldigi OK · ldd: clean
ROUNDTRIP OK — 111s total
```

**PASS — 2026-10-26, round 2 (`--with-rpmfusion`, adds ffmpeg):**

```
[kit-build] rpmfusion: rpmfusion-free-release-45-0.2.noarch rpmfusion-nonfree-release-45-0.2.noarch
           (pinned URLs + sha256 re-verified in-container, per G5)
[kit-build] repo: 573 rpms · manifest: 624 files hashed
[grid-down] kit manifest verified: 624 files
[2/365] ffmpeg-0:9.0.2-1.fc45.x86_64  ← RPM Fusion build, as audited
install complete · binary: direwolf OK · binary: fldigi OK · ldd: clean
ROUNDTRIP OK — 85s total
```

(The `Header … NOKEY` line is the expected `gpgcheck=0` warning — kit integrity
is enforced by `manifest.sha256`, verified before the repo is enabled.)

## VM acceptance (Fedora 45 GUI VM, aarch64 or x86_64)

Status: **PENDING (human half)** — runbook: `docs/phase1-vm-runbook.md`.

## Findings that shaped the design (round-trip debugging, 2026-10-26)

1. **Apple Silicon `--platform` trap (install side)** — the harness's install
   container ran native **arm64** `fedora:45` and the solver rejected the x86_64
   kit ("does not have a compatible architecture"). All containers are pinned
   `linux/amd64` explicitly, same lesson as the Phase 0 audit.
2. **Snapshot consistency / soname jump** — a stale install base pinned
   `libxml2.so.2` (2.13.9) while repo-HEAD libs need `.so.16` (2.15.4) → solver
   deadlock on the old `libdnf5`. Fixed two ways:
   - kit carries a **base-refresh set** (`dnf dnf5 libdnf5 librepo libsolv
     libxml2` + closure) so it is internally consistent and can upgrade older
     targets into its snapshot in one transaction;
   - targets must be **patched to the kit's snapshot date before air-gapping**
     (harness installs from an `dnf -y update`-ed image; VM runbook step 3).
   The complete fix (target-state independence: full point-in-time repo mirror)
   is the P3 strategy per spec §6.8.


## Notes / caveats

- Manifest `source` column: dnf-mirror URLs rotate, so rpm provenance records the
  repo identity (`fedora:45 …` / pinned `mirrors.rpmfusion.org` release RPMs) rather
  than a per-rpm mirror URL; per-rpm URL capture is P3 polish.
- Trust model: kit repo uses `gpgcheck=0`; integrity is enforced by
  `manifest.sha256` (strict re-hash + file-count equality) verified *before* the
  repo is enabled. Documented in the generated repo file header (spec §5).
- The container round-trip for `--arch aarch64` installs aarch64 rpms inside an
  amd64 container (exec assertions are skipped in that mode) — the real aarch64
  proof is the VM runbook itself.
