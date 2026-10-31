#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Offline round-trip gate: P1 kit + P5 postinst (M11d). Spec §11 P1 + §5 postinst.
#   1. build a real sha256-pinned kit into $GD_ROOT/kit-staging (no tarball);
#   2. augment it with the M11d postinst probe — a udev rule file + a direwolf
#      postinst.tsv row edit + manifest re-hash (the M6c edit-re-hash discipline
#      as a POSITIVE test: gd_kit_verify must still pass). No shipped recipe
#      declares a udev rule yet (install/udev/ is a README), so the probe proves
#      gd_postinst_udev placement with real kit plumbing; it retires when a real
#      rule lands at recipe-pin/live-E2E time;
#   3. run ONE --network none container that installs with an operator
#      (bootstrap.sh --user), then asserts:
#        R  install + P1 core (rpms, binaries, ldd clean)
#        A  postinst effects: dialout gid, udev rule 0644 + content, units
#           DELEGATION (M12: the kit packs control/ — the suite renders + installs
#           the unit set headless, daemon-reload soft-skips), applied steps
#           watermarked, state kit_manifest_lines == on-disk manifest
#        G  wine-prefix arch gate: uname-shimmed aarch64 must DIE naming
#           x86_64 (a die, not a skip) without touching state.json
#        N  idempotency: second bootstrap is a no-op — dnf reports installed
#           packages, watermarks skip, self-skips re-attempt, and state.json
#           is byte-identical (python3 compare — f45 ships no cmp/diff)
#   4. gate line: "ROUNDTRIP OK — incl. postinst".
# Usage: tools/roundtrip-dnf.sh [--with-rpmfusion] [--with-wine] [--arch x86_64|aarch64]
#   --with-wine adds varac to the app set (wine dnf closure + the ~1 GB shared
#   prefix ball; M11b-proven). aarch64 is a dnf-only subset — combining it with
#   --with-wine is refused by the resolver, as designed.
# Heavy: this downloads the full package closure once (dnf inside the builder
# container), then installs it again from the kit. Budget a few minutes.
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${GD_BASE_IMAGE:-fedora:45}"
ARCH="x86_64"; RPMF_FLAG=""; RPMF_TXT="no"; WINE="0"; WINE_TXT="no"
while [ $# -gt 0 ]; do
    case "$1" in
        --with-rpmfusion) RPMF_FLAG="--with-rpmfusion"; RPMF_TXT="yes"; shift ;;
        --with-wine)      WINE="1"; WINE_TXT="yes"; shift ;;
        --arch)           ARCH="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done
case "$ARCH" in x86_64|aarch64) ;; *) echo "bad --arch: $ARCH" >&2; exit 2 ;; esac

# The install run must match the kit's arch (the M11c arch guard refuses the
# alternative before anything mutates). Map kit ARCH -> docker --platform.
case "$ARCH" in
    x86_64)  PLATFORM="linux/amd64" ;;
    aarch64) PLATFORM="linux/arm64" ;;
esac

T0=$(date +%s)
echo "== [1/3] build kit (online side) =="
APPS="direwolf,fldigi,hamlib"
if [ -n "$RPMF_FLAG" ]; then APPS="direwolf,fldigi,hamlib,ffmpeg"; fi   # rpmfusion implies an rpmfusion app
if [ "$WINE" = "1" ]; then APPS="$APPS,varac"; fi                       # the M11d wine leg
"$GD_ROOT/install/bundle-builder/build-kit.sh" --arch "$ARCH" --no-pack --apps "$APPS" \
    ${RPMF_FLAG:+"$RPMF_FLAG"}

KIT="$GD_ROOT/kit-staging"

echo "== [2/3] M11d postinst probe: manifest-consistent udev augmentation =="
# Write a real rule, flip direwolf's udev column onto it, then re-hash the
# manifest exactly as container-build.sh does (TSV sha256 \t path \t source \t
# version, find|sort -z order) — the builder's strict verifier must accept the
# edited kit. The staging tree is scratch, discarded after this run.
RULE_REL="install/udev/99-grid-down-probe.rules"
[ -f "$KIT/postinst.tsv" ] || { echo "FATAL: kit has no postinst.tsv" >&2; exit 1; }
mkdir -p "$KIT/install/udev"
cat > "$KIT/$RULE_REL" <<'RULEEOF'
# round-trip probe rule (tools/roundtrip-dnf.sh, M11d) — proves gd_postinst_udev
# places 0644 rules under /etc/udev/rules.d with intact content. No shipped
# recipe declares a udev rule yet; retires when a real one lands.
ACTION=="add|change", SUBSYSTEM=="tty", ATTRS{idVendor}=="0gd0", ATTRS{idProduct}=="11d0", MODE="0660", GROUP="dialout"
RULEEOF
awk -F'\t' 'BEGIN{OFS="\t"} $1=="direwolf"{$3="'"$RULE_REL"'"} {print}' \
    "$KIT/postinst.tsv" > "$KIT/postinst.tsv.new"
awk -F'\t' -v want="$RULE_REL" '$1=="direwolf" && $3==want' "$KIT/postinst.tsv.new" \
    | grep -q direwolf || { echo "FATAL: direwolf row not flipped in postinst.tsv" >&2; exit 1; }
mv "$KIT/postinst.tsv.new" "$KIT/postinst.tsv"
( cd "$KIT" && find . -type f ! -name manifest.sha256 -print0 | sort -z |
while IFS= read -r -d '' f; do
    p="${f#./}"; h=$(sha256sum "$p" | cut -d' ' -f1)
    case "$p" in
        repo/*.rpm)          src="fedora:45 dnf download --resolve --alldeps" ;;
        wine-prefix.tar.zst) src="shared wine prefix built in fedora:45 (kit wine dnf set, wineboot -i)" ;;
        ARCH)                src="kit target arch (bootstrap arch guard, M11c)" ;;
        wine/*)              src="operator-staged kit-import installer (G6 — rides the kit, runs at the operator's station)" ;;
        control/*)           src="grid-down repo (control/ suite — postinst units/wine-install delegation, M12)" ;;
        profiles/*)          src="grid-down repo (station profile template — profile_path() repo fallback, M12)" ;;
        "$RULE_REL")         src="round-trip postinst probe (tools/roundtrip-dnf.sh, M11d)" ;;
        *)                   src="grid-down repo (install/ snapshot)" ;;
    esac
    printf '%s\t%s\t%s\t%s\n' "$h" "$p" "$src" "-"
done ) > "$KIT/manifest.sha256"
echo "  probe: direwolf/udev -> $RULE_REL ($(wc -l < "$KIT/manifest.sha256") manifest rows)"

# Watermark expectations for stage A (id/step tokens in state.postinst_done):
# applied steps are marked. Since M12 the kit packs control/, so EVERY
# declarative step applies — units and wine-install delegate to the kit's
# suite; nothing self-skips in this harness (MARK_ABSENT is empty).
MARK_APPLY="direwolf/dialout fldigi/dialout hamlib/dialout direwolf/udev direwolf/units fldigi/units hamlib/units"
MARK_ABSENT=""
if [ "$WINE" = "1" ]; then
    MARK_APPLY="$MARK_APPLY varac/dialout varac/wine-prefix varac/wine-install"
fi

# Install-side image: updated to repo-HEAD so it represents the spec's target —
# "a Fedora 45 machine patched up to the kit's snapshot date, then air-gapped".
# (A stale base pins old library sonames the kit cannot satisfy; see
# docs/audit/phase1-roundtrip.md. In real ops: `dnf -y update` BEFORE air-gapping.)
UPD_IMG="${GD_INSTALL_IMAGE:-gd-f45-current:latest}"
if ! docker image inspect "$UPD_IMG" >/dev/null 2>&1; then
    echo "== building updated install image: $UPD_IMG (one-time) =="
    printf 'FROM %s\nRUN dnf -y update\n' "$IMAGE" \
        | docker build --platform "$PLATFORM" -t "$UPD_IMG" - >/dev/null
fi

echo
echo "== [3/3] air-gap install + postinst gate (--network none, $PLATFORM) =="
# bash -s reads this script on stdin: postinst lines (gd_log) go to stderr,
# dnf and bootstrap's summary to stdout — 2>&1 captures both, pipefail keeps
# bootstrap's exit honest through tee.
docker run --rm --platform "$PLATFORM" --network none -i \
    -v "$KIT:/kit:ro" \
    -e GD_APPS="direwolf fldigi hamlib" \
    -e GD_WINE="$WINE" \
    -e GD_MARK_APPLY="$MARK_APPLY" \
    -e GD_MARK_ABSENT="$MARK_ABSENT" \
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

# ---------- R: air-gap install (operator identity via --user) -----------------
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

# ---------- A: postinst effects -----------------------------------------------
grep -qF 'postinst: operator=operator (units/dialout/wine steps apply for them)' \
    /tmp/install.log || fail "bootstrap didn't see the operator"
for app in $GD_APPS; do
    # the first app's step adds the operator; the rest see the membership already
    grep -qF "postinst[$app/dialout]: adding operator to dialout" /tmp/install.log \
        || grep -qF "postinst[$app/dialout]: operator already in dialout" /tmp/install.log \
        || fail "dialout step didn't apply for $app"
done
grep -qF 'postinst[direwolf/dialout]: adding operator to dialout' /tmp/install.log \
    || fail "dialout: the adding step never ran (direwolf is the TSV's first row)"
id -nG operator | tr ' ' '\n' | grep -qx dialout \
    || fail "operator is not in the dialout group"
grep -qF 'postinst[direwolf/udev]: installing 99-grid-down-probe.rules -> /etc/udev/rules.d/99-grid-down-probe.rules' \
    /tmp/install.log || fail "udev step didn't apply the probe rule"
[ -f /etc/udev/rules.d/99-grid-down-probe.rules ] \
    || fail "probe rule missing from /etc/udev/rules.d"
[ "$(stat -c %a /etc/udev/rules.d/99-grid-down-probe.rules)" = 644 ] \
    || fail "probe rule is not 0644"
pycmp /etc/udev/rules.d/99-grid-down-probe.rules "$KIT/install/udev/99-grid-down-probe.rules" \
    || fail "placed rule content differs from the kit copy"
for app in $GD_APPS; do
    # M12: the kit packs control/ — the step delegates to the kit's suite and
    # the unit set renders + installs for the operator
    grep -qF "postinst[$app/units]: rendering + installing grid-down-* user units for operator" \
        /tmp/install.log || fail "units step didn't delegate to the kit suite for $app"
done
grep -Eq '^services: +[0-9]+ installed$' /tmp/install.log \
    || fail "units install didn't report installed services"
grep -qF 'daemon-reload: skipped (no systemd user session)' /tmp/install.log \
    || fail "units install didn't soft-skip the headless daemon-reload"
[ -n "$(ls /home/operator/.config/systemd/user/grid-down-*.service 2>/dev/null)" ] \
    || fail "no grid-down-*.service units landed in the operator's user unit dir"
python3 - "$STATE" "$GD_MARK_APPLY" "$GD_MARK_ABSENT" <<'PY' || fail "postinst watermark state wrong"
import json, sys
d = json.load(open(sys.argv[1]))
m = set(d.get("postinst_done", []))
need, forb = set(sys.argv[2].split()), set(sys.argv[3].split())
miss, bad = sorted(need - m), sorted(forb & m)
if miss or bad:
    print("FAIL missing=%s unexpected=%s" % (miss, bad)); sys.exit(1)
PY
ml_state=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("kit_manifest_lines"))' "$STATE")
ml_disk=$(wc -l < "$KIT/manifest.sha256")
[ "$ml_state" = "$ml_disk" ] \
    || fail "kit_manifest_lines=$ml_state vs on-disk manifest rows=$ml_disk"

# ---------- W: wine leg (--with-wine) -----------------------------------------
if [ "$GD_WINE" = "1" ]; then
    [ -d /opt/grid-down/wine-prefix/drive_c ] || fail "wine prefix drive_c missing"
    [ -f /opt/grid-down/wine-prefix/.update-timestamp ] \
        || fail "wine prefix not bootstrapped (.update-timestamp missing)"
    [ "$(stat -c %u /opt/grid-down/wine-prefix)" = "$(id -u operator)" ] \
        || fail "wine prefix not chowned to the operator"
    # M12: the step delegates to the kit's suite — the silent kit-import
    # installer runs for the operator via gd winehub, and winehub only reports
    # "installed" after the recipe's exe candidates really exist under drive_c
    grep -qF 'postinst[varac/wine-install]: silent kit-import installer for varac (operator: operator)' \
        /tmp/install.log || fail "wine-install didn't delegate to the kit suite"
    grep -qF 'install: varac  installed' /tmp/install.log \
        || fail "winehub install didn't report varac installed"
fi

# ---------- P1 core: the kit really installed ---------------------------------
rpm -q direwolf fldigi hamlib python3-hamlib || fail "kit rpms missing"
command -v direwolf >/dev/null || fail "/usr/bin/direwolf missing"
command -v fldigi >/dev/null || fail "fldigi binary missing"
if ldd "$(command -v direwolf)" 2>/dev/null | grep -qi "not found"; then
    fail "direwolf has unresolved libraries"
fi
echo "install + postinst effects: OK"
# ---------- G: wine-prefix arch gate — loud DIE, not a silent skip ------------
mkdir -p /tmp/shim
printf '#!/bin/sh\n[ "$1" = -m ] && { echo aarch64; exit 0; }\nexec /usr/bin/uname "$@"\n' \
    > /tmp/shim/uname
chmod +x /tmp/shim/uname
cp "$STATE" /tmp/state.pre-archgate
set +e
( PATH=/tmp/shim:$PATH bash -c \
    '. /kit/install/lib/common.sh; . /kit/install/lib/kit.sh; gd_postinst_wine_prefix operator /kit direwolf' ) \
    > /tmp/archgate.log 2>&1
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "arch-gate probe exited 0 (must die loud)"
grep -qF 'wine prefix requires x86_64 (this host: aarch64)' /tmp/archgate.log \
    || fail "arch-gate die didn't name the x86_64 requirement"
pycmp "$STATE" /tmp/state.pre-archgate \
    || fail "arch-gate probe touched the install state"
echo "arch-gate die probe: OK"

# ---------- N: idempotency — the second run is a no-op ------------------------
cp "$STATE" /tmp/state.run1
bash "$KIT/install/bootstrap.sh" --kit "$KIT" --user operator 2>&1 | tee /tmp/rerun.log
grep -q 'is already installed' /tmp/rerun.log \
    || fail "re-run: dnf did work (kit install is not idempotent)"
for app in $GD_APPS; do
    grep -qF "postinst[$app/dialout]: already applied (re-run: skip)" /tmp/rerun.log \
        || fail "re-run: dialout watermark not honored for $app"
done
grep -qF 'postinst[direwolf/udev]: already applied (re-run: skip)' /tmp/rerun.log \
    || fail "re-run: udev watermark not honored"
for app in $GD_APPS; do
    grep -qF "postinst[$app/units]: already applied (re-run: skip)" /tmp/rerun.log \
        || fail "re-run: units watermark not honored for $app"
done
if [ "$GD_WINE" = "1" ]; then
    grep -qF 'postinst[varac/wine-prefix]: already applied (re-run: skip)' /tmp/rerun.log \
        || fail "re-run: wine prefix watermark not honored"
    grep -qF 'postinst[varac/wine-install]: already applied (re-run: skip)' /tmp/rerun.log \
        || fail "re-run: wine-install watermark not honored"
fi
pycmp "$STATE" /tmp/state.run1 \
    || fail "re-run: state.json changed (idempotency broken)"
grep -qF 'install complete:' /tmp/rerun.log || fail "re-run didn't reach install complete"
echo "no-op re-run: OK"

echo "POSTINST ASSERTIONS OK"
OUTEREOF

echo
echo "ROUNDTRIP OK — incl. postinst (M11d gate) — $(( $(date +%s) - T0 ))s total (arch=$ARCH rpmfusion=$RPMF_TXT wine=$WINE_TXT)"
echo "kit staging tree: $KIT  (record results in docs/audit/phase1-roundtrip.md)"