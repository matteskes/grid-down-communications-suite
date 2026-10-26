#!/usr/bin/env bash
# Fedora 45 package availability audit (run INSIDE a fedora:45 container).
# Runner: tools/run-audit.sh   Output: plain "AVAILABLE|MISSING <pkg>" lines.
# Spec: docs/SPECIFICATION.md §11 Phase 0.
set -uo pipefail

echo "# Fedora 45 package audit"
echo "image: ${AUDIT_IMAGE:-fedora:45}  date: $(date -u +%Y-%m-%dT%H:%M:%SZ)  container-arch: $(uname -m)"

dnf -q makecache --refresh >/dev/null 2>&1 || echo "WARN: makecache failed"

# RPM Fusion (needed for ffmpeg et al.)
if dnf -q -y install rpmfusion-free-release rpmfusion-nonfree-release >/dev/null 2>&1; then
    echo "rpmfusion: enabled"
else
    echo "rpmfusion: NOT enabled (ffmpeg/audacity strategy may change)"
fi

check() {
    local p
    for p in "$@"; do
        if dnf -q list available "$p" >/dev/null 2>&1; then
            echo "AVAILABLE  $p"
        else
            echo "MISSING    $p"
        fi
    done
}

echo "--- ham/digital group ---"
check direwolf hamlib hamlib-python python3-hamlib fldigi flarq flamp flmsg flwrap fllog flrig wsjtx qsstv

echo "--- reference/map/weather ---"
check kiwix-desktop xygrib navit

echo "--- av / utils ---"
check audacity sox conky cheese ffmpeg

echo "--- toolchain / runtime ---"
check rtl-sdr nodejs java-21-openjdk java-17-openjdk wine winetricks createrepo_c python3-pip php-cli

echo "--- expected-missing (tarball/build/wine strategies) ---"
check js8call pat meshtastic gridtracker

# --- Round 2: verify surprises with alternate names, globs, and official RPM Fusion release RPMs ---

echo "--- rpmfusion via official release rpms ---"
if dnf -q -y install \
    https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-45.noarch.rpm \
    https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-45.noarch.rpm >/dev/null 2>&1; then
    echo "rpmfusion: enabled (release rpms)"
    check ffmpeg
else
    echo "rpmfusion: FAILED to enable (ffmpeg must ship in kit as pinned build)"
fi

echo "--- alternate names for round-1 misses ---"
check wine-common wine-core java-openjdk java-21-openjdk-headless java-latest-openjdk snapshot obs-studio gnome-snapshot

echo "--- glob sweeps ---"
for g in 'wine' 'wine-*' 'java-2*openjdk*' 'flarq*' 'flwrap*' 'xygrib*' 'navit*' 'cheese*' 'snapshot*' 'meshtastic*'; do
    echo "glob $g:"
    dnf -q list available "$g" 2>/dev/null | tail -n +2 | head -6 || true
done

