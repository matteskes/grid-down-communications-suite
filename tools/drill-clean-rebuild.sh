#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
#
# tools/drill-clean-rebuild.sh — Phase 5 M13 clean-host drill (docs/phase5-plan.md
# M13, spec §10): prove a x86_64 station rebuilds from the M12 USB layout on a
# host with no other state. Build → pack (FAT32 USB parts) → kit-staging WIPED,
# then N fresh fedora:45 linux/amd64 containers, each air-gapped (--network
# none), mounting the layout read-only, unpacking it with the kit's own
# unpacker, installing the FULL app set offline and running the gate chain in
# tools/p5-drill-gates.sh until it prints exactly "P5: PASS". The drill never
# sees the staging tree: the USB layout is the sole source, like the field stick.
#
# Stage list (per invocation):
#   1 preflight — docker daemon + amd64 smoke (P4 harness)
#   2 image     — gd-p5-station:f45: updated fresh-F45 baseline (P4 recipe)
#   3 static    — operator content staging, kit-static/meshtastic-web (P4 pin)
#   4 build     — full app set (registry minus the unstaged VARA imports):
#                 build-kit.sh --no-pack → make-usb-kit.sh → rm -rf kit-staging
#   5 rounds    — N air-gapped clean-host rebuilds (default 3), each timed
#
# Usage: tools/drill-clean-rebuild.sh [--runs N] [--skip-build]
#   --runs N      rebuild rounds (default 3)
#   --skip-build  reuse usb-kit/ from an earlier invocation
# Logs: ${TMPDIR:-/tmp}/gd-p5-drill-logs/ — record outcomes in
# docs/audit/p5-clean-rebuild.md (human GUI-VM leg: docs/phase5-runbook.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BASE="fedora:45"                 # drill base + kit-build carrier (spec §10)
IMAGE="gd-p5-station:f45"
PLATFORM="linux/amd64"
RUNS=3
SKIP_BUILD=0
USB="$ROOT/usb-kit"
LOG_DIR="${TMPDIR:-/tmp}/gd-p5-drill-logs"
COPYFILE_DISABLE=1; export COPYFILE_DISABLE   # macOS tar/bsdtar: no ._ members

while [ $# -gt 0 ]; do
    case "$1" in
        --runs)       RUNS="$2"; shift 2 ;;
        --skip-build) SKIP_BUILD=1; shift ;;
        -h|--help)    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)            echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
    esac
done
case "$RUNS" in ''|*[!0-9]*) echo "bad --runs: $RUNS" >&2; exit 2 ;; esac
[ "$RUNS" -ge 1 ] || { echo "bad --runs: $RUNS" >&2; exit 2; }

stage() { printf '\n==== %s\n' "$*"; }
die() { printf 'drill-clean-rebuild: FATAL: %s\n' "$*" >&2; exit 1; }
sha256() { # portable sha256
    if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# M13 drill app set: every registry app except the VARA trio whose kit-import
# files aren't staged yet (install/kit-import/ carries only VARA HF/FM +
# VarAC; resolve-kit.py fails closed on the rest — the honest gap, M13).
EXCLUDED_APP_IDS="vara-sat vara-chat vara-term"
APPS_CSV="$(cd "$ROOT/control/registry/apps" \
    && ls *.yaml | sed 's/\.yaml$//' \
    | grep -vxF $(printf -- '-e %s ' $EXCLUDED_APP_IDS) \
    | paste -sd, -)"
[ -n "$APPS_CSV" ] || die "no apps derived from control/registry/apps/"
N_APPS=$(printf '%s\n' "$APPS_CSV" | tr ',' '\n' | wc -l | tr -d ' ')
printf 'drill app set: %s of %s registry apps (excluded: %s)\n' \
    "$N_APPS" "$(ls "$ROOT"/control/registry/apps/*.yaml | wc -l | tr -d ' ')" \
    "$(printf '%s ' $EXCLUDED_APP_IDS | sed 's/ $//')"

# ---- 1 · preflight -----------------------------------------------------------
stage "1/5 preflight — daemon, amd64 smoke"
docker version --format 'server {{.Server.Version}}' >/dev/null 2>&1 \
    || die "docker daemon not reachable — start Docker Desktop"
ARCH_OUT="$(docker run --rm --platform "$PLATFORM" "$BASE" sh -c 'uname -m')"
[ "$ARCH_OUT" = "x86_64" ] || die "amd64 smoke says '$ARCH_OUT', want x86_64"
printf '  amd64 smoke: x86_64 ok\n'

# ---- 2 · station image -------------------------------------------------------
stage "2/5 station image — $IMAGE (updated fresh-F45 baseline)"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    printf '  present (reuse — delete the tag to rebuild)\n'
else
    docker build --platform "$PLATFORM" -t "$IMAGE" - <<'DOCKERFILE'
FROM fedora:45
# A maintained F45 station: upgrade first so @System matches the kit closure's
# repo versions — a stale base (libxml2 2.13.9) makes the air-gap-only kit
# transaction unsolvable (installed libdnf5 pins symbols the kit's libxml2
# dropped). Same carrier lesson as roundtrip-dnf.sh's UPD_IMG (P4 recipe).
# python3: control suite + unpack-usb-kit.sh; python3-yaml: the suite's YAML
# dep; file: ELF arch probes.
RUN dnf -y upgrade \
    && dnf -y --setopt=install_weak_deps=False install python3 python3-yaml file \
    && dnf clean all
DOCKERFILE
fi

# ---- 3 · operator static content (meshtastic-web) ----------------------------
stage "3/5 operator static — kit-static/meshtastic-web/"
if [ "$SKIP_BUILD" = 1 ]; then
    printf '  skipped (--skip-build; already in the packed layout)\n'
else
    STATIC="$ROOT/kit-static/meshtastic-web"
    WEB_URL="https://github.com/meshtastic/web/releases/download/v2.7.2/build.tar"
    WEB_SHA256="62657b85b4c24af4d44da2932b64143abdbf6e65a79fcb51fd0801d9540616e2"
    if [ -n "$(ls -A "$STATIC" 2>/dev/null)" ]; then
        printf '  staged: %s files (keep)\n' "$(find "$STATIC" -type f | wc -l | tr -d ' ')"
    else
        TMP="$(mktemp -d "${TMPDIR:-/tmp}/gd-web.XXXXXX")"
        trap 'rm -rf "$TMP"' EXIT
        printf '  fetching %s\n' "$WEB_URL"
        curl -fsSL -o "$TMP/build.tar" "$WEB_URL" || die "meshtastic-web release fetch failed"
        GOT="$(sha256 "$TMP/build.tar")"
        [ "$GOT" = "$WEB_SHA256" ] || die "meshtastic-web sha256 mismatch: $GOT (want $WEB_SHA256)"
        mkdir -p "$TMP/ext"
        tar -xf "$TMP/build.tar" -C "$TMP/ext"
        # Upstream ships ONLY precompressed assets (index.html.gz, *.js.gz —
        # built for gzip_static servers). The suite serves plain http.server
        # (port-map §8092), so decompress on stage: gzip is transport encoding,
        # the content bytes are unchanged. Recorded in docs/audit/p4-*.md.
        find "$TMP/ext" -name '*.gz' -exec gunzip -q {} +
        DOCROOT="$(dirname "$(find "$TMP/ext" -maxdepth 3 -name index.html | head -1)")"
        [ -n "${DOCROOT:-}" ] && [ -f "$DOCROOT/index.html" ] || die "no index.html in build.tar"
        mkdir -p "$STATIC"
        ( shopt -s dotglob; cp -R "$DOCROOT"/* "$STATIC"/ )
        printf '  staged: %s files (v2.7.2, sha256 ok)\n' \
            "$(find "$STATIC" -type f | wc -l | tr -d ' ')"
    fi
    [ -f "$STATIC/index.html" ] || die "kit-static/meshtastic-web/index.html missing"
fi

# ---- 4 · build + pack: the USB layout becomes the only source -----------------
stage "4/5 build + pack — full set ($N_APPS apps), then kit-staging is wiped"
if [ "$SKIP_BUILD" = 1 ]; then
    printf '  skipped (--skip-build)\n'
    [ -f "$USB/usb-manifest.sha256" ] || die "--skip-build but $USB has no usb-manifest.sha256"
else
    printf '  building (Rosetta, be patient — build log is this shell)\n'
    ( cd "$ROOT" && bash install/bundle-builder/build-kit.sh \
        --arch x86_64 --no-pack --apps "$APPS_CSV" ) || die "kit build failed"
    [ -f "$ROOT/kit-staging/manifest.sha256" ] || die "kit-staging incomplete (no manifest)"
    [ -f "$ROOT/kit-staging/static/meshtastic-web/index.html" ] \
        || die "operator static content did not ride the kit (kit-staging/static/meshtastic-web)"
    printf '  kit: %s manifest files, static ok — packing USB layout\n' \
        "$(wc -l < "$ROOT/kit-staging/manifest.sha256" | tr -d ' ')"
    ( cd "$ROOT" && bash tools/make-usb-kit.sh --from "$ROOT/kit-staging" --out "$USB" --force ) \
        || die "usb pack failed"
    rm -rf "$ROOT/kit-staging"   # THE point of the drill: layout-only from here
    printf '  kit-staging wiped — %s is the sole source\n' "$USB"
fi
ls "$USB"/*.part-* >/dev/null 2>&1 || die "no *.part-* parts at $USB"
printf '  layout: %s part file(s), %s\n' \
    "$(ls "$USB"/*.part-* | wc -l | tr -d ' ')" "$(du -sh "$USB" | cut -f1)"

# ---- 5 · clean-host rebuild rounds -------------------------------------------
stage "5/5 clean-host rebuild rounds — $RUNS x --network none (tools/p5-drill-gates.sh)"
mkdir -p "$LOG_DIR"
T_ALL=$(date +%s); PASS=0
for i in $(seq 1 "$RUNS"); do
    LOG="$LOG_DIR/p5-run-$i.log"
    printf -- '\n---- run %s/%s (log: %s)\n' "$i" "$RUNS" "$LOG"
    T0=$(date +%s)
    set -o pipefail
    # the `if` guards the pipeline from errexit so a failure gets the
    # die-branch treatment (tail + log path) instead of a bare exit
    if docker run --rm --platform "$PLATFORM" --network none \
        -v "$USB:/usb:ro" \
        -v "$ROOT:/gd:ro" \
        "$IMAGE" bash /gd/tools/p5-drill-gates.sh 2>&1 | tee "$LOG"; then
        RC=0
    else
        RC=$?
    fi
    set +o pipefail
    if [ "$RC" -eq 0 ] && grep -q '^P5: PASS$' "$LOG"; then
        PASS=$((PASS + 1))
        printf '  run %s: P5: PASS (%ss)\n' "$i" "$(( $(date +%s) - T0 ))"
    else
        printf '  run %s FAILED (rc=%s) — last 40 lines:\n' "$i" "$RC" >&2
        tail -n 40 "$LOG" >&2 || true
        die "run $i did not reach P5: PASS (log: $LOG)"
    fi
done
TOTAL=$(( $(date +%s) - T_ALL ))
printf '\nDRILL OK — %s/%s clean-host rebuilds passed in %ss (logs: %s)\n' \
    "$PASS" "$RUNS" "$TOTAL" "$LOG_DIR"
echo "record results in docs/audit/p5-clean-rebuild.md (human leg: docs/phase5-runbook.md)"