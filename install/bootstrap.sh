#!/usr/bin/env bash
# Grid Down installer entry point (Phase 0 skeleton — no-op, exits 2).
# Spec: docs/SPECIFICATION.md §5.
#
#   bootstrap.sh --online          # online-side prep (rare; prefer kit build)
#   bootstrap.sh --kit <dir>       # air-gap install from an offline kit
#   bootstrap.sh --dry-run ...     # show plan without changing the system
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/common.sh
source "$GD_ROOT/install/lib/common.sh"

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
gd_info "Fedora ${VERSION_ID:-?} detected; mode=${MODE} dry_run=${DRY_RUN}"

if [ "$MODE" = "kit" ]; then
    [ -d "$KIT_DIR" ] || gd_die "kit directory not found: $KIT_DIR"
    [ -f "$KIT_DIR/manifest.sha256" ] || gd_die "kit manifest missing: $KIT_DIR/manifest.sha256"
    gd_info "kit manifest found — verification lands in Phase 1"
fi

gd_warn "Phase 0 skeleton: no installation performed (exit 2)"
exit 2
