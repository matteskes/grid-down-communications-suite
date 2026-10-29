#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Build-deps audit (M6a pre-pass) — fedora:45, linux/amd64.
#
# The P0 audit (audit-f45-packages.sh) verified runtime packages only. This
# round verifies every candidate build dependency of the 9 build-strategy
# recipes, plus the extraction tools (bsdtar / cpio / unzip) the kit
# installer needs station-side (deb-or-rpm fallback policy, phase4-plan M6a).
#
# Run from the host (Docker Desktop up):
#   bash tools/run-build-deps-audit.sh
# or directly:
#   docker run --rm --platform linux/amd64 \
#       -v "$PWD/tools:/tools:ro" fedora:45 bash /tools/audit-f45-build-deps.sh
# Paste the output into docs/audit/f45-package-audit.md (M6a section).
#
# NOTE (audit lesson, round 1): on Apple Silicon, pin --platform linux/amd64 —
# the target station is x86_64 and repo answers differ per arch.
set -uo pipefail   # NOT -e: a MISSING package must not stop the sweep

if [ "$(uname -m)" != "x86_64" ]; then
    echo "FATAL: container arch is $(uname -m), audit requires x86_64" >&2
    exit 1
fi

image_tag="$(rpm -q fedora-release --qf '%{VERSION}' 2>/dev/null || echo '?')"
echo "image: fedora:${image_tag} (linux/amd64)  date: $(date -u +%Y-%m-%dT%H:%M:%SZ)  container-arch: $(uname -m)"

dnf -q makecache >/dev/null 2>&1 || dnf makecache >/dev/null
echo "dnf cache primed"
echo "--- base toolchain ---"
for p in gcc gcc-c++ make cmake patch git-core; do
    dnf -q list available "$p" >/dev/null 2>&1 && echo "AVAILABLE $p" || echo "MISSING $p"
done
echo "--- qt5 (wsjtx-improved, qttermtcp, xygrib) ---"
for p in qt5-qtbase-devel qt5-qtmultimedia-devel qt5-qtserialport-devel qt5-qttools-devel; do
    dnf -q list available "$p" >/dev/null 2>&1 && echo "AVAILABLE $p" || echo "MISSING $p"
done
echo "--- audio (ardopcf) ---"
for p in alsa-lib-devel portaudio-devel pulseaudio-libs-devel; do
    dnf -q list available "$p" >/dev/null 2>&1 && echo "AVAILABLE $p" || echo "MISSING $p"
done
echo "--- dsp/ham (wsjtx-improved, sdrpp) ---"
for p in fftw-devel hamlib-devel boost-devel asciidoc glfw-devel glew-devel libusb-devel volk-devel; do
    dnf -q list available "$p" >/dev/null 2>&1 && echo "AVAILABLE $p" || echo "MISSING $p"
done
echo "--- sdr (dump1090) ---"
for p in rtl-sdr-devel librtlsdr-devel; do
    dnf -q list available "$p" >/dev/null 2>&1 && echo "AVAILABLE $p" || echo "MISSING $p"
done
echo "--- fltk (flwrap) ---"
for p in fltk-devel fltk-fluid libsamplerate-devel; do
    dnf -q list available "$p" >/dev/null 2>&1 && echo "AVAILABLE $p" || echo "MISSING $p"
done
echo "--- imaging/geo (xygrib, osm-navit) ---"
for p in libpng-devel openjpeg2-devel zlib-devel bzip2-devel proj-devel glib2-devel gtk3-devel gettext-devel libcurl-devel sqlite-devel fontconfig-devel freetype-devel ncurses-devel; do
    dnf -q list available "$p" >/dev/null 2>&1 && echo "AVAILABLE $p" || echo "MISSING $p"
done
echo "--- station-side extraction (kit installer fallbacks) ---"
for p in libarchive-tools cpio unzip rpm-build; do
    dnf -q list available "$p" >/dev/null 2>&1 && echo "AVAILABLE $p" || echo "MISSING $p"
done
echo "--- glob sweeps (near-miss names) ---"
for g in 'rtl*sdr*' '*openjpeg*' '*volk*' '*fltk*' '*portaudio*' '*glfw*'; do
    echo "glob $g:"
    dnf -q list available "$g" 2>/dev/null | sed -n '2,$p' || echo "            (empty)"
done
