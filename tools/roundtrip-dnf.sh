#!/usr/bin/env bash
# P1 acceptance harness: build a dnf-only kit online, then install it in a
# --network none container (true air gap). Spec §11 P1 (VM acceptance is the
# human half: docs/phase1-vm-runbook.md).
#
# Usage: tools/roundtrip-dnf.sh [--with-rpmfusion] [--arch x86_64|aarch64]
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${GD_BASE_IMAGE:-fedora:45}"
ARCH="x86_64"; RPMF_FLAG=""; RPMF_TXT="no"
while [ $# -gt 0 ]; do
    case "$1" in
        --with-rpmfusion) RPMF_FLAG="--with-rpmfusion"; RPMF_TXT="yes"; shift ;;
        --arch)           ARCH="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

T0=$(date +%s)
echo "== [1/3] build kit (online side) =="
APPS="direwolf,fldigi,hamlib"
if [ -n "$RPMF_FLAG" ]; then APPS="direwolf,fldigi,hamlib,ffmpeg"; fi   # rpmfusion implies an rpmfusion app
"$GD_ROOT/install/bundle-builder/build-kit.sh" --arch "$ARCH" --no-pack --apps "$APPS" \
    ${RPMF_FLAG:+"$RPMF_FLAG"}

KIT="$GD_ROOT/kit-staging"

# Install-side image: updated to repo-HEAD so it represents the spec's target —
# "a Fedora 45 machine patched up to the kit's snapshot date, then air-gapped".
# (A stale base pins old library sonames the kit cannot satisfy; see
# docs/audit/phase1-roundtrip.md. In real ops: `dnf -y update` BEFORE air-gapping.)
UPD_IMG="${GD_INSTALL_IMAGE:-gd-f45-current:latest}"
if ! docker image inspect "$UPD_IMG" >/dev/null 2>&1; then
    echo "== building updated install image: $UPD_IMG (one-time) =="
    printf 'FROM %s\nRUN dnf -y update\n' "$IMAGE" \
        | docker build --platform linux/amd64 -t "$UPD_IMG" - >/dev/null
fi

echo
echo "== [2/3] air-gap install (--network none, linux/amd64) =="
echo "== [3/3] assertions =="
docker run --rm --platform linux/amd64 --network none -v "$KIT:/kit:ro" "$UPD_IMG" bash -c '
    set -e
    bash /kit/install/bootstrap.sh --kit /kit
    echo "--- assertions ---"
    rpm -q direwolf fldigi hamlib python3-hamlib
    command -v direwolf >/dev/null && echo "binary: direwolf OK"
    command -v fldigi   >/dev/null && echo "binary: fldigi OK"
    if ldd "$(command -v direwolf)" 2>/dev/null | grep -qi "not found"; then
        echo "FAIL: direwolf has unresolved libraries" >&2; exit 1
    fi
    echo "ldd: clean"
'
echo
echo "ROUNDTRIP OK — $(( $(date +%s) - T0 ))s total (arch=$ARCH rpmfusion=$RPMF_TXT)"
echo "kit staging tree: $KIT  (record results in docs/audit/phase1-roundtrip.md)"