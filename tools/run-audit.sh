#!/usr/bin/env bash
# Run the F45 package audit in a local container (host side).
# Usage: bash tools/run-audit.sh [> docs/audit/f45-package-audit.md]
# Tries fedora:45, then fedora:beta, then fedora:latest (labels output with actual image).
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

IMAGE=""
for candidate in fedora:45 fedora:beta fedora:latest; do
    if docker image inspect "$candidate" >/dev/null 2>&1 \
       || docker pull -q "$candidate" >/dev/null 2>&1; then
        IMAGE="$candidate"
        break
    fi
done
[ -n "$IMAGE" ] || { echo "ERROR: no usable fedora image found" >&2; exit 1; }
echo "audit image: $IMAGE" >&2

docker run --rm \
    --platform linux/amd64 \
    -e AUDIT_IMAGE="$IMAGE (linux/amd64)" \
    -v "$GD_ROOT/tools/audit-f45-packages.sh:/audit.sh:ro" \
    "$IMAGE" bash /audit.sh
