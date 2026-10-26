#!/usr/bin/env bash
# tools/spike-dnf5-offline.sh — Phase 1 spike: verify dnf5 offline-kit flags inside fedora:45.
# Answers (recorded in docs/audit/phase1-roundtrip.md):
#   S1 dnf flavor on F45; does `dnf download --resolve --alldeps` produce a complete
#      closure (i.e. does it include base-system-installed rpms like glibc)?
#   S2 createrepo_c available?
#   S3 offline resolve via repo-file + --cacheonly --disablerepo='*' --enablerepo=<id>
#   S4 --forcearch=aarch64 download works from an amd64 container?
# Usage: tools/spike-dnf5-offline.sh [platform]   (default: linux/amd64)
set -euo pipefail

if [ "${GD_SPIKE_INNER:-0}" = "1" ]; then
    # ---------------- inner: runs inside fedora:45 ----------------
    echo "S1 dnf flavor: $(dnf --version | head -1)"
    dnf -q makecache --refresh >/dev/null 2>&1 && echo "S1 makecache: ok"

    dnf -q list installed dnf5-plugins createrepo_c >/dev/null 2>&1 \
        || dnf -q -y install dnf5-plugins createrepo_c >/dev/null 2>&1
    echo "S2 createrepo_c: $(command -v createrepo_c || echo MISSING)"

    rm -rf /spike && mkdir -p /spike/all /spike/min /spike/arm

    echo "S1 download --resolve --alldeps (direwolf fldigi hamlib):"
    dnf -q download --resolve --alldeps --destdir /spike/all direwolf fldigi hamlib 2>&1 | tail -2 || true
    echo "S1 rpm count (--alldeps): $(ls /spike/all/*.rpm 2>/dev/null | wc -l)"
    echo "S1 glibc closure check: $(ls /spike/all/glibc-*.rpm 2>/dev/null | head -1 | xargs -r basename || echo 'NOT-DOWNLOADED')"

    dnf -q download --resolve --destdir /spike/min direwolf fldigi hamlib 2>&1 | tail -2 || true
    echo "S1 rpm count (resolve only): $(ls /spike/min/*.rpm 2>/dev/null | wc -l)"

    dnf -q --forcearch=aarch64 download --resolve --alldeps --destdir /spike/arm direwolf 2>&1 | tail -2 || true
    echo "S4 aarch64 rpms: $(ls /spike/arm/*.rpm 2>/dev/null | head -3 | xargs -r -n1 basename | tr '\n' ' ')"

    createrepo_c -q /spike/all && echo "S2 createrepo: ok"

    printf '%s\n' '[gd-kit]' 'name=grid-down kit' 'baseurl=file:///spike/all' \
        'enabled=0' 'gpgcheck=0' 'priority=10' > /etc/yum.repos.d/gd-kit.repo
    echo "S3 offline resolve (--cacheonly + repo-file, --assumeno):"
    dnf -q --cacheonly --disablerepo='*' --enablerepo=gd-kit install --assumeno direwolf fldigi hamlib 2>&1 | tail -4 || true
    echo "S3 done (real offline install is exercised by tools/roundtrip-dnf.sh)"
    exit 0
fi

# ---------------- outer: host orchestration ----------------
PLATFORM="${1:-linux/amd64}"
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
exec docker run --rm --platform "$PLATFORM" \
    -e GD_SPIKE_INNER=1 \
    -v "$SCRIPT:/gd-spike.sh:ro" \
    fedora:45 bash /gd-spike.sh
