#!/usr/bin/env bash
# Grid Down offline-kit builder — host-side orchestrator (Phase 1).
# Resolves dnf-strategy recipes into repo-split package sets, then runs
# container-build.sh inside fedora:45 (network ON) to produce a sha256-pinned kit.
#
# Usage: install/bundle-builder/build-kit.sh
#          [--arch x86_64|aarch64]      target arch (default x86_64; aarch64 for
#                                       ARM targets — spike S4: --forcearch works)
#          [--apps id[,id...]]          apps to include (default: direwolf,fldigi,hamlib)
#          [--all-dnf]                  include every dnf-strategy app instead
#          [--with-rpmfusion]           enable pinned RPM Fusion release RPMs
#                                       (auto-on if a selected app needs rpmfusion-free)
#          [--out DIR]                  kit output dir (default: $GD_ROOT/kit)
#          [--no-pack]                  leave the staging tree only (round-trip dev loop)
# Env: GD_BASE_IMAGE (default fedora:45), GD_KIT_OUT
# P1 scope: dnf-strategy apps only; anything else fails with a clear list (P3+).
# Specs: docs/SPECIFICATION.md §5, §6.8; audit: docs/audit/f45-package-audit.md.
set -euo pipefail

GD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${GD_BASE_IMAGE:-fedora:45}"
ARCH="x86_64"; APPS=""; ALL_DNF=0; RPMFUSION="${GD_RPMFUSION:-0}"; PACK=1
OUT="${GD_KIT_OUT:-$GD_ROOT/kit}"

while [ $# -gt 0 ]; do
    case "$1" in
        --arch)           ARCH="$2"; shift 2 ;;
        --apps)           APPS="$2"; shift 2 ;;
        --all-dnf)        ALL_DNF=1; shift ;;
        --with-rpmfusion) RPMFUSION=1; shift ;;
        --out)            OUT="$2"; shift 2 ;;
        --no-pack)        PACK=0; shift ;;
        -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
    esac
done
case "$ARCH" in x86_64|aarch64) ;; *) echo "bad --arch: $ARCH" >&2; exit 2 ;; esac

# --- resolve recipes -> app ids + repo-split package sets (host has PyYAML) ---
PY="python3"
if [ -x "$GD_ROOT/.venv/bin/python" ]; then PY="$GD_ROOT/.venv/bin/python"; fi
RESOLVE="$("$PY" - "$GD_ROOT" "$APPS" "$ALL_DNF" <<'PYEOF'
import pathlib, sys
import yaml
root, sel, all_dnf = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
rec = pathlib.Path(root) / "install" / "recipes"
apps = {}
for p in sorted(rec.glob("*.yaml")):
    r = yaml.safe_load(p.read_text())["recipe"]
    dnf = r.get("dnf") or {}
    apps[r["id"]] = (r["strategy"], dnf.get("repo", "fedora"), dnf.get("packages") or [])
if all_dnf:
    ids = sorted(i for i, (s, _, pk) in apps.items() if s == "dnf" and pk)
elif sel:
    ids = [s.strip() for s in sel.split(",") if s.strip()]
else:
    ids = ["direwolf", "fldigi", "hamlib"]          # P1 acceptance set
unknown = [i for i in ids if i not in apps]
if unknown:
    sys.exit(f"unknown apps: {unknown}")
unsupported = [(i, apps[i][0]) for i in ids if apps[i][0] != "dnf" or not apps[i][2]]
if unsupported:
    sys.exit(f"P1 supports dnf-strategy apps only; non-dnf selected: {unsupported}")
fed, rf = [], []
for i in ids:
    (fed if apps[i][1] == "fedora" else rf).extend(apps[i][2])
print(" ".join(ids), "|", " ".join(dict.fromkeys(fed)), "|", " ".join(dict.fromkeys(rf)))
PYEOF
)"
IFS='|' read -r APPS PKGS_FEDORA PKGS_RPMFUSION <<< "$RESOLVE"
APPS="${APPS#"${APPS%%[![:space:]]*}"}"
[ -n "${PKGS_RPMFUSION// /}" ] || PKGS_RPMFUSION=""   # IFS-read leaves a " " in the tail field
[ -n "$APPS" ] || { echo "no apps selected" >&2; exit 2; }
if [ -n "$PKGS_RPMFUSION" ] && [ "$RPMFUSION" != "1" ]; then
    RPMFUSION=1   # an rpmfusion-free app is in the set — enablement is mandatory
fi

echo "grid-down kit builder"
echo "  apps        : $(tr ' ' ',' <<< "$APPS")"
echo "  packages    : fedora=[$PKGS_FEDORA] rpmfusion=[$PKGS_RPMFUSION]"
echo "  arch        : $ARCH   rpmfusion-enablement: $RPMFUSION"
echo "  image       : $IMAGE (linux/amd64)   out: $OUT   pack: $PACK"

STAGING="$GD_ROOT/kit-staging"
rm -rf "$STAGING"; mkdir -p "$STAGING" "$OUT"
docker run --rm --platform linux/amd64 \
    -e GD_IN_OUT=/kit-staging -e GD_IN_ARCH="$ARCH" -e GD_IN_RPMFUSION="$RPMFUSION" \
    -e GD_IN_PACK="$PACK" -e GD_IN_APPS="$APPS" \
    -e GD_IN_PKGS_FEDORA="$PKGS_FEDORA" -e GD_IN_PKGS_RPMFUSION="$PKGS_RPMFUSION" \
    -v "$GD_ROOT/install:/install-src:ro" \
    -v "$STAGING:/kit-staging" \
    -v "$OUT:/kits" \
    "$IMAGE" bash /install-src/bundle-builder/container-build.sh

# --- post-build sanity on the host ---
[ -f "$STAGING/manifest.sha256" ] || { echo "FATAL: kit staging incomplete" >&2; exit 1; }
echo "kit ready:"
awk -F'\t' '{n++} END {printf "  manifest : %d files\n", n}' "$STAGING/manifest.sha256"
printf '  rpms     : %s\n' "$(find "$STAGING/repo" -maxdepth 1 -name '*.rpm' | wc -l | tr -d ' ')"
printf '  apps     : %s\n' "$(tr '\n' ' ' < "$STAGING/apps.txt")"
du -sh "$STAGING" "$OUT" 2>/dev/null | sed 's/^/  size /' || true
