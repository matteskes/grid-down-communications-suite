#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Restore a kit from a make-usb-kit.sh USB layout (M12, spec §6.9 P4): verify
# every part against the sidecar usb-manifest.sha256, reassemble the tar, extract,
# then re-hash the extracted tree against the kit's OWN manifest.sha256 — the
# same strict check gd_kit_verify runs at install time, so the tool refuses a
# layout the installer would refuse. Dependency-free: python3 tarfile + sha256sum.
# Usage: tools/unpack-usb-kit.sh [--from DIR] [--out DIR] [--force]
#   --from DIR   USB layout dir (default $GD_ROOT/usb-kit)
#   --out DIR    where the kit-staging tree is restored (default $GD_ROOT/kit-staging);
#                must be empty unless --force wipes it
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FROM="$GD_ROOT/usb-kit"; OUT="$GD_ROOT/kit-staging"; FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --from)    FROM="$2"; shift 2 ;;
        --out)     OUT="$2"; shift 2 ;;
        --force)   FORCE=1; shift ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
    esac
done

[ -f "$FROM/usb-manifest.sha256" ] || { echo "FATAL: $FROM has no usb-manifest.sha256 (pack first: tools/make-usb-kit.sh)" >&2; exit 1; }

# macOS ships shasum(1); Linux/coreutils sha256sum(1) — accept either
hash_file() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
if [ -d "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
    [ "$FORCE" = "1" ] || { echo "FATAL: $OUT is not empty (use --force to wipe it)" >&2; exit 1; }
    rm -rf "$OUT"
fi
mkdir -p "$OUT"

echo "grid-down USB unpacker"
printf '  from     : %s\n  out      : %s\n' "$FROM" "$OUT"

# --- 1. verify every part against the sidecar manifest ------------------------
T0=$(date +%s)
( cd "$FROM" && while IFS=$'\t' read -r want p; do
    [ -f "$p" ] || { echo "FATAL: usb layout missing $p" >&2; exit 1; }
    got=$(hash_file "$p")
    [ "$got" = "$want" ] || { echo "FATAL: $p hash mismatch (transport corruption)" >&2; exit 1; }
done < usb-manifest.sha256 ) || exit 1
N_ROWS=$(wc -l < "$FROM/usb-manifest.sha256" | tr -d ' ')
echo "  verified : $N_ROWS layout files against usb-manifest.sha256"

# --- 2. reassemble + extract --------------------------------------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TAR="$TMP/kit.tar"
# parts concatenate in sorted order; xargs batches are sequential, order is kept
( cd "$FROM" && sed -n 's/^[0-9a-f]*\t//p' usb-manifest.sha256 | grep '\.part-' | sort | xargs cat ) > "$TAR"
python3 - "$TAR" "$OUT" <<'PYEOF'
import sys, tarfile
tar, dest = sys.argv[1], sys.argv[2]
with tarfile.open(tar) as tf:
    try:
        tf.extractall(dest, filter="data")   # 3.12+: no traversal/setuid surprises
    except TypeError:
        tf.extractall(dest)
PYEOF

# --- 3. re-hash the restored tree against the kit's own manifest --------------
KIT="$OUT/kit-staging"
[ -f "$KIT/manifest.sha256" ] || { echo "FATAL: restored tree has no manifest.sha256" >&2; exit 1; }
python3 - "$KIT" <<'PYEOF' || exit 1
import hashlib, os, sys
kit = sys.argv[1]
rows, bad = 0, []
with open(os.path.join(kit, "manifest.sha256")) as fh:
    for line in fh:
        want, rel = line.rstrip("\n").split("\t")[:2]
        rows += 1
        p = os.path.join(kit, rel)
        if not os.path.isfile(p):
            bad.append(f"missing {rel}")
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != want:
            bad.append(f"hash {rel}")
if bad:
    print("\n".join("FATAL: " + b for b in bad[:20]), file=sys.stderr)
    sys.exit(1)
print(f"  restored : {rows} kit files re-hashed against the kit's manifest.sha256 — clean")
PYEOF

printf '  arch     : %s\n' "$(cat "$KIT/ARCH" 2>/dev/null || echo MISSING)"
du -sh "$KIT" | sed 's/^/  size     : /'
echo "kit restored: $KIT  ($(( $(date +%s) - T0 ))s) — install air-gap side: install/bootstrap.sh --kit $KIT"