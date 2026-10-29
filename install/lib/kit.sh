#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# install/lib/kit.sh — air-gap-side kit helpers (Phase 1).
# Sourced by bootstrap.sh; expects common.sh to already be sourced.
# Offline-install mechanics verified by tools/spike-dnf5-offline.sh (S3):
# NEVER --cacheonly (it reads stale system-cache metadata — air-gap leak);
# instead disable every configured repo and enable only the kit repo, with
# skip_if_unavailable=0 so a broken kit fails loudly instead of silently.

gd_kit_verify() { # $1 = kit dir — strict: every file hashed + no extras
    local kit="$1" tmp n_manifest n_files
    [ -f "$kit/manifest.sha256" ] || { gd_err "kit manifest missing: $kit/manifest.sha256"; return 1; }
    tmp=$(mktemp)
    awk -F'\t' 'NF >= 2 {print $1 "  " $2}' "$kit/manifest.sha256" > "$tmp"
    if ! (cd "$kit" && sha256sum -c --quiet "$tmp"); then
        rm -f "$tmp"
        gd_err "kit manifest verification FAILED (content mismatch)"
        return 1
    fi
    rm -f "$tmp"
    n_manifest=$(wc -l < "$kit/manifest.sha256")
    n_files=$(cd "$kit" && find . -type f ! -name manifest.sha256 | wc -l)
    if [ "$n_manifest" -ne "$n_files" ]; then
        gd_err "kit file-count mismatch: manifest=$n_manifest on-disk=$n_files"
        return 1
    fi
    gd_info "kit manifest verified: $n_manifest files"
}

gd_kit_repo_file() { printf '%s' "/etc/yum.repos.d/grid-down-kit.repo"; }

gd_kit_repo_setup() { # $1 = kit dir
    cat > "$(gd_kit_repo_file)" <<REPOEOF
# Written by grid-down bootstrap.sh — kit-local OFFLINE dnf repo.
# Trust model: integrity is enforced by kit/manifest.sha256 (sha256 of every
# file, verified before this repo is enabled), not by GPG — the kit is a
# self-contained trusted artifact (spec §5).
[gd-kit]
name=grid-down kit
baseurl=file://$1/repo
enabled=0
gpgcheck=0
priority=10
REPOEOF
}

gd_kit_install() { # offline install — only the kit repo is enabled
    dnf -y --disablerepo='*' --enablerepo=gd-kit \
        --setopt=gd-kit.skip_if_unavailable=0 \
        --nogpgcheck install "$@"
}

gd_kit_repo_cleanup() { rm -f "$(gd_kit_repo_file)"; }

# --- M6c: air-gap placement of build/tarball/pip/static payloads -------------
# The builder pre-renders kit/artifacts.tsv, kit/wheels.txt and kit/static.tsv
# so the air-gap side never parses YAML (station PyYAML is not guaranteed).

gd_kit_extract_artifacts() { # $1 = kit dir — bsdtar-extract pinned artifacts to their recipe dests
    local kit="$1" tsv="$kit/artifacts.tsv" id file dest fmt arch
    [ -f "$tsv" ] || return 0
    command -v bsdtar >/dev/null || { gd_err "bsdtar not found — install the kit dnf set first (it ships bsdtar)"; return 1; }
    while IFS=$'\t' read -r id file dest fmt arch; do
        [ -n "$id" ] || continue
        gd_info "extracting $id ($fmt, arch=$arch) -> $dest"
        mkdir -p "$dest"
        case "$fmt" in
            npm) bsdtar -xf "$kit/$file" -C "$dest" --strip-components=1 ;;
            *)   bsdtar -xf "$kit/$file" -C "$dest" ;;
        esac
    done < "$tsv"
}

gd_kit_install_wheels() { # $1 = kit dir — offline wheel install (no network, no repos)
    local kit="$1" tsv="$kit/wheels.txt" id pkg pybin
    [ -f "$tsv" ] || return 0
    command -v python3 >/dev/null || { gd_err "python3 not found — required for vendored wheels"; return 1; }
    while IFS=$'\t' read -r id pkg pybin; do
        [ -n "$id" ] || continue
        pybin="${pybin:-python3}"
        gd_info "installing wheels for $id ($pkg under $pybin)"
        if [ "$pybin" = "python3" ]; then
            # shellcheck disable=SC2086 — spec list is word-split by design
            python3 -m pip install --no-index --find-links "$kit/wheels" $pkg
        else
            # alternate python stack (e.g. meshtastic on python3.14): isolated venv,
            # pip comes from ensurepip (Fedora ships no <pyver>-pip package)
            command -v "$pybin" >/dev/null || { gd_err "$pybin not found — install the kit dnf set first"; return 1; }
            mkdir -p "/opt/grid-down/$id"
            "$pybin" -m venv "/opt/grid-down/$id/venv"
            # shellcheck disable=SC2086
            "/opt/grid-down/$id/venv/bin/pip" install --no-index --find-links "$kit/wheels" $pkg
        fi
    done < "$tsv"
}

gd_kit_place_static() { # $1 = kit dir — static payloads into their dest under /opt/grid-down
    local kit="$1" tsv="$kit/static.tsv" id rel serve
    [ -f "$tsv" ] || return 0
    while IFS=$'\t' read -r id rel serve; do
        [ -n "$id" ] || continue
        mkdir -p "/opt/grid-down/$id"
        cp -r "$kit/$rel"/. "/opt/grid-down/$id/" 2>/dev/null || true
        if [ -z "$(ls -A "/opt/grid-down/$id" 2>/dev/null)" ]; then
            gd_warn "$id: static payload empty — operator stages content per profile (Doctor WARNs)"
        fi
    done < "$tsv"
}