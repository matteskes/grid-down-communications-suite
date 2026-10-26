#!/usr/bin/env bash
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