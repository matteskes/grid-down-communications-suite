#!/usr/bin/env bash
# Kit builder skeleton (Phase 3). Runs on the ONLINE side inside a fedora:45 container.
# Spec: docs/SPECIFICATION.md §5 — resolves recipes into a SHA-256-pinned offline kit.
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_IMAGE="${GD_BASE_IMAGE:-fedora:45}"
OUT="${GD_KIT_OUT:-$GD_ROOT/kit}"

printf 'grid-down kit builder (Phase 3 skeleton)\n'
printf '  base image : %s\n' "$BASE_IMAGE"
printf '  recipes    : %s\n' "$GD_ROOT/install/recipes"
printf '  output     : %s\n' "$OUT"
printf '\nNot implemented yet. Phase 3 will:\n'
printf '  1. resolve every recipe in install/recipes/*.yaml\n'
printf '  2. fail closed if VARA/VarAC artifacts are absent from install/kit-import/ (G6)\n'
printf '  3. dnf download --resolve all dnf-strategy packages (+ RPM Fusion)\n'
printf '  4. build source-strategy apps in-container; vend pip wheels; stage static bundles\n'
printf '  5. emit manifest.sha256 (path, sha256, url, version) and pack grid-down-kit-DATE.tar.zst\n'
exit 2
