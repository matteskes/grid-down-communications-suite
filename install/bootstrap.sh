#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Grid Down installer entry point (Phase 1: --kit mode is real; --online stays a stub).
# Spec: docs/SPECIFICATION.md §5.
#
#   bootstrap.sh --kit <dir>       # air-gap install from an offline kit
#   bootstrap.sh --kit <dir> --dry-run
#
# Exit codes: 0 ok · 1 verification/install failure · 2 usage
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root OR kit root
# shellcheck source=lib/common.sh
source "$GD_ROOT/install/lib/common.sh"
# shellcheck source=lib/kit.sh
source "$GD_ROOT/install/lib/kit.sh"

MODE=""
KIT_DIR=""
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --online)  MODE="online" ;;
        --kit)     MODE="kit"; KIT_DIR="${2:?--kit requires a directory}"; shift ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            printf 'usage: bootstrap.sh (--online | --kit DIR) [--dry-run]\n'
            exit 0 ;;
        *) gd_die "unknown argument: $1" ;;
    esac
    shift
done

[ -n "$MODE" ] || gd_die "choose --online or --kit DIR (see --help)"
gd_is_fedora || gd_die "not a Fedora system (/etc/fedora-release missing)"
. /etc/os-release
gd_info "Fedora ${VERSION_ID:-?} · mode=$MODE dry_run=$DRY_RUN"

if [ "$MODE" = "online" ]; then
    gd_warn "--online is a stub (kit-first design); build a kit with install/bundle-builder/build-kit.sh"
    exit 2
fi

# --- kit mode -----------------------------------------------------------------
KIT_DIR="$(cd "$KIT_DIR" && pwd)"
for need in manifest.sha256 apps.txt packages.txt install/bootstrap.sh repo/repodata; do
    [ -e "$KIT_DIR/$need" ] || gd_die "kit incomplete: missing $KIT_DIR/$need"
done
gd_kit_verify "$KIT_DIR"

APPS_KIT="$(tr '\n' ' ' < "$KIT_DIR/apps.txt")"
PACKAGES="$(tr '\n' ' ' < "$KIT_DIR/packages.txt")"
N_RPM="$(find "$KIT_DIR/repo" -maxdepth 1 -name '*.rpm' | wc -l | tr -d ' ')"

if [ "$DRY_RUN" = "1" ]; then
    echo "plan (dry run — nothing changed):"
    echo "  apps     : $APPS_KIT"
    echo "  packages : $(wc -w <<< "$PACKAGES") specs -> $(head -c 300 <<< "$PACKAGES")..."
    echo "  repo     : $N_RPM rpms at $KIT_DIR/repo (repo id gd-kit, gpgcheck=0, sha256-verified)"
    [ -f "$KIT_DIR/artifacts.tsv" ] && echo "  artifacts: $(($(wc -l < "$KIT_DIR/artifacts.tsv"))) pinned artifact(s) -> bsdtar extract"
    [ -f "$KIT_DIR/wheels.txt" ] && echo "  wheels   : $(find "$KIT_DIR/wheels" -name '*.whl' 2>/dev/null | wc -l | tr -d ' ') wheel(s) -> pip --no-index"
    [ -f "$KIT_DIR/static.tsv" ] && echo "  static   : $(($(wc -l < "$KIT_DIR/static.tsv"))) payload slot(s) -> /opt/grid-down"
    echo "  state    : $(gd_state_file)"
    exit 0
fi

gd_require_root

gd_kit_repo_setup "$KIT_DIR"
trap gd_kit_repo_cleanup EXIT

gd_info "offline install: ${#PACKAGES} words of package specs from kit repo only"
gd_kit_install $PACKAGES   # word-splitting intended: plain package specs

# Non-dnf payloads (phase4-plan M6c): pinned artifacts, vendored wheels,
# operator-staged static slots. All read the builder's pre-rendered TSVs.
gd_kit_extract_artifacts "$KIT_DIR"
gd_kit_install_wheels "$KIT_DIR"
gd_kit_place_static "$KIT_DIR"

gd_state_init
gd_state_set kit "$(basename "$KIT_DIR")"
gd_state_set installed_apps "$APPS_KIT"
gd_state_set installed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

for id in $APPS_KIT; do
    recipe="$KIT_DIR/install/recipes/$id.yaml"
    if ! grep -Eq '^  postinst: \[\]' "$recipe" 2>/dev/null; then
        gd_die "$id: recipe declares postinst hooks — not supported until P2+ (fail-closed)"
    fi
done
gd_info "postinst hooks: none declared (P1 scaffold OK)"

echo "install complete:"
echo "  apps     : $APPS_KIT"
echo "  kit      : $(basename "$KIT_DIR") ($(du -sh "$KIT_DIR" | cut -f1))"
echo "  next     : Control Center GUI + Doctor land in P2"

