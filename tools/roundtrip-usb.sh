#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Round-trip gate: P4 USB pack/unpack (M12). Spec §6.9 P4 — the kit's transport
# loop is only proven when a full install comes back out of it:
#   1. build a real kit into $GD_ROOT/kit-staging (no tarball);
#   2. pack it with tools/make-usb-kit.sh (FAT32 parts + sidecar manifest);
#   3. delete the staging tree — the USB layout is now the ONLY source, exactly
#      like a wiped station beside a stick;
#   4. restore with tools/unpack-usb-kit.sh (part-hash verify, reassemble,
#      extract, re-hash the tree against the kit's own manifest);
#   5. air-gap install from the RESTORED tree (--network none, operator via
#      --user) and assert the postinst delegation that M12 makes possible:
#        R  bootstrap reaches install complete (its own gd_kit_verify accepts
#           the restored tree — the installer re-checks every hash)
#        A  dialout applied; units DELEGATE to the kit's control/ suite
#           (rendered into the operator's user unit dir, daemon-reload skips)
#        N  re-run is a no-op: dnf reports installed, watermarks skip, and
#           state.json is byte-identical
#   6. gate line: "ROUNDTRIP OK — usb pack/unpack (M12 gate)".
# Heavy: downloads the full dnf closure once (builder), then installs from the
# kit. Budget a few minutes. The full postinst suite lives in roundtrip-dnf.sh
# (M11d); this gate adds the transport leg, not a second copy of it.
# Usage: tools/roundtrip-usb.sh [--arch x86_64|aarch64] [--with-rpmfusion]
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${GD_BASE_IMAGE:-fedora:45}"
ARCH="x86_64"; RPMF_FLAG=""; RPMF_TXT="no"
while [ $# -gt 0 ]; do
    case "$1" in
        --arch)           ARCH="$2"; shift 2 ;;
        --with-rpmfusion) RPMF_FLAG="--with-rpmfusion"; RPMF_TXT="yes"; shift ;;
        *)                echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done
case "$ARCH" in x86_64|aarch64) ;; *) echo "bad --arch: $ARCH" >&2; exit 2 ;; esac
case "$ARCH" in
    x86_64)  PLATFORM="linux/amd64" ;;
    aarch64) PLATFORM="linux/arm64" ;;
esac

T0=$(date +%s)
STAGING="$GD_ROOT/kit-staging"
USB="$GD_ROOT/usb-kit"
RESTORED="$GD_ROOT/kit-staging-restored"   # parent; the tree lands at $RESTORED/kit-staging

echo "== [1/4] build kit (online side) =="
APPS="direwolf,fldigi,hamlib"
if [ -n "$RPMF_FLAG" ]; then APPS="direwolf,fldigi,hamlib,ffmpeg"; fi
"$GD_ROOT/install/bundle-builder/build-kit.sh" --arch "$ARCH" --no-pack --apps "$APPS" \
    ${RPMF_FLAG:+"$RPMF_FLAG"}

echo
echo "== [2/4] pack to the USB layout, then wipe the staging tree =="
"$GD_ROOT/tools/make-usb-kit.sh" --from "$STAGING" --out "$USB" --force
rm -rf "$STAGING"
[ ! -e "$STAGING" ] || { echo "FATAL: staging tree survived the wipe" >&2; exit 1; }
echo "  staging wiped — the USB layout is the only source now"

echo
echo "== [3/4] restore from the USB layout =="
"$GD_ROOT/tools/unpack-usb-kit.sh" --from "$USB" --out "$RESTORED" --force
# unpack-usb-kit.sh's --out is the PARENT of the restored kit-staging/ tree
KIT="$GD_ROOT/kit-staging-restored/kit-staging"
[ -f "$KIT/manifest.sha256" ] || { echo "FATAL: restored tree incomplete" >&2; exit 1; }

# --- tamper probe: one flipped byte in a part is refused loudly ----------------
TAMPER="$GD_ROOT/usb-kit-tampered"
rm -rf "$TAMPER" "$GD_ROOT/kit-staging-tampered"
cp -R "$USB" "$TAMPER"
PART="$(ls "$TAMPER"/*.part-* | head -1)"
printf 'X' | dd of="$PART" bs=1 seek=100 conv=notrunc 2>/dev/null
if "$GD_ROOT/tools/unpack-usb-kit.sh" --from "$TAMPER" --out "$GD_ROOT/kit-staging-tampered" 2>/dev/null; then
    echo "FATAL: a tampered layout unpacked cleanly" >&2
    exit 1
fi
echo "  tamper probe: flipped byte refused (part hash mismatch) — OK"
rm -rf "$TAMPER" "$GD_ROOT/kit-staging-tampered"

echo
echo "== [4/4] air-gap install from the RESTORED tree (--network none, $PLATFORM) =="
# Same updated-base discipline as roundtrip-dnf.sh: the install image is
# repo-HEAD (a stale base pins library sonames the kit cannot satisfy).
UPD_IMG="${GD_INSTALL_IMAGE:-gd-f45-current:latest}"
if ! docker image inspect "$UPD_IMG" >/dev/null 2>&1; then
    echo "== building updated install image: $UPD_IMG (one-time) =="
    printf 'FROM %s\nRUN dnf -y update\n' "$IMAGE" \
        | docker build --platform "$PLATFORM" -t "$UPD_IMG" - >/dev/null
fi
docker run --rm --platform "$PLATFORM" --network none -i \
    -v "$KIT:/kit:ro" \
    -e GD_APPS="direwolf fldigi hamlib" \
    "$UPD_IMG" bash -s <<'OUTEREOF'
set -euo pipefail
KIT=/kit
STATE=/var/lib/grid-down/state.json
fail() { echo "FAIL: $*" >&2; exit 1; }
pycmp() { python3 - "$1" "$2" <<'PY'
import sys
same = open(sys.argv[1], "rb").read() == open(sys.argv[2], "rb").read()
sys.exit(0 if same else 1)
PY
}

# ---------- R: the restored kit really installs -------------------------------
# a REAL operator: the historic uid-11 "operator" system account (the setup
# package's /etc/passwd relic — home /root, nologin) is not one. gd_as_user
# would resolve HOME to /root and the delegation would die on permissions
# (M12 gate finding). Replace it, then create the real one.
if id operator >/dev/null 2>&1 && [ "$(id -u operator)" -lt 1000 ]; then
    userdel operator
fi
id operator >/dev/null 2>&1 || useradd -m operator
bash "$KIT/install/bootstrap.sh" --kit "$KIT" --user operator 2>&1 | tee /tmp/install.log
grep -qF 'install complete:' /tmp/install.log || fail "bootstrap didn't reach install complete"

# ---------- A: postinst delegation from the kit's control/ --------------------
id -nG operator | tr ' ' '\n' | grep -qx dialout || fail "operator is not in the dialout group"
for app in $GD_APPS; do
    grep -qF "postinst[$app/units]: rendering + installing grid-down-* user units for operator" \
        /tmp/install.log || fail "units step didn't delegate to the kit suite for $app"
done
[ -n "$(ls /home/operator/.config/systemd/user/grid-down-*.service 2>/dev/null)" ] \
    || fail "no grid-down-*.service units landed in the operator's user unit dir"
echo "install + postinst delegation: OK"

# ---------- N: re-run is a no-op ----------------------------------------------
cp "$STATE" /tmp/state.run1
bash "$KIT/install/bootstrap.sh" --kit "$KIT" --user operator 2>&1 | tee /tmp/rerun.log
grep -q 'is already installed' /tmp/rerun.log || fail "re-run: dnf did work"
for app in $GD_APPS; do
    grep -qF "postinst[$app/dialout]: already applied (re-run: skip)" /tmp/rerun.log \
        || fail "re-run: dialout watermark not honored for $app"
    grep -qF "postinst[$app/units]: already applied (re-run: skip)" /tmp/rerun.log \
        || fail "re-run: units watermark not honored for $app"
done
pycmp "$STATE" /tmp/state.run1 || fail "re-run: state.json changed"
grep -qF 'install complete:' /tmp/rerun.log || fail "re-run didn't reach install complete"
echo "no-op re-run: OK"
echo "POSTINST ASSERTIONS OK (restored kit)"
OUTEREOF

echo
echo "ROUNDTRIP OK — usb pack/unpack (M12 gate) — $(( $(date +%s) - T0 ))s total (arch=$ARCH rpmfusion=$RPMF_TXT)"
echo "restored kit: $KIT   usb layout: $USB  (record results in docs/audit/p4-usb-roundtrip.md)"