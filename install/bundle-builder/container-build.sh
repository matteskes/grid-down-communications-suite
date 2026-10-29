#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Kit-build payload — runs INSIDE a fedora:45 container with network access.
# Invoked by build-kit.sh (host side); emits a kit staging tree and optionally packs it.
# Layout (spec §6.8 + phase4-plan M6c):
#   kit/repo/            merged local dnf repo (fedora + rpmfusion rpms + repodata)
#   kit/install/         bootstrap.sh + lib/ + recipes/ (the air-gap installer snapshot)
#   kit/opt/<id>/        build-strategy payloads (compiled in this container, per recipe)
#   kit/tarball/<id>/    pinned upstream artifacts (verified here; extracted air-gap side)
#   kit/wheels/          pinned pip wheels (same ABI as the F45 station)
#   kit/static/<id>/     operator-staged payloads (region ride-along; Doctor WARNs if empty)
#   kit/apps.txt         selected app ids (one per line)
#   kit/packages.txt     resolved package specs for offline dnf install (one per line)
#   kit/artifacts.tsv    id -> (file, dest, format, arch) for the air-gap extractor
#   kit/wheels.txt       id -> pip spec for the air-gap wheel install
#   kit/manifest.sha256  TSV: sha256, relpath, source, version
# Inputs via env (set by build-kit.sh):
#   GD_IN_OUT (staging dir), GD_IN_ARCH (x86_64|aarch64), GD_IN_RPMFUSION (0|1),
#   GD_IN_PACK (0|1), GD_IN_PLAN (JSON plan from resolve-kit.py),
#   /install-src (repo install/ mounted ro), /kits (pack output dir)
# Spike-verified dnf5 flags: tools/spike-dnf5-offline.sh (S1: --resolve --alldeps is a
# complete closure; S2: createrepo_c must be installed explicitly; S3: never --cacheonly).
set -euo pipefail

OUT="${GD_IN_OUT:?GD_IN_OUT required}"
ARCH="${GD_IN_ARCH:-x86_64}"
RPMFUSION="${GD_IN_RPMFUSION:-0}"
PACK="${GD_IN_PACK:-1}"
PLAN="${GD_IN_PLAN:?GD_IN_PLAN required}"
RPFFREE_SHA="a120c2a2c70ec2c8ad461908aacf316998b03f6b2156c143b772052972e80a95"
RPFNONFREE_SHA="0cb381899827136c92be41a068e6177982bda36ce594729c0bf6f4fa485a15a9"
BUILD_ROOT=/gd-build           # per-app build scratch (extract + compile)

log() { printf '[kit-build] %s\n' "$*" >&2; }
FORCEARCH=()
if [ "$ARCH" = "aarch64" ]; then FORCEARCH=(--forcearch=aarch64); fi

log "dnf $(dnf --version | head -1) | target-arch=$ARCH rpmfusion=$RPMFUSION pack=$PACK"
dnf -q makecache >/dev/null
# updates-testing rides the default repo set on a freshly GA'd Fedora; keep it
# explicit so named build deps (openjpeg-devel on F45 at pin time) resolve.
dnf config-manager setopt "updates-testing.enabled=1" >/dev/null 2>&1 \
    || dnf config-manager --set-enabled updates-testing >/dev/null 2>&1 || true
log "installing build/kit prerequisites (createrepo_c = spike S2; python3-pip for the"
log "  wheels phase = fedora:45 base has no python3; bsdtar = shipped for air-gap extraction)"
dnf -y install createrepo_c python3-pip bsdtar zstd >/dev/null
command -v createrepo_c >/dev/null || { log "FATAL: createrepo_c unavailable"; exit 1; }
command -v python3 >/dev/null  || { log "FATAL: python3 unavailable"; exit 1; }

# plan_rows <strategy> <field...> - emit the plan's rows as \x1f-joined lines
# (\x1f is NOT bash IFS-whitespace, so EMPTY fields keep their positions;
# tab-separated `read` would collapse consecutive tabs and shift columns).
plan_rows() {
    GD_IN_PLAN="$PLAN" python3 - "$@" <<'PYEOF'
import json, os, sys
plan = json.loads(os.environ["GD_IN_PLAN"])
fields = sys.argv[2:]
for row in plan.get(sys.argv[1], []):
    cells = []
    for f in fields:
        v = row.get(f, "") or ""
        if isinstance(v, list):
            v = " ".join(str(x) for x in v)   # lists: space-joined (no tabs/newlines)
        cells.append(str(v))
    print("\x1f".join(cells))
PYEOF
}

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
mkdir -p "$OUT/repo" "$BUILD_ROOT"

# Base-refresh set: the kit resolves against repo-HEAD, but the air-gap target may
# run an older snapshot whose dnf stack pins older library sonames (observed: target
# libdnf5 needed libxml2.so.2 while repo-HEAD libxkbcommon needs .so.16 -> solver
# deadlock). Carrying the current dnf stack + its libs keeps the kit internally
# consistent AND lets older targets upgrade into the kit's snapshot in one
# transaction. Round-trip: docs/audit/phase1-roundtrip.md. bsdtar rides along so the
# air-gap side can extract tar.gz/rpm/deb/zip artifacts (phase4-plan M6c).
BASE_REFRESH="dnf dnf5 libdnf5 librepo libsolv libxml2 bsdtar"

dl() { # dl <label> <pkgs...> — full closure download (spike S1: --alldeps includes base rpms)
    local label="$1"; shift
    [ $# -gt 0 ] || return 0
    log "downloading closure [$label]: $*"
    dnf -q download --resolve --alldeps "${FORCEARCH[@]}" --destdir "$OUT/repo" "$@"
}
plan_pkgs() { # plan_pkgs <pkgs_fedora|pkgs_rpmfusion> — a plan download list, space-joined
    GD_IN_PLAN="$PLAN" python3 -c 'import json,os,sys; print(" ".join(json.loads(os.environ["GD_IN_PLAN"])[sys.argv[1]]))' "$1"
}
dl "fedora" $BASE_REFRESH $(plan_pkgs pkgs_fedora)
dl "rpmfusion" $(plan_pkgs pkgs_rpmfusion)

N_RPM=$(find "$OUT/repo" -maxdepth 1 -name '*.rpm' | wc -l)
[ "$N_RPM" -gt 0 ] || { log "FATAL: no rpms downloaded"; exit 1; }
createrepo_c "$OUT/repo" >/dev/null
log "repo: $N_RPM rpms + repodata"

# --- build-strategy phase: fetch pinned source, verify, compile, collect artifacts ---
ORIGINS="$BUILD_ROOT/origins.txt"; : > "$ORIGINS"
while IFS=$'\x1f' read -r id git tag archive_url sha256 version build_cmd artifacts build_deps; do
    [ -n "$id" ] || continue
    log "build[$id]: preparing ($version)"
    work="$BUILD_ROOT/$id"; mkdir -p "$work/src"
    if [ -n "$archive_url" ]; then
        url="$archive_url"; origin="$archive_url"
    else
        repo_path="${git#https://github.com/}"; repo_path="${repo_path%.git}"
        url="https://codeload.github.com/${repo_path}/tar.gz/${tag}"
        origin="${git}@${tag}"
    fi
    curl -fsSL -o "$work/archive" "$url"
    printf '%s  %s\n' "$sha256" "$work/archive" | sha256sum -c --quiet -
    # GNU tar under Rosetta (amd64 emulation on Apple Silicon) fails on statx
    # (ENOSYS — same family as the audit's fexecve quirk); python tarfile uses
    # classic syscalls. Extract, then flatten: most upstream archives carry a
    # single top-level directory; a flat archive moves as-is.
    mkdir -p "$work/ext" "$work/src"
    python3 -m tarfile -e "$work/archive" "$work/ext"
    set -- "$work/ext"/*
    if [ $# -eq 1 ] && [ -d "$1" ]; then
        ( shopt -s dotglob; mv "$1"/* "$work/src"/ )
    else
        ( shopt -s dotglob; mv "$work/ext"/* "$work/src"/ )
    fi
    rmdir "$work/ext" 2>/dev/null || true
    if [ -n "$build_deps" ]; then
        log "build[$id]: installing build deps: $build_deps"
        # shellcheck disable=SC2086 — word splitting is the package list
        dnf -y install $build_deps >/dev/null
    fi
    log "build[$id]: building"
    ( cd "$work/src" && bash -c "$build_cmd" ) >&2
    log "build[$id]: collecting artifacts: $artifacts"
    read -ra ARTS <<< "$artifacts"          # whitespace split; entries hold no spaces
    for art in "${ARTS[@]}"; do
        src_rel="${art%%:*}"; dest_rel="${art#*:}"
        [ -n "$src_rel" ] && [ -n "$dest_rel" ] || { log "FATAL: bad artifacts entry '$art' for $id"; exit 1; }
        mkdir -p "$OUT/opt/$id/$(dirname "$dest_rel")"
        set -- "$work/src"/$src_rel            # unquoted glob: versioned names (paracon_*.pyz)
        [ -e "$1" ] || { log "FATAL: build[$id]: artifact '$src_rel' not found"; exit 1; }
        cp -r "$@" "$OUT/opt/$id/$dest_rel"
    done
    printf '%s\t%s\t%s\n' "$id" "$origin" "$version" >> "$ORIGINS"
done < <(plan_rows build id git tag archive_url sha256 version build_cmd artifacts build_deps)

# --- tarball-strategy phase: fetch pinned upstream artifacts, verify ---
: > "$OUT/artifacts.tsv"
while IFS=$'\x1f' read -r id url sha256 dest arch format version arm64_url arm64_sha256; do
    [ -n "$id" ] || continue
    pick_url="$url"; pick_sha="$sha256"
    if [ "$ARCH" = "aarch64" ] && [ -n "$arm64_url" ]; then
        pick_url="$arm64_url"; pick_sha="$arm64_sha256"
    fi
    fname="${pick_url##*/}"; fname="${fname%%\?*}"
    log "tarball[$id]: fetching $pick_url"
    mkdir -p "$OUT/tarball/$id"
    curl -fsSL -o "$OUT/tarball/$id/$fname" "$pick_url"
    printf '%s  %s\n' "$pick_sha" "$OUT/tarball/$id/$fname" | sha256sum -c --quiet -
    printf '%s\t%s\t%s\t%s\t%s\n' "$id" "tarball/$id/$fname" "$dest" "$format" "$arch" >> "$OUT/artifacts.tsv"
    printf '%s\t%s\t%s\n' "$id" "$pick_url" "$version" >> "$BUILD_ROOT/tarball-origins.txt"
done < <(plan_rows tarball id url sha256 dest arch format version arm64_url arm64_sha256)

# --- pip-vendored phase: wheels for the STATION python named in the recipe ---
# meshtastic caps Requires-Python <3.15 while F45 ships 3.15, so its recipe
# names python3.14 (Fedora alternate stack; venv+ensurepip provide pip).
while IFS=$'\x1f' read -r id package pybin; do
    [ -n "$id" ] || continue
    log "pip[$id]: downloading $package (for $pybin)"
    mkdir -p "$OUT/wheels"
    if [ "$pybin" = "python3" ]; then
        python3 -m pip download -q -d "$OUT/wheels" "$package"
    else
        dnf -y install "$pybin" >/dev/null
        venv="$BUILD_ROOT/venv-$id"
        "$pybin" -m venv "$venv"
        "$venv/bin/pip" download -q -d "$OUT/wheels" "$package"
    fi
    printf '%s\t%s\t%s\n' "$id" "$package" "$pybin" >> "$OUT/wheels.txt"
done < <(plan_rows pip id package python)
[ -f "$OUT/wheels.txt" ] && [ -s "$OUT/wheels.txt" ] && \
    log "wheels: $(find "$OUT/wheels" -name '*.whl' | wc -l) files for $(wc -l < "$OUT/wheels.txt") apps"

# --- static-strategy phase: operator-staged payload slots (region ride-along) ---
: > "$OUT/static.tsv"
while IFS=$'\x1f' read -r id src serve; do
    [ -n "$id" ] || continue
    mkdir -p "$OUT/static/$id"
    printf '%s\t%s\t%s\n' "$id" "static/$id" "$serve" >> "$OUT/static.tsv"
done < <(plan_rows static id src serve)

# --- air-gap installer snapshot + selection lists ---
mkdir -p "$OUT/install"
cp -r /install-src/bootstrap.sh /install-src/lib /install-src/recipes "$OUT/install/"
plan_rows_apps() {
    GD_IN_PLAN="$PLAN" python3 -c 'import json,os; print("\n".join(json.loads(os.environ["GD_IN_PLAN"])["apps"]))'
}
plan_rows_apps | sort > "$OUT/apps.txt"
{ plan_pkgs pkgs_fedora; plan_pkgs pkgs_rpmfusion; } | tr ' ' '\n' | grep -v '^$' | sort -u > "$OUT/packages.txt"

# --- manifest.sha256 — TSV sha256 <TAB> relpath <TAB> source <TAB> version ---
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
        opt/*)
            id="${p#opt/}"; id="${id%%/*}"
            origin=$(awk -F'\t' -v i="$id" '$1==i {print $2}' "$BUILD_ROOT/origins.txt")
            ver=$(awk -F'\t' -v i="$id" '$1==i {print $3}' "$BUILD_ROOT/origins.txt")
            src="built in fedora:45 from ${origin:-?}" ;;
        tarball/*)
            id="${p#tarball/}"; id="${id%%/*}"
            src=$(awk -F'\t' -v i="$id" '$1==i {print $2}' "$BUILD_ROOT/tarball-origins.txt")
            ver=$(awk -F'\t' -v i="$id" '$1==i {print $3}' "$BUILD_ROOT/tarball-origins.txt")
            src="upstream artifact: ${src:-?}" ;;
        wheels/*)
            b="${p##*/}"; src="pypi (pip download in fedora:45)"
            ver="$(printf '%s' "$b" | sed -E 's/^[^-]+-([^-]+)-.*/\1/')" ;;
        static/*) src="operator-staged payload (region ride-along)"; ver="-" ;;
    esac
    printf '%s\t%s\t%s\t%s\n' "$h" "$p" "$src" "$ver"
done ) > "$OUT/manifest.sha256"
log "manifest: $(wc -l < "$OUT/manifest.sha256") files hashed"

if [ "$PACK" = "1" ]; then
    command -v zstd >/dev/null 2>&1 || dnf -y install zstd >/dev/null
    KITNAME="grid-down-kit-$(date -u +%Y%m%d)-$ARCH"
    # python tarfile (Rosetta-safe, see build phase) writes plain tar, zstd compresses.
    python3 - "$OUT" "/kits/$KITNAME.tar" <<'PYEOF'
import os, sys, tarfile
out, dest = sys.argv[1], sys.argv[2]
with tarfile.open(dest, "w") as tf:
    for root, dirs, files in os.walk(out):
        for name in sorted(dirs + files):
            p = os.path.join(root, name)
            tf.add(p, arcname=os.path.join("kit-staging", os.path.relpath(p, out)))
PYEOF
    zstd -q -f "/kits/$KITNAME.tar" -o "/kits/$KITNAME.tar.zst" && rm -f "/kits/$KITNAME.tar"
    log "packed: /kits/$KITNAME.tar.zst ($(du -h "/kits/$KITNAME.tar.zst" | cut -f1))"
fi
log "done — staging tree: $OUT"
