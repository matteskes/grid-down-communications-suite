#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
#
# tools/container-p4-e2e.sh — Phase 4 M10: full-kit container E2E, host side.
#
# Carrier: Docker Desktop, `--platform linux/amd64` (Rosetta) — the proven
# M6/M6d path (docs/audit/f45-package-audit.md). Scope-locked to a container:
# no VM, no network during the gated rounds (`--network none`).
#
# Stages:
#   1. preflight       — daemon up, amd64 smoke (`uname -m` = x86_64 under Rosetta)
#   2. station image   — fedora:45 + python3/python3-yaml/file/curl: the fresh
#                        F45 station baseline (control-suite YAML dep + probes);
#                        NOT kit content, the kit stays payload-only
#   3. operator static — kit-static/meshtastic-web/ staged from the pinned
#                        upstream release (sha256-verified; spec §6.8)
#   4. kit build       — build-kit.sh --arch x86_64, all 19 pinned apps
#   5. E2E rounds      — N fresh air-gapped containers: bootstrap.sh --kit, then
#                        tools/p4-e2e-gates.sh; each run's log must end "P4: PASS"
#
# Usage: container-p4-e2e.sh [--runs 3] [--skip-build]
set -euo pipefail

RUNS=3
SKIP_BUILD=0
while [ $# -gt 0 ]; do
    case "$1" in
        --runs) RUNS="$2"; shift 2 ;;
        --skip-build) SKIP_BUILD=1; shift ;;
        *) echo "usage: $0 [--runs N] [--skip-build]" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE=fedora:45
IMAGE=gd-p4-station:f45
LOG_DIR="${TMPDIR:-/tmp}/gd-p4-e2e-logs"
APPS="ardop,artemis,chattervox,commstatone,dump1090,flwrap,gridtracker,js8spotter,meshtastic,meshtastic-web,meshtasticd,osm-navit,paracon,pat,qttermtcp,sdrpp,wsjtx-improved,xygrib,yaac"
WEB_URL="https://github.com/meshtastic/web/releases/download/v2.7.2/build.tar"
WEB_SHA256="62657b85b4c24af4d44da2932b64143abdbf6e65a79fcb51fd0801d9540616e2"

mkdir -p "$LOG_DIR"
COPYFILE_DISABLE=1; export COPYFILE_DISABLE   # macOS bsdtar: no ._ members

stage() { printf '\n==== %s\n' "$*"; }
die() { printf 'container-p4-e2e: FATAL: %s\n' "$*" >&2; exit 1; }
sha256() { # portable sha256
    if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# ---- 1 · preflight -----------------------------------------------------------
stage "1/5 preflight — daemon, amd64 smoke"
docker version --format 'server {{.Server.Version}}' >/dev/null 2>&1 \
    || die "docker daemon not reachable — start Docker Desktop"
ARCH_OUT="$(docker run --rm --platform linux/amd64 "$BASE" sh -c 'uname -m')"
[ "$ARCH_OUT" = "x86_64" ] || die "amd64 smoke says '$ARCH_OUT', want x86_64"
printf '  amd64 smoke: x86_64 ok\n'

# ---- 2 · station image -------------------------------------------------------
stage "2/5 station image — $IMAGE (fresh-F45 baseline)"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    printf '  present (reuse — delete the tag to rebuild)\n'
else
    docker build --platform linux/amd64 -t "$IMAGE" - <<'DOCKERFILE'
FROM fedora:45
# A maintained F45 station: upgrade first so @System matches the kit closure's
# repo versions — a stale base (libxml2 2.13.9) makes the air-gap-only kit
# transaction unsolvable (installed libdnf5 pins symbols the kit's libxml2
# dropped). Same carrier lesson as roundtrip-dnf.sh's UPD_IMG.
# python3: control suite + suite-served static dashboards; python3-yaml: the
# suite's YAML dep (station baseline prep — operator checkout runs the suite);
# file: ELF arch probes; curl: the 8091/8092 probes.
RUN dnf -y upgrade \
    && dnf -y --setopt=install_weak_deps=False install python3 python3-yaml file curl \
    && dnf clean all
DOCKERFILE
fi

# ---- 3 · operator static content (meshtastic-web) ----------------------------
stage "3/5 operator static — kit-static/meshtastic-web/"
STATIC="$ROOT/kit-static/meshtastic-web"
if [ -n "$(ls -A "$STATIC" 2>/dev/null)" ]; then
    printf '  staged: %s files (keep)\n' "$(find "$STATIC" -type f | wc -l | tr -d ' ')"
else
    TMP="$(mktemp -d "${TMPDIR:-/tmp}/gd-web.XXXXXX")"
    trap 'rm -rf "$TMP"' EXIT
    printf '  fetching %s\n' "$WEB_URL"
    curl -fsSL -o "$TMP/build.tar" "$WEB_URL" || die "meshtastic-web release fetch failed"
    GOT="$(sha256 "$TMP/build.tar")"
    [ "$GOT" = "$WEB_SHA256" ] || die "meshtastic-web sha256 mismatch: $GOT (want $WEB_SHA256)"
    tar -xf "$TMP/build.tar" -C "$TMP/ext" 2>/dev/null || { mkdir -p "$TMP/ext"; tar -xf "$TMP/build.tar" -C "$TMP/ext"; }
    # Upstream ships ONLY precompressed assets (index.html.gz, *.js.gz — built
    # for gzip_static servers). The suite serves plain http.server (port-map
    # §8092), so decompress on stage: gzip is transport encoding, the content
    # bytes are unchanged. Recorded in docs/audit/p4-container-e2e.md.
    find "$TMP/ext" -name '*.gz' -exec gunzip -q {} +
    DOCROOT="$(dirname "$(find "$TMP/ext" -maxdepth 3 -name index.html | head -1)")"
    [ -n "${DOCROOT:-}" ] && [ -f "$DOCROOT/index.html" ] || die "no index.html in build.tar"
    mkdir -p "$STATIC"
    ( shopt -s dotglob; cp -R "$DOCROOT"/* "$STATIC"/ )
    printf '  staged: %s files (v2.7.2, sha256 ok)\n' "$(find "$STATIC" -type f | wc -l | tr -d ' ')"
fi
[ -f "$STATIC/index.html" ] || die "kit-static/meshtastic-web/index.html missing"

# ---- 4 · full x86_64 kit build -----------------------------------------------
stage "4/5 kit build — build-kit.sh --arch x86_64 (19 apps; Rosetta, be patient)"
if [ "$SKIP_BUILD" = 1 ]; then
    printf '  skipped (--skip-build)\n'
else
    ( cd "$ROOT" && bash install/bundle-builder/build-kit.sh --arch x86_64 --apps "$APPS" ) \
        || die "kit build failed"
fi
[ -f "$ROOT/kit-staging/manifest.sha256" ] || die "kit-staging incomplete (no manifest)"
[ -f "$ROOT/kit-staging/static/meshtastic-web/index.html" ] \
    || die "operator static content did not ride the kit (kit-staging/static/meshtastic-web)"
printf '  kit: %s manifest files, static ok\n' "$(wc -l < "$ROOT/kit-staging/manifest.sha256" | tr -d ' ')"

# ---- 5 · air-gapped E2E rounds ------------------------------------------------
stage "5/5 E2E rounds — $RUNS fresh container(s), --network none"
pass=0
for i in $(seq 1 "$RUNS"); do
    LOG="$LOG_DIR/run-$i.log"
    printf -- '\n---- run %s/%s (log: %s)\n' "$i" "$RUNS" "$LOG"
    set -o pipefail
    if docker run --rm --platform linux/amd64 --network none \
        -v "$ROOT/kit-staging:/kit:ro" \
        -v "$ROOT:/gd:ro" \
        "$IMAGE" bash -c 'set -e; bash /kit/install/bootstrap.sh --kit /kit && exec bash /gd/tools/p4-e2e-gates.sh' \
        2>&1 | tee "$LOG"; then
        : # exit status alone is not the gate — grep the marker
    fi
    set +o pipefail
    if grep -q '^P4: PASS$' "$LOG"; then
        printf 'run %s: P4: PASS\n' "$i"
        pass=$((pass + 1))
    else
        die "run $i did not reach P4: PASS — tail of $LOG:
$(tail -20 "$LOG")"
    fi
done

printf '\n%s\n' "======================================================="
printf 'container-p4-e2e: %s/%s runs P4: PASS  (x86_64 station profile, air-gapped)\n' "$pass" "$RUNS"
printf 'logs: %s\n' "$LOG_DIR"
[ "$pass" -eq "$RUNS" ]

