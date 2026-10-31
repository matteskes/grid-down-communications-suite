#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Grid Down installer entry point (Phase 1: --kit mode is real; --online stays a stub).
# Spec: docs/SPECIFICATION.md §5.
#
#   bootstrap.sh --kit <dir>       # air-gap install from an offline kit
#   bootstrap.sh --kit <dir> --user <name|uid>   # operator for postinst steps
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
GD_POSTINST_USER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --online)  MODE="online" ;;
        --kit)     MODE="kit"; KIT_DIR="${2:?--kit requires a directory}"; shift ;;
        --user)    GD_POSTINST_USER="${2:?--user requires a name or uid}"; shift ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            printf 'usage: bootstrap.sh (--online | --kit DIR) [--user NAME|UID] [--dry-run]\n'
            exit 0 ;;
        *) gd_die "unknown argument: $1" ;;
    esac
    shift
done
export GD_POSTINST_USER

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
for need in ARCH manifest.sha256 apps.txt packages.txt install/bootstrap.sh repo/repodata; do
    [ -e "$KIT_DIR/$need" ] || gd_die "kit incomplete: missing $KIT_DIR/$need"
done
gd_kit_verify "$KIT_DIR"
KIT_ARCH="$(gd_kit_arch_check "$KIT_DIR")"   # M11c: refuses a mismatch before anything mutates

APPS_KIT="$(tr '\n' ' ' < "$KIT_DIR/apps.txt")"
PACKAGES="$(tr '\n' ' ' < "$KIT_DIR/packages.txt")"
N_RPM="$(find "$KIT_DIR/repo" -maxdepth 1 -name '*.rpm' | wc -l | tr -d ' ')"
N_MANIFEST="$(wc -l < "$KIT_DIR/manifest.sha256" | tr -d ' ')"

if [ "$DRY_RUN" = "1" ]; then
    echo "plan (dry run — nothing changed):"
    echo "  apps     : $APPS_KIT"
    echo "  arch     : kit=$KIT_ARCH host=$(uname -m)"
    echo "  packages : $(wc -w <<< "$PACKAGES") specs -> $(head -c 300 <<< "$PACKAGES")..."
    echo "  repo     : $N_RPM rpms at $KIT_DIR/repo (repo id gd-kit, gpgcheck=0, sha256-verified)"
    [ -f "$KIT_DIR/artifacts.tsv" ] && echo "  artifacts: $(($(wc -l < "$KIT_DIR/artifacts.tsv"))) pinned artifact(s) -> bsdtar extract"
    [ -d "$KIT_DIR/opt" ] && echo "  build    : $(find "$KIT_DIR/opt" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ') build payload(s) -> /opt/grid-down"
    [ -f "$KIT_DIR/wheels.txt" ] && echo "  wheels   : $(find "$KIT_DIR/wheels" -name '*.whl' 2>/dev/null | wc -l | tr -d ' ') wheel(s) -> pip --no-index"
    [ -f "$KIT_DIR/static.tsv" ] && echo "  static   : $(($(wc -l < "$KIT_DIR/static.tsv"))) payload slot(s) -> /opt/grid-down"
    [ -f "$KIT_DIR/postinst.tsv" ] && echo "  postinst : $(($(wc -l < "$KIT_DIR/postinst.tsv"))) app row(s) of declarative steps -> gd_kit_postinst${GD_POSTINST_USER:+ (operator: $GD_POSTINST_USER)}"
    echo "  state    : $(gd_state_file)"
    exit 0
fi

gd_require_root

# Install phases (M11c): the phase name rides $PHASE so a hard failure's ERR
# trap stops loud saying where it stopped, and a re-run resumes — dnf is
# transactional, payload placement is content-idempotent, and applied postinst
# steps are watermark-skipped. State lands BEFORE the phases so a crash
# mid-install still says what was being installed.
PHASE="setup"
trap 'gd_err "install FAILED in phase: $PHASE — fix the cause above and re-run bootstrap.sh --kit (it resumes)"' ERR

PHASE="dnf (kit repo only)"
gd_kit_repo_setup "$KIT_DIR"
trap gd_kit_repo_cleanup EXIT

gd_info "offline install: ${#PACKAGES} words of package specs from kit repo only"
gd_kit_install $PACKAGES   # word-splitting intended: plain package specs

# Kit identity lands in state right after dnf (M11c): the state helpers shell
# out to python3, which the kit dnf set itself ships — this is the first point
# the writes are guaranteed to work (a bare fedora:45 container has no python3
# and the writes would only warn). A crash before this point is a dnf
# transaction that rolled back; the re-run redoes it cleanly.
PHASE="state"
PREV_KIT="$(gd_state_get kit)"
gd_state_init
gd_state_set kit "$(basename "$KIT_DIR")"
gd_state_set kit_arch "$KIT_ARCH"
gd_state_set kit_manifest_lines "$N_MANIFEST"
gd_state_set installed_apps "$APPS_KIT"
# installed_at freezes across no-op re-runs of the same kit (a re-run leaves
# state.json byte-identical — the M11d idempotency gate asserts it); a rebuilt
# kit carries a new name (grid-down-kit-<date>-<arch>) and refreshes it.
if [ "$PREV_KIT" != "$(basename "$KIT_DIR")" ] || [ -z "$(gd_state_get installed_at)" ]; then
    gd_state_set installed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

# Non-dnf payloads (phase4-plan M6c): pinned artifacts, vendored wheels,
# operator-staged static slots. All read the builder's pre-rendered TSVs.
PHASE="payload placement"
gd_kit_extract_artifacts "$KIT_DIR"
gd_kit_place_opt "$KIT_DIR"
gd_kit_install_wheels "$KIT_DIR"
gd_kit_place_static "$KIT_DIR"

# Declarative postinst (M11a + M11c watermarks, spec §5): payload placement
# already succeeded, so state says what's on disk; every step runs under
# postinst_done — exit 0 marks it (re-runs skip), a self-skip leaves it
# unmarked (re-run applies after the fix), a failure dies naming app/step.
PHASE="postinst"
gd_kit_postinst "$KIT_DIR"

trap - ERR
echo "install complete:"
echo "  apps     : $APPS_KIT"
echo "  kit      : $(basename "$KIT_DIR") ($(du -sh "$KIT_DIR" | cut -f1))"
echo "  next     : gd doctor (first run) · gd units install refreshes user units"

