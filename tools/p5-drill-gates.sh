#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
#
# tools/p5-drill-gates.sh — Phase 5 M13 clean-host drill: gate stages.
# Runs INSIDE a fresh fedora:45 linux/amd64 station container (Rosetta, Docker
# Desktop) as root, with the network switched OFF (`--network none`) and two
# read-only mounts:
#   /usb — the M12 USB layout (gd-kit-*.tar.part-* + usb-manifest.sha256)
#   /gd  — the operator checkout (only tools/unpack-usb-kit.sh is read)
# The chain this script drives, in order:
#   G0     USB layout + sidecar verify, unpacked to a container-local /restore
#   G1     arch guard: a self-consistent wrong-arch kit is refused, no mutation
#   G2     strict kit manifest verify (the kit's own installer lib)
#   (install) bootstrap.sh --kit: repo-only dnf + payloads + declarative postinst
#   G3     dnf canaries: every install-strategy family of the full set
#   G4     payload placement: build ELF, tarball bins, pip venv pin, static
#   G5     postinst effects: dialout, rendered user units, wine subset on the
#          shared prefix (VARA HF/FM + VarAC; no recipe declares a udev row yet)
#   G6     re-run is a no-op: dnf skips, watermarks honored, state byte-identical
#   G7     gd doctor exits 0 (warn-level caveats allowed; audit doc)
#   G8     render sanity, spawn-free: mesh adapter dry-run (argv + PSK mask),
#          wine-hub config surface, rendered-units drift check
# Since M12 the kit carries control/ and install/, so every gate below reads
# the suite + installer from the restored kit — /gd only boots the unpacker.
# tools/drill-clean-rebuild.sh is the host-side orchestrator that builds, packs,
# wipes the staging tree and runs this script once per round; the final line on
# success is exactly "P5: PASS", which it greps per run (N x PASS discipline).
set -euo pipefail

USB=/usb               # USB layout (read-only mount)
GD=/gd                 # operator checkout (read-only mount) — unpacker only
RESTORE=/restore       # container-local unpack target
KIT=$RESTORE/kit-staging
STATE=/var/lib/grid-down/state.json
BACKEND=direct         # no systemd user session in a container (spec §6.5)
# The restored kit must stay byte-pristine: G6's re-run re-verifies the kit
# manifest, and python would otherwise drop __pycache__/ into control/ (M12's
# roundtrip never saw this — it mounted the kit read-only; a restored tree is
# writable). No bytecode anywhere, drill or delegation.
export PYTHONDONTWRITEBYTECODE=1

fail() { printf 'P5-FAIL: %s\n' "$*" >&2; exit 1; }
stage() { printf '\n== %s\n' "$*"; }
gd() { python3 "$KIT/control/cli/gd.py" "$@"; }   # the kit's own suite

stage "G0 — USB layout + sidecar, unpacked to a container-local /restore"
[ -f "$USB/usb-manifest.sha256" ] || fail "no usb-manifest.sha256 at $USB"
[ ! -f "$USB/ARCH" ] || fail "$USB is a bare kit staging tree (ARCH present) — the drill must start from the USB layout"
ls "$USB"/*.part-* >/dev/null 2>&1 || fail "no *.part-* files at $USB"
ROWS=$(wc -l < "$USB/usb-manifest.sha256" | tr -d ' ')
PARTS=$(ls "$USB"/*.part-* | wc -l | tr -d ' ')
[ "$ROWS" -eq "$PARTS" ] || fail "sidecar rows ($ROWS) != part files ($PARTS)"
rm -rf "$RESTORE"; mkdir -p "$RESTORE"
bash "$GD/tools/unpack-usb-kit.sh" --from "$USB" --out "$RESTORE" | sed 's/^/  /'
[ -f "$KIT/manifest.sha256" ] || fail "restored tree has no kit manifest"
[ -f "$KIT/ARCH" ] || fail "restored tree has no ARCH"
KIT_ARCH="$(cat "$KIT/ARCH")"
[ "$KIT_ARCH" = "x86_64" ] || fail "restored kit is $KIT_ARCH — the field kit is x86_64"
printf '  restored: %s (%s manifest rows), arch %s\n' "$KIT" \
    "$(wc -l < "$KIT/manifest.sha256" | tr -d ' ')" "$KIT_ARCH"

stage "G1 — arch guard: a self-consistent wrong-arch kit is refused BEFORE mutating"
rm -rf /wrongarch; cp -a "$KIT" /wrongarch
printf 'aarch64\n' > /wrongarch/ARCH
# Re-sign the tampered copy: flipping ARCH alone would trip the EARLIER
# manifest guard (ARCH is hashed), so rewrite its manifest row too — the
# refusal below must come from the arch guard (bootstrap.sh's arch check),
# the gate that stops a mis-built kit before a single byte is installed.
python3 - <<'PY' || fail "could not re-sign the tampered kit's manifest"
import hashlib
man = "/wrongarch/manifest.sha256"
rows = []
with open(man) as fh:
    for line in fh:
        cols = line.rstrip("\n").split("\t")
        if len(cols) >= 2 and cols[1] == "ARCH":
            with open("/wrongarch/ARCH", "rb") as arch:
                cols[0] = hashlib.sha256(arch.read()).hexdigest()
        rows.append("\t".join(cols))
with open(man, "w") as fh:
    fh.write("\n".join(rows) + "\n")
PY
if WRONG_OUT="$(bash /wrongarch/install/bootstrap.sh --kit /wrongarch 2>&1)"; then
    fail "bootstrap accepted a wrong-arch kit"
fi
printf '%s\n' "$WRONG_OUT" | tail -n 2 | sed 's/^/  /'
printf '%s' "$WRONG_OUT" | grep -q 'kit arch mismatch' \
    || fail "refused, but not by the arch guard: $(printf '%s' "$WRONG_OUT" | tail -n 1)"
[ ! -e "$STATE" ] || fail "arch guard wrote state before refusing"
rm -rf /wrongarch

stage "G2 — strict kit manifest verify (the kit's own installer lib)"
GD_ROOT="$KIT" . "$KIT/install/lib/common.sh"
. "$KIT/install/lib/kit.sh"
gd_kit_verify "$KIT" || fail "the kit's own strict verifier refused the restored tree"

stage "install — bootstrap.sh --kit (real operator, repo-only dnf, postinst)"
# A real operator: the historic uid-11 "operator" system account (the setup
# package's /etc/passwd relic — home /root, nologin) is not one; replace it
# first (M12 gate finding) so the postinst delegation can write HOME files.
if id operator >/dev/null 2>&1 && [ "$(id -u operator)" -lt 1000 ]; then
    userdel operator
fi
id operator >/dev/null 2>&1 || useradd -m operator
bash "$KIT/install/bootstrap.sh" --kit "$KIT" --user operator 2>&1 | tee /tmp/install.log
grep -qF 'install complete:' /tmp/install.log || fail "bootstrap didn't reach install complete"

stage "G3 — dnf canaries: every install-strategy family of the full set"
# Canary per family: core APRS/digital (dnf), full-set GUI/dnf-only adds,
# the x86_64 closure (RPM Fusion ffmpeg, wine, java, node, rtl-sdr) and the
# installer tooling + python3.14 runtime rows the kit itself must ship.
# The air-gap is the container itself: this ran with --network none.
for pkg in hamlib fldigi direwolf \
           qsstv js8call wsjtx flamp flmsg fllog \
           audacity sox conky snapshot kiwix-desktop \
           rtl-sdr ffmpeg wine \
           python3.14 bsdtar; do
    rpm -q "$pkg" >/dev/null 2>&1 || fail "rpm $pkg not installed (kit dnf set incomplete)"
done
# flarq ships inside Fedora's fldigi rpm (Provides: flarq — recipe #1 finding):
# there is no flarq rpm to query, so canary the binary the kit puts on PATH
command -v flarq >/dev/null 2>&1 || fail "flarq binary absent (fldigi's Provides not installed?)"
printf '  18 rpm canaries + flarq binary present · station total: %s rpms\n' \
    "$(rpm -qa --qf '%{NAME}\n' | wc -l | tr -d ' ')"

stage "G4 — payload placement: build ELF, tarball bins, pip pin, operator static"
python3 - "$KIT" <<'PY' || fail "payload canaries failed"
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "control"))
from core import registry

apps = {a["id"]: a for a in registry.load_registry(root / "control" / "registry" / "apps")}

# build-strategy: the compiled launcher lands at the registry's path, x86-64 ELF
bin_path = apps["ardop"]["launch"]["cmd"].split()[0]
assert Path(bin_path).is_file(), f"ardop: build payload missing at {bin_path}"
out = subprocess.run(["file", "-b", bin_path], capture_output=True, text=True).stdout
assert "ELF 64-bit" in out and "x86-64" in out, f"ardop: {bin_path} not a x86-64 ELF: {out.strip()}"
print(f"  build  : ardop -> {bin_path} ({out.strip()[:44]})")
PY
# tarball-strategy: the pinned binary lands at the recipe dest + is executable
# (pat's registry launch cmd is a bare PATH name — the recipe dest is the truth)
PAT_BIN="$(find /opt/grid-down/pat -maxdepth 2 -type f -name pat -perm -u+x 2>/dev/null | head -1)"
[ -n "$PAT_BIN" ] || fail "pat: tarball payload missing under /opt/grid-down/pat (recipe dest)"
printf '  tarball: pat   -> %s\n' "$PAT_BIN"
[ -e /opt/grid-down/js8spotter/js8spotter.py ] || fail "js8spotter payload incomplete (js8spotter.py missing — flatten?)"
[ -e /opt/grid-down/js8spotter/azure.tcl ] || fail "js8spotter payload incomplete (azure.tcl missing)"
MT=/opt/grid-down/meshtastic/venv/bin/meshtastic
"$MT" --version | grep -q '2\.7\.11' || fail "meshtastic --version is not the pin (2.7.11): $("$MT" --version)"
[ -n "$(ls -A /opt/grid-down/meshtastic-web 2>/dev/null)" ] || fail "meshtastic-web docroot empty (operator content missing)"
[ -f /opt/grid-down/meshtastic-web/index.html ] || fail "meshtastic-web docroot has no index.html"
YAAC_JAR="$(find /opt/grid-down -maxdepth 3 -name '*.jar' 2>/dev/null | head -1)"
[ -n "$YAAC_JAR" ] || fail "no tarball JAR payload under /opt/grid-down (yaac missing?)"
printf '  static : meshtastic-web docroot ok · jar: %s\n' "$YAAC_JAR"

stage "G5 — postinst effects: dialout, user units, wine subset on the shared prefix"
id -nG operator | tr ' ' '\n' | grep -qx dialout || fail "operator is not in the dialout group"
UNITS=$(ls /home/operator/.config/systemd/user/grid-down-*.service 2>/dev/null | wc -l | tr -d ' ')
[ "$UNITS" -ge 1 ] || fail "no grid-down-*.service units rendered for the operator"
printf '  %s grid-down-* user units rendered for operator\n' "$UNITS"
grep -q 'postinst\[direwolf/units\]' /tmp/install.log \
    || fail "units step didn't delegate to the kit suite (direwolf)"
grep -q 'postinst\[direwolf/dialout\]' /tmp/install.log \
    || fail "dialout step didn't delegate to the kit suite (direwolf)"
# wine subset on the shared prefix: the exes each kit recipe declares must exist
python3 - "$KIT" <<'PY' || fail "wine subset missing from the shared prefix"
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
drive_c = Path("/opt/grid-down/wine-prefix/drive_c")
for app_id in ("vara-hf", "vara-fm", "varac"):
    rec = yaml.safe_load((root / "install" / "recipes" / f"{app_id}.yaml").read_text())["recipe"]
    names = {str(n).lower() for n in (rec.get("wine") or {}).get("exes") or []}
    if not names:
        raise SystemExit(f"{app_id}: kit recipe declares no wine.exes")
    hit = next(
        (p for p in drive_c.rglob("*") if p.suffix.lower() == ".exe" and p.name.lower() in names),
        None,
    )
    if hit is None:
        raise SystemExit(f"{app_id}: none of {sorted(names)} found under {drive_c}")
    print(f"  wine   : {app_id} -> {hit.relative_to(hit.parent.parents[2])}")
PY

stage "G6 — re-run is a no-op (dnf skips, watermarks, byte-identical state)"
[ -f "$STATE" ] || fail "state.json missing after install"
cp "$STATE" /tmp/state.run1
bash "$KIT/install/bootstrap.sh" --kit "$KIT" --user operator 2>&1 | tee /tmp/rerun.log
grep -q 'is already installed' /tmp/rerun.log || fail "re-run: dnf did work"
for app in direwolf fldigi hamlib; do
    grep -qF "postinst[$app/units]: already applied (re-run: skip)" /tmp/rerun.log \
        || fail "re-run: units watermark not honored for $app"
    grep -qF "postinst[$app/dialout]: already applied (re-run: skip)" /tmp/rerun.log \
        || fail "re-run: dialout watermark not honored for $app"
done
# vara-fm carries dialout + wine rows but no units row (its G6 check)
grep -qF "postinst[vara-fm/dialout]: already applied (re-run: skip)" /tmp/rerun.log \
    || fail "re-run: dialout watermark not honored for vara-fm"
python3 - "$STATE" <<'PY' || fail "re-run: state.json changed"
import sys

same = open(sys.argv[1], "rb").read() == open("/tmp/state.run1", "rb").read()
sys.exit(0 if same else 1)
PY
grep -qF 'install complete:' /tmp/rerun.log || fail "re-run didn't reach install complete"
echo "  dnf skipped · watermarks honored · state.json byte-identical"

stage "G7 — doctor: exit 0 (warn-level caveats allowed; audit doc records them)"
export GRID_DOWN_PROFILE=/tmp/p5-drill-profile.yaml
printf 'profile:\n  callsign: W1AW\n  grid: FN31pr\n' > "$GRID_DOWN_PROFILE"
gd doctor 2>&1 | tee /tmp/g7.out | sed 's/^/  /'

stage "G8 — render sanity, spawn-free: mesh argv + PSK mask, wine surface, units drift"
gd units list 2>&1 | tee /tmp/g8-units.out | sed 's/^/  /'
grep -q 'grid-down-' /tmp/g8-units.out || fail "units list shows no grid-down units"
python3 - "$KIT" <<'PY' || fail "render pins failed"
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "control"))
from core import adapters, registry  # noqa: E402

apps = {a["id"]: a for a in registry.load_registry(root / "control" / "registry" / "apps")}
ada = adapters.get_adapter(apps["meshtastic"], install_root="/opt/grid-down")
assert str(ada.resolve_cli()) == "/opt/grid-down/meshtastic/venv/bin/meshtastic", ada.resolve_cli()

profile = {
    "profile": {"callsign": "W1AW", "grid": "FN31pr"},
    "mesh": {
        "device": {"type": "tcp", "host": "127.0.0.1"},
        "node": {
            "long_name": "P5 Drill",
            "short_name": "P5DR",
            "region": "UNSET",
            "modem_preset": "LONG_FAST",
            "channel_url": "https://meshtastic.org/e/#CgcIAQgBEBAQGgMQAw",
        },
    },
}
dry = ada.apply(profile, dry_run=True)  # spawn-free by contract
assert dry["dry_run"] and dry["changed"] is False, dry
assert dry["target"] == "tcp 127.0.0.1", dry["target"]
cmds = dry["commands"]
assert any("--host" in c and "127.0.0.1" in c for c in cmds), cmds
assert any("--seturl ***" in c for c in cmds), cmds
assert not any("CgcIAQgBEBAQ" in c for c in cmds), "PSK leaked into display"
assert dry["payload"]["config"]["user"]["long_name"] == "P5 Drill", dry["payload"]
assert dry["payload"]["config"]["lora"]["region"] == "UNSET", dry["payload"]
print("  mesh   : dry-run render ok (target argv, --seturl masked, payload 2 sections)")

for bad, why in (
    ({"profile": {"callsign": "W1AW"}}, "no mesh block"),
    ({
        "profile": {"callsign": "W1AW"},
        "mesh": {"device": {"type": "serial"}, "node": {"long_name": "x"}},
    }, "serial without path"),
    ({
        "profile": {"callsign": "W1AW"},
        "mesh": {
            "device": {"type": "tcp", "host": "127.0.0.1"},
            "daemon": {"enabled": True},
            "node": {"long_name": "x"},
        },
    }, "daemon.enabled true"),
):
    try:
        ada.apply(bad, dry_run=True)
        raise SystemExit(f"FAIL: apply accepted {why}")
    except adapters.ConfigError as exc:
        print(f"  refusal ok ({why}): {str(exc)[:72]}…")
PY
gd config show vara-hf 2>&1 | tee /tmp/g8-vara.out | sed 's/^/  /'
grep -q '/opt/grid-down/wine-prefix' /tmp/g8-vara.out \
    || fail "wine-hub config surface didn't report the shared prefix"

printf '\nP5: PASS\n'