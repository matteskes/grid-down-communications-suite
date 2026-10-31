#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Pack a built kit for FAT32 transport (M12, spec §6.9 P4): one plain tar of the
# kit staging tree, split into FAT32-safe parts, with a sidecar sha256 manifest
# over the USB layout itself. No compression: the kit's payload is already
# compressed (rpms, tar.zst artifacts, zstd prefix), and plain tar + split keeps
# this tool dependency-free (python3 tarfile + POSIX split — Rosetta-safe, same
# discipline as the builder's pack phase).
#
# USB layout written to --out:
#   <name>.tar.part-0000 …    fixed-size parts of the kit tar (FAT32: <= 4 GiB/file;
#                             default 3800 MiB leaves headroom for allocation quirks)
#   usb-manifest.sha256       TSV sha256 <TAB> relpath — over the parts, verified by
#                             unpack-usb-kit.sh before reassembly
# The kit's OWN manifest.sha256 rides INSIDE the tar (the installer re-verifies it
# air-gap side); the sidecar pins the transport bytes.
# Usage: tools/make-usb-kit.sh [--from DIR] [--out DIR] [--name NAME]
#                              [--part-mib N] [--force]
#   --from DIR     built kit staging tree (default $GD_ROOT/kit-staging)
#   --out DIR      USB layout dir (default $GD_ROOT/usb-kit); must be empty
#                  unless --force wipes it
#   --name NAME    layout basename (default gd-kit-<UTC date>-<kit arch>)
#   --part-mib N   part size in MiB (default 3800)
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FROM="$GD_ROOT/kit-staging"; OUT="$GD_ROOT/usb-kit"; NAME=""; PART_MIB=3800; FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --from)     FROM="$2"; shift 2 ;;
        --out)      OUT="$2"; shift 2 ;;
        --name)     NAME="$2"; shift 2 ;;
        --part-mib) PART_MIB="$2"; shift 2 ;;
        --force)    FORCE=1; shift ;;
        -h|--help)  sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)          echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
    esac
done
case "$PART_MIB" in ''|*[!0-9]*) echo "bad --part-mib: $PART_MIB" >&2; exit 2 ;; esac
[ "$PART_MIB" -lt 4096 ] || { echo "FATAL: --part-mib must stay under FAT32's 4096" >&2; exit 2; }

# macOS ships shasum(1); Linux/coreutils sha256sum(1) — accept either
hash_file() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

[ -f "$FROM/manifest.sha256" ] || { echo "FATAL: $FROM has no manifest.sha256 (build first: install/bundle-builder/build-kit.sh)" >&2; exit 1; }
[ -f "$FROM/ARCH" ] || { echo "FATAL: $FROM has no ARCH (pre-M11c kit layout?)" >&2; exit 1; }
[ -n "$NAME" ] || NAME="gd-kit-$(date -u +%Y%m%d)-$(cat "$FROM/ARCH")"

if [ -d "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
    [ "$FORCE" = "1" ] || { echo "FATAL: $OUT is not empty (use --force to wipe it)" >&2; exit 1; }
    rm -rf "$OUT"
fi
mkdir -p "$OUT"

echo "grid-down USB packer"
printf '  from     : %s (%s files, %s)\n' "$FROM" \
    "$(find "$FROM" -type f | wc -l | tr -d ' ')" "$(du -sh "$FROM" | cut -f1)"
printf '  out      : %s   name: %s   part: %s MiB\n' "$OUT" "$NAME" "$PART_MIB"

# --- deterministic tar (sorted walk, Rosetta-safe python tarfile) -------------
T0=$(date +%s)
python3 - "$FROM" "$OUT/$NAME.tar" <<'PYEOF'
import os, sys, tarfile
src, dest = sys.argv[1], sys.argv[2]
with tarfile.open(dest, "w") as tf:
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for name in sorted(files):
            p = os.path.join(root, name)
            tf.add(p, arcname=os.path.join("kit-staging", os.path.relpath(p, src)))
PYEOF

# --- split + sidecar manifest -------------------------------------------------
split -b "${PART_MIB}m" "$OUT/$NAME.tar" "$OUT/$NAME.tar.part-"
rm "$OUT/$NAME.tar"
N_PARTS=$(find "$OUT" -name "$NAME.tar.part-*" | wc -l | tr -d ' ')
( cd "$OUT" && find . -type f ! -name usb-manifest.sha256 -print0 | sort -z |
while IFS= read -r -d '' f; do
    p="${f#./}"
    printf '%s\t%s\n' "$(hash_file "$p")" "$p"
done ) > "$OUT/usb-manifest.sha256"

echo "  parts    : $N_PARTS x <= ${PART_MIB} MiB  + usb-manifest.sha256 ($(wc -l < "$OUT/usb-manifest.sha256") rows)"
du -sh "$OUT" | sed 's/^/  size     : /'
echo "usb kit ready: $OUT  ($(( $(date +%s) - T0 ))s) — copy onto a FAT32 stick; unpack-usb-kit.sh restores + re-verifies"