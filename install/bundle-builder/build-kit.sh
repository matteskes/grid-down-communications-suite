#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Grid Down offline-kit builder — host-side orchestrator (M6c).
# Resolves ALL install strategies via resolve-kit.py into a JSON plan, then runs
# container-build.sh inside fedora:45 (network ON) to produce a sha256-pinned kit.
#
# Usage: install/bundle-builder/build-kit.sh
#          [--arch x86_64|aarch64]      target arch (default x86_64; aarch64 is
#                                       dnf-only subsets — phase4-plan §Arch policy)
#          [--apps id[,id...]]          apps to include (default: direwolf,fldigi,hamlib)
#          [--all-dnf]                  include every dnf-strategy app instead
#          [--with-rpmfusion]           enable pinned RPM Fusion release RPMs
#                                       (auto-on if a selected app needs rpmfusion-free)
#          [--out DIR]                  kit output dir (default: $GD_ROOT/kit)
#          [--no-pack]                  leave the staging tree only (round-trip dev loop)
# Env: GD_BASE_IMAGE (default fedora:45), GD_KIT_OUT
# Specs: docs/phase4-plan.md (M6c); audit: docs/audit/f45-package-audit.md.
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${GD_BASE_IMAGE:-fedora:45}"
ARCH="x86_64"; APPS=""; ALL_DNF=0; RPMFUSION="${GD_RPMFUSION:-0}"; PACK=1
OUT="${GD_KIT_OUT:-$GD_ROOT/kit}"

while [ $# -gt 0 ]; do
    case "$1" in
        --arch)           ARCH="$2"; shift 2 ;;
        --apps)           APPS="$2"; shift 2 ;;
        --all-dnf)        ALL_DNF=1; shift ;;
        --with-rpmfusion) RPMFUSION=1; shift ;;
        --out)            OUT="$2"; shift 2 ;;
        --no-pack)        PACK=0; shift ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
    esac
done
case "$ARCH" in x86_64|aarch64) ;; *) echo "bad --arch: $ARCH" >&2; exit 2 ;; esac

# --- resolve recipes -> JSON plan (guards: pins, unknown ids, arch policy) ---
PY="python3"
if [ -x "$GD_ROOT/.venv/bin/python" ]; then PY="$GD_ROOT/.venv/bin/python"; fi
PLAN_ARGS=(--arch "$ARCH")
[ "$ALL_DNF" = "1" ] && PLAN_ARGS+=(--all-dnf)
[ -n "$APPS" ] && PLAN_ARGS+=(--apps "$APPS")
PLAN="$("$PY" "$GD_ROOT/install/bundle-builder/resolve-kit.py" \
    "$GD_ROOT/install/recipes" "${PLAN_ARGS[@]}")" || {
    echo "FATAL: plan resolution failed (see message above)" >&2; exit 2; }

plan_field() { # plan_field <python-expr on $p> — read a field of the plan JSON
    PLAN="$PLAN" "$PY" -c "import json,os; p=json.loads(os.environ['PLAN']); print($1)"
}
SEL_APPS="$(plan_field "' '.join(p['apps'])")"
N_BUILD="$(plan_field "len(p['build'])")"
N_TARBALL="$(plan_field "len(p['tarball'])")"
N_PIP="$(plan_field "len(p['pip'])")"
N_STATIC="$(plan_field "len(p['static'])")"
N_DNF="$(plan_field "len([r for r in p['dnf'] if r['id'] in p['apps']])")"
PKGS_FEDORA="$(plan_field "' '.join(p['pkgs_fedora'])")"
PKGS_RPMFUSION="$(plan_field "' '.join(p['pkgs_rpmfusion'])")"
if [ -n "${PKGS_RPMFUSION// /}" ] && [ "$RPMFUSION" != "1" ]; then
    RPMFUSION=1   # an rpmfusion-free app is in the set — enablement is mandatory
fi

echo "grid-down kit builder"
echo "  apps        : $SEL_APPS"
echo "  strategies  : dnf=$N_DNF build=$N_BUILD tarball=$N_TARBALL pip=$N_PIP static=$N_STATIC"
echo "  packages    : fedora=[$PKGS_FEDORA] rpmfusion=[$PKGS_RPMFUSION]"
echo "  arch        : $ARCH   rpmfusion-enablement: $RPMFUSION"
echo "  image       : $IMAGE (linux/amd64)   out: $OUT   pack: $PACK"

STAGING="$GD_ROOT/kit-staging"
rm -rf "$STAGING"; mkdir -p "$STAGING" "$OUT"
# Build scratch lives on a HOST bind mount: the container-local overlay under
# Rosetta (linux/amd64 on Apple Silicon) returns ENOSYS for tar's mkdir/open
# calls (same syscall family as the M4 fexecve quirk — audit doc). virtiofs
# bind mounts are the well-trodden path. Kept OUTSIDE the staging tree so
# build scratch can never leak into the kit or its manifest.
BUILD_HOST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/gd-kitbuild.XXXXXX")"
trap 'rm -rf "$BUILD_HOST_DIR"' EXIT
docker run --rm --platform linux/amd64 \
    -e GD_IN_OUT=/kit-staging -e GD_IN_ARCH="$ARCH" -e GD_IN_RPMFUSION="$RPMFUSION" \
    -e GD_IN_PACK="$PACK" -e GD_IN_PLAN="$PLAN" \
    -v "$GD_ROOT/install:/install-src:ro" \
    -v "$STAGING:/kit-staging" \
    -v "$OUT:/kits" \
    -v "$BUILD_HOST_DIR:/gd-build" \
    "$IMAGE" bash /install-src/bundle-builder/container-build.sh

# --- post-build sanity on the host ---
[ -f "$STAGING/manifest.sha256" ] || { echo "FATAL: kit staging incomplete" >&2; exit 1; }
echo "kit ready:"
awk -F'\t' '{n++} END {printf "  manifest : %d files\n", n}' "$STAGING/manifest.sha256"
printf '  rpms     : %s\n' "$(find "$STAGING/repo" -maxdepth 1 -name '*.rpm' | wc -l | tr -d ' ')"
[ -d "$STAGING/opt" ] && printf '  built    : %s\n' \
    "$(find "$STAGING/opt" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') dirs under kit/opt/"
[ -d "$STAGING/wheels" ] && printf '  wheels   : %s\n' \
    "$(find "$STAGING/wheels" -name '*.whl' | wc -l | tr -d ' ')"
[ -d "$STAGING/tarball" ] && printf '  artifacts: %s\n' \
    "$(find "$STAGING/tarball" -type f | wc -l | tr -d ' ')"
printf '  apps     : %s\n' "$(tr '\n' ' ' < "$STAGING/apps.txt")"
du -sh "$STAGING" "$OUT" 2>/dev/null | sed 's/^/  size /' || true

