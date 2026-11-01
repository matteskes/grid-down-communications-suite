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

gd_kit_arch_check() { # $1 = kit dir; prints the kit arch. M11c arch guard:
    # the kit records its target arch (kit/ARCH, one line, manifest-hashed)
    # and this refuses a mismatch BEFORE anything mutates — the P1 finding #1
    # failure mode used to die inside the dnf solver with a confusing
    # dependency error instead of naming the fix.
    local kit="$1" want have
    [ -f "$kit/ARCH" ] || gd_die "kit/ARCH missing — pre-M11c kit; rebuild it (build-kit.sh)"
    want=$(tr -d '[:space:]' < "$kit/ARCH")
    have=$(uname -m)
    case "$want" in
        x86_64|aarch64) ;;
        *) gd_die "kit/ARCH holds '$want' — expected x86_64 or aarch64; rebuild the kit (build-kit.sh --arch <arch>)" ;;
    esac
    [ "$want" = "$have" ] \
        || gd_die "kit arch mismatch: this host is $have, the kit targets $want — rebuild with build-kit.sh --arch $have"
    printf '%s' "$want"
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
    # --no-xattrs: upstream tarballs may carry xattrs (pat's binary does) that a
    # container fs can't restore ("Unknown error -1") — restoring them is not
    # part of the payload contract; exit code must reflect real extraction
    # errors only. M10 container E2E.
    # split local: `$kit` in the same declaration expands from the CALLER's
    # scope (empty under bootstrap's set -u, wrong paths otherwise) — M10 E2E.
    local kit="$1"
    local tsv="$kit/artifacts.tsv" id file dest fmt arch wrk src
    [ -f "$tsv" ] || return 0
    command -v bsdtar >/dev/null || { gd_err "bsdtar not found — install the kit dnf set first (it ships bsdtar)"; return 1; }
    while IFS=$'\t' read -r id file dest fmt arch; do
        [ -n "$id" ] || continue
        gd_info "extracting $id ($fmt, arch=$arch) -> $dest"
        mkdir -p "$dest"
        case "$fmt" in
            npm) bsdtar --no-xattrs -xf "$kit/$file" -C "$dest" --strip-components=1 ;;
            deb|rpm) bsdtar --no-xattrs -xf "$kit/$file" -C "$dest" ;;
            *)  # Release tarballs/zips usually wrap everything in one top dir;
                # flatten it so the recipe dest IS the payload root — registry
                # cmd/docroot paths assume that (M10: js8spotter served 404s
                # from the wrapped dir; gridtracker/pat wrapped, yaac not).
                # M13 drill finding: the dest is replaced WHOLESALE — the
                # artifact is sha-pinned, so its content is the truth, and
                # mv(1) cannot overwrite a non-empty dir on a re-run (G6:
                # re-runs are no-ops; gridtracker's locales/ died there).
                wrk="$(mktemp -d "$(dirname "$dest")/.$(basename "$dest").extract.XXXXXX")"
                bsdtar --no-xattrs -xf "$kit/$file" -C "$wrk"
                set -- "$wrk"/* "$wrk"/.[!.]*
                if [ $# -eq 2 ] && [ -d "$1" ] && [ "$2" = "$wrk"/.[!.]* ]; then
                    set -- "$1"
                fi
                if [ $# -eq 1 ] && [ -d "$1" ]; then
                    src="$1"
                else
                    src="$wrk"
                fi
                rm -rf "$dest"
                mkdir -p "$dest"
                ( shopt -s dotglob; mv "$src"/* "$dest"/ )
                rm -rf "$wrk"
                ;;
        esac
    done < "$tsv"
}

gd_kit_install_wheels() { # $1 = kit dir — offline wheel install (no network, no repos)
    local kit="$1"
    local tsv="$kit/wheels.txt" id pkg pybin
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

gd_kit_place_opt() { # $1 = kit dir — build-strategy payloads into /opt/grid-down
    # kit/opt/<id>/ mirrors the recipes' artifact dests under /opt/grid-down/<id>/
    # (container-build.sh collects build artifacts there). No TSV needed: the
    # payload set is whatever build apps the plan selected. Manifest-verified.
    local kit="$1" id dir
    [ -d "$kit/opt" ] || return 0
    for dir in "$kit"/opt/*; do
        [ -d "$dir" ] || continue
        id="$(basename "$dir")"
        gd_info "placing build payload $id -> /opt/grid-down/$id"
        mkdir -p "/opt/grid-down/$id"
        cp -a "$dir/." "/opt/grid-down/$id/"
    done
}

gd_kit_place_static() { # $1 = kit dir — static payloads into their dest under /opt/grid-down
    local kit="$1"
    local tsv="$kit/static.tsv" id rel serve
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

# --- M11a: declarative postinst (spec §5) ------------------------------------
# The builder pre-renders kit/postinst.tsv (id, units, udev, dialout, prefix,
# install) from the recipes' postinst: steps — the air-gap side never parses
# YAML. Per app, steps apply in the canonical order dialout -> udev -> units
# -> wine-prefix -> wine-install, each under the postinst_done watermark
# (M11c): applied steps are marked and skipped on re-runs, self-skipped steps
# stay unmarked (a fixed condition + re-run applies them), and failures die
# loud naming the step — so fixing + re-running bootstrap resumes.

gd_as_user() { # gd_as_user <name-or-uid> <cmd...> — run as the operator, root-side
    # with the operator's HOME (units render into their user dirs; wine must
    # never run as root). runuser (util-linux) — no sudo dependency.
    local who="$1"; shift
    local home
    home=$(getent passwd "$who" | cut -d: -f6)
    [ -n "$home" ] || { gd_err "gd_as_user: no passwd entry for $who"; return 1; }
    runuser -u "$who" -- env HOME="$home" "$@"
}

gd_postinst_operator() { # the operator: bootstrap's --user wins, then SUDO_UID
    if [ -n "${GD_POSTINST_USER:-}" ]; then printf '%s' "$GD_POSTINST_USER"; return 0; fi
    if [ -n "${SUDO_UID:-}" ]; then printf '%s' "$SUDO_UID"; return 0; fi
    return 1
}

gd_postinst_dialout() { # $1 = operator ("" = none known) $2 = app id
    # Convention (M11c): return 0 = applied (the caller watermarks it);
    # return non-zero = skipped itself with the follow-up printed (no
    # watermark — a fixed condition + re-run applies it); gd_die = loud stop.
    local op="$1" id="$2"
    if ! getent group dialout >/dev/null; then
        gd_warn "postinst[$id/dialout]: dialout group missing — create it (groupadd --system dialout) and re-run bootstrap"
        return 1
    fi
    if [ -z "$op" ]; then
        gd_warn "postinst[$id/dialout]: no operator identity — skipped (re-run with --user, or as root: usermod -aG dialout <operator>)"
        return 1
    fi
    if id -nG "$op" 2>/dev/null | tr ' ' '\n' | grep -qx dialout; then
        gd_info "postinst[$id/dialout]: $op already in dialout"
        return 0
    fi
    gd_info "postinst[$id/dialout]: adding $op to dialout"
    usermod -aG dialout "$op" || gd_die "postinst[$id/dialout]: usermod failed"
}

gd_postinst_udev() { # $1 = kit dir $2 = kit-relative rule path $3 = app id
    # split local: `$rel` in the same declaration expands from the CALLER's
    # scope (unbound under set -u) — the M10 E2E lesson, met again here.
    local kit="$1" rel="$2" id="$3"
    local dest="/etc/udev/rules.d/$(basename "$rel")"
    gd_info "postinst[$id/udev]: installing $(basename "$rel") -> $dest"
    mkdir -p /etc/udev/rules.d
    install -m 0644 "$kit/$rel" "$dest" || gd_die "postinst[$id/udev]: rule install failed"
    # Best-effort reload: containers and minimal hosts may lack the daemon —
    # rules still apply at next boot and Doctor re-checks drift.
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload 2>/dev/null \
            || gd_warn "postinst[$id/udev]: udevadm reload unavailable — rules apply at next boot"
    fi
}

gd_postinst_units() { # $1 = operator ("" = none known) $2 = app id — suite delegation
    # The unit SET's content lives in the control suite (M12 packs control/
    # into the kit); delegating keeps render byte-identical with what Doctor
    # diffs (units_current), instead of reimplementing it in bash.
    local op="$1" id="$2" gd="$GD_ROOT/control/cli/gd.py"
    if [ ! -f "$gd" ]; then
        gd_warn "postinst[$id/units]: control suite missing from this kit (M12+ kits pack control/) — run as the operator: gd units install"
        return 1
    fi
    if [ -z "$op" ]; then
        gd_warn "postinst[$id/units]: no operator identity — skipped (re-run with --user, or as the operator: gd units install)"
        return 1
    fi
    gd_info "postinst[$id/units]: rendering + installing grid-down-* user units for $op"
    gd_as_user "$op" python3 "$gd" units install \
        || gd_die "postinst[$id/units]: gd units install failed (unresolved presets are named in its output)"
}

gd_postinst_wine_prefix() { # $1 = operator $2 = kit dir $3 = app id
    local op="$1" kit="$2" id="$3"
    [ "$(uname -m)" = "x86_64" ] \
        || gd_die "postinst[$id/wine-prefix]: wine prefix requires x86_64 (this host: $(uname -m)) — VARA/wine is x86_64-only upstream"
    local dest="${GRID_DOWN_WINE_PREFIX:-/opt/grid-down/wine-prefix}"
    if [ -d "$dest/drive_c" ]; then
        gd_info "postinst[$id/wine-prefix]: prefix already present at $dest (skip)"
        return 0
    fi
    local ball="$kit/wine-prefix.tar.zst"
    [ -f "$ball" ] \
        || gd_die "postinst[$id/wine-prefix]: cached prefix tarball missing ($ball) — rebuild the kit with wine-prefix packing (M11b)"
    gd_info "postinst[$id/wine-prefix]: unpacking cached prefix -> $dest"
    mkdir -p "$dest"
    # The cached ball carries a top-level "wine-prefix/" component (the builder
    # packs the prefix dir itself) — strip it so the contents land AT $dest;
    # a plain -C left them one level deep and the re-run guard never matched
    # (found by tools/roundtrip-dnf.sh --with-wine, M11d).
    bsdtar -xf "$ball" -C "$dest" --strip-components=1 \
        || gd_die "postinst[$id/wine-prefix]: prefix unpack failed"
    if [ -n "$op" ]; then
        chown -R "$(getent passwd "$op" | cut -d: -f3):$(getent passwd "$op" | cut -d: -f4)" "$dest" \
            || gd_die "postinst[$id/wine-prefix]: prefix chown to $op failed"
    fi
}

gd_postinst_wine_install() { # $1 = operator $2 = app id — suite-run silent installer
    # G6: kit-import installers, never kit-built; the installer file, mode and
    # flags live in the recipe's wine: block, read only by the suite
    # (gd winehub install) — the executor never parses them.
    local op="$1" id="$2" gd="$GD_ROOT/control/cli/gd.py"
    if [ ! -f "$gd" ]; then
        gd_warn "postinst[$id/wine-install]: control suite missing from this kit (M12+ kits pack control/) — run as the operator: gd winehub install $id"
        return 1
    fi
    if [ -z "$op" ]; then
        gd_warn "postinst[$id/wine-install]: no operator identity — skipped (wine never runs as root; re-run with --user)"
        return 1
    fi
    gd_info "postinst[$id/wine-install]: silent kit-import installer for $id (operator: $op)"
    gd_as_user "$op" python3 "$gd" winehub install "$id" \
        || gd_die "postinst[$id/wine-install]: $id installer failed (see its output above)"
}

gd_postinst_step() { # gd_postinst_step <id/step> <fn> [args...] — one declarative
    # step under the postinst_done watermark (M11c). fn exit 0 = applied
    # (watermarked; re-runs skip it); any other exit = the step skipped itself
    # with its follow-up printed (no watermark — a fixed condition + re-run
    # applies it); a gd_die inside fn stops the install loud, step named.
    local tag="$1"; shift
    if gd_state_marked postinst_done "$tag"; then
        gd_info "postinst[$tag]: already applied (re-run: skip)"
        return 0
    fi
    if "$@"; then
        gd_state_mark postinst_done "$tag"
    else
        gd_info "postinst[$tag]: skipped this run — re-run bootstrap to apply it"
    fi
}

gd_kit_postinst() { # $1 = kit dir — apply every selected app's declarative steps
    local kit="$1"
    local tsv="$kit/postinst.tsv"
    local id units udev dialout prefix install op=""
    if op=$(gd_postinst_operator); then
        gd_info "postinst: operator=$op (units/dialout/wine steps apply for them)"
    else
        gd_warn "postinst: no operator identity (--user or sudo) — operator steps will note their follow-up"
    fi
    if [ ! -f "$tsv" ]; then
        gd_info "postinst: no steps declared by the selected apps"
        return 0
    fi
    # Columns (builder-normalized, never empty — tab-read collapses empties):
    # id, units, udev, dialout, prefix, install — 1 = step, - = absent,
    # udev carries the kit-relative rule path when present.
    while IFS=$'\t' read -r id units udev dialout prefix install; do
        [ -n "$id" ] || continue
        if [ "$dialout" = "1" ]; then gd_postinst_step "$id/dialout" gd_postinst_dialout "$op" "$id"; fi
        if [ "$udev" != "-" ]; then gd_postinst_step "$id/udev" gd_postinst_udev "$kit" "$udev" "$id"; fi
        if [ "$units" = "1" ]; then gd_postinst_step "$id/units" gd_postinst_units "$op" "$id"; fi
        if [ "$prefix" = "1" ]; then gd_postinst_step "$id/wine-prefix" gd_postinst_wine_prefix "$op" "$kit" "$id"; fi
        if [ "$install" = "1" ]; then gd_postinst_step "$id/wine-install" gd_postinst_wine_install "$op" "$id"; fi
    done < "$tsv"
    gd_info "postinst: declarative steps applied ($(grep -c . "$tsv" 2>/dev/null || echo 0) app row(s), $(gd_state_get postinst_done | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0) step(s) watermarked)"
}