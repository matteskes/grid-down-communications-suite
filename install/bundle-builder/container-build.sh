#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Kit-build payload — runs INSIDE a fedora:45 container with network access.
# Invoked by build-kit.sh (host side); emits a kit staging tree and optionally packs it.
# Layout (spec §6.8):
#   kit/repo/            merged local dnf repo (fedora + rpmfusion rpms + repodata)
#   kit/install/         bootstrap.sh + lib/ + recipes/ (the air-gap installer snapshot)
#   kit/apps.txt         selected app ids (one per line)
#   kit/packages.txt     resolved package specs for offline dnf install (one per line)
#   kit/manifest.sha256  TSV: sha256, relpath, source, version
# Inputs via env (set by build-kit.sh):
#   GD_IN_OUT (staging dir), GD_IN_ARCH (x86_64|aarch64), GD_IN_RPMFUSION (0|1),
#   GD_IN_PACK (0|1), GD_IN_APPS (ids), GD_IN_PKGS_FEDORA, GD_IN_PKGS_RPMFUSION,
#   /install-src (repo install/ mounted ro), /kits (pack output dir)
# Spike-verified dnf5 flags: tools/spike-dnf5-offline.sh (S1: --resolve --alldeps is a
# complete closure; S2: createrepo_c must be installed explicitly; S3: never --cacheonly).
set -euo pipefail

OUT="${GD_IN_OUT:?GD_IN_OUT required}"
ARCH="${GD_IN_ARCH:-x86_64}"
RPMFUSION="${GD_IN_RPMFUSION:-0}"
PACK="${GD_IN_PACK:-1}"
APPS="${GD_IN_APPS:-}"
PKGS_FEDORA="${GD_IN_PKGS_FEDORA:-}"
PKGS_RPMFUSION="${GD_IN_PKGS_RPMFUSION:-}"
RPFFREE_SHA="a120c2a2c70ec2c8ad461908aacf316998b03f6b2156c143b772052972e80a95"
RPFNONFREE_SHA="0cb381899827136c92be41a068e6177982bda36ce594729c0bf6f4fa485a15a9"

log() { printf '[kit-build] %s\n' "$*" >&2; }
FORCEARCH=()
if [ "$ARCH" = "aarch64" ]; then FORCEARCH=(--forcearch=aarch64); fi

log "dnf $(dnf --version | head -1) · target-arch=$ARCH · rpmfusion=$RPMFUSION · pack=$PACK"
dnf -q makecache >/dev/null
log "installing createrepo_c (not in base image — spike S2)"
if ! dnf -y install createrepo_c >/dev/null; then
    log "FATAL: could not install createrepo_c"; exit 1
fi
command -v createrepo_c >/dev/null || { log "FATAL: createrepo_c unavailable"; exit 1; }

if [ "$RPMFUSION" = "1" ]; then
    log "enabling RPM Fusion via pinned release RPMs (sha256-verified; docs/audit/f45-package-audit.md)"
    curl -fsSL -o /tmp/rf-free.rpm  https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-45.noarch.rpm
    curl -fsSL -o /tmp/rf-nonfree.rpm https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-45.noarch.rpm
    printf '%s  %s\n%s  %s\n' "$RPFFREE_SHA" /tmp/rf-free.rpm "$RPFNONFREE_SHA" /tmp/rf-nonfree.rpm \
        | sha256sum -c --quiet -
    dnf -y install /tmp/rf-free.rpm /tmp/rf-nonfree.rpm >/dev/null
    log "rpmfusion: $(rpm -q rpmfusion-free-release rpmfusion-nonfree-release | tr '\n' ' ')"
fi

rm -rf "$OUT" 2>/dev/null || true          # no-op when $OUT is a bind-mount point
find "$OUT" -mindepth 1 -delete 2>/dev/null || true
mkdir -p "$OUT/repo"

# Base-refresh set: the kit resolves against repo-HEAD, but the air-gap target may
# run an older snapshot whose dnf stack pins older library sonames (observed: target
# libdnf5 needed libxml2.so.2 while repo-HEAD libxkbcommon needs .so.16 -> solver
# deadlock). Carrying the current dnf stack + its libs keeps the kit internally
# consistent AND lets older targets upgrade into the kit's snapshot in one
# transaction. Round-trip: docs/audit/phase1-roundtrip.md.
BASE_REFRESH="dnf dnf5 libdnf5 librepo libsolv libxml2"

dl() { # dl <label> <pkgs...> — full closure download (spike S1: --alldeps includes base rpms)
    local label="$1"; shift
    [ $# -gt 0 ] || return 0
    log "downloading closure [$label]: $*"
    dnf -q download --resolve --alldeps "${FORCEARCH[@]}" --destdir "$OUT/repo" "$@"
}
dl "fedora" $BASE_REFRESH $PKGS_FEDORA
dl "rpmfusion" $PKGS_RPMFUSION

N_RPM=$(find "$OUT/repo" -maxdepth 1 -name '*.rpm' | wc -l)
[ "$N_RPM" -gt 0 ] || { log "FATAL: no rpms downloaded"; exit 1; }
createrepo_c "$OUT/repo" >/dev/null
log "repo: $N_RPM rpms + repodata"

mkdir -p "$OUT/install"
cp -r /install-src/bootstrap.sh /install-src/lib /install-src/recipes "$OUT/install/"
printf '%s\n' $APPS | grep -v '^$' | sort > "$OUT/apps.txt"
printf '%s\n' $PKGS_FEDORA $PKGS_RPMFUSION | grep -v '^$' | sort -u > "$OUT/packages.txt"

# manifest.sha256 — TSV sha256 <TAB> relpath <TAB> source <TAB> version
( cd "$OUT" && find . -type f ! -name manifest.sha256 -print0 | sort -z | \
while IFS= read -r -d '' f; do
    p="${f#./}"
    h=$(sha256sum "$p" | cut -d' ' -f1)
    src="grid-down repo (install/ snapshot)"; ver="-"
    case "$p" in
        repo/*.rpm)
            b="${p##*/}"; ver="${b%.rpm}"; ver="${ver%.*}"
            case "$b" in
                rpmfusion-*-release-45.noarch.rpm) src="mirrors.rpmfusion.org (pinned release rpm)" ;;
                *) src="fedora:45 dnf download --resolve --alldeps" ;;
            esac ;;
    esac
    printf '%s\t%s\t%s\t%s\n' "$h" "$p" "$src" "$ver"
done ) > "$OUT/manifest.sha256"
log "manifest: $(wc -l < "$OUT/manifest.sha256") files hashed"

if [ "$PACK" = "1" ]; then
    command -v zstd >/dev/null 2>&1 || dnf -y install zstd >/dev/null
    KITNAME="grid-down-kit-$(date -u +%Y%m%d)-$ARCH"
    tar --zstd -cf "/kits/$KITNAME.tar.zst" -C "$(dirname "$OUT")" "$(basename "$OUT")"
    log "packed: /kits/$KITNAME.tar.zst ($(du -h "/kits/$KITNAME.tar.zst" | cut -f1))"
fi
log "done — staging tree: $OUT"