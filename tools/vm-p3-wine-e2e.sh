#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# P3 wine-hub VM E2E (guest-side driver) — run INSIDE the guest after a
# `git archive` sync of the tree (docs/phase3-vm-runbook.md):
#
#   git archive --format=tar.gz -o /tmp/grid-down-suite.tar.gz HEAD
#   vmpal cp /tmp/grid-down-suite.tar.gz "<vm>":/var/tmp/      # or scp
#   # on the guest:
#   mkdir -p /var/tmp/grid-down && tar -xzf /var/tmp/grid-down-suite.tar.gz -C /var/tmp/grid-down
#   sh /var/tmp/grid-down/tools/vm-p3-wine-e2e.sh               # live (x86_64 only)
#   sh /var/tmp/grid-down/tools/vm-p3-wine-e2e.sh --rehearsal   # any arch, no wine
#
# Live mode gates (stages 0–7): x86_64 arch guard (wine 11.20 is x86_64/i686
# only — no aarch64 wine in F45 repos, docs/audit/f45-package-audit.md), wine +
# kit sha256 preflight (kit-import zips, G6), `gd winehub init`, silent
# installs ×3, first-run INI poll (exe-adjacent INI appears on first start),
# `gd config show` inventory, declared-key apply (dry-run → write + backup →
# idempotence), doctor. Needs coreutils sha256sum (present on the guest).
#
# --rehearsal mode proves every file-I/O piece of that pipeline on a fixture
# prefix — dummy .exe/.ini files, candidate-pin recipes, the REAL adapter code
# (read/mask, dry-run diff, backup + atomic write, idempotence, empty-profile
# refusal), the generator --check baseline, and a self-test of the live arch
# guard. No wine anywhere: runs on any arch, including the dev box.
#
# Exits non-zero on the first failed gate; prints P3-E2E: PASS (live) or
# P3-REHEARSAL: PASS (rehearsal) at the end. Capture with:
#   sh tools/vm-p3-wine-e2e.sh 2>&1 | tee /var/tmp/p3-e2e.log
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
gd() { python3 "$here/control/cli/gd.py" "$@"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

# Kit artifacts pinned in install/kit-import/README.md (G6 — user-supplied,
# gitignored, never committed; the double space in the VARA HF name is
# upstream's). Verified verbatim before anything runs.
KIT_SHA256='5ad7d75c722e4414705dec998c28a711b7567f8568bea75cb84e8aa7c991f48a  VARA HF v4.9.0  setup.zip
5c3ee6a6a124e25aaabf5e494b33d268d37b21e4f1462e798f271070a6bc5915  VARA FM v4.4.0 setup.zip
e543116ade4b7c920f52869d4509d31a702634eef341f68f37e0612da38241b7  VarAC v16.1.8.zip'

live() {
    echo "== stage 0: arch guard (wine 11.20 is x86_64/i686 only — audit §wine)"
    arch=$(uname -m)
    echo "arch: $arch"
    [ "$arch" = "x86_64" ] || {
        echo "REFUSED: $arch has no wine in Fedora 45 repos — P3 needs an x86_64 carrier" >&2
        echo "(carrier decision + deferral: docs/phase3-vm-runbook.md §0; run --rehearsal here instead)" >&2
        exit 2
    }

    echo "== stage 1: preflight (wine on PATH, kit zips + sha256, /opt writability)"
    command -v wine >/dev/null || fail "wine not on PATH — runbook §1: sudo dnf install -y --enablerepo=updates-testing wine"
    wine --version
    command -v winetricks >/dev/null || echo "  note: winetricks not installed — the suite never calls it (informational)"
    [ -d "$here/install/kit-import" ] || fail "install/kit-import/ missing from the synced tree (zips are gitignored — runbook §2)"
    ( cd "$here/install/kit-import" && printf '%s\n' "$KIT_SHA256" | sha256sum -c - ) \
        || fail "kit-import sha256 mismatch — re-stage the zips per install/kit-import/README.md"
    if [ -d /opt/grid-down ]; then
        [ -w /opt/grid-down ] || fail "/opt/grid-down exists but is not writable — runbook §1: sudo chown \"\$USER\" /opt/grid-down"
    else
        [ -w /opt ] || fail "/opt not writable — runbook §1: sudo mkdir -p /opt/grid-down && sudo chown \"\$USER\" /opt/grid-down"
    fi

    echo "== stage 2: gd winehub init (shared prefix at /opt/grid-down/wine-prefix)"
    gd winehub init
    gd winehub status | grep -q '^prefix: present' || fail "prefix still absent after init (gd winehub status)"

    echo "== stage 3: silent installs ×3 (kit-import globs; Inno /VERYSILENT + portable zip)"
    for app in vara-hf vara-fm varac; do
        gd winehub install "$app" | grep -E "^install: $app +(installed|already-installed)" \
            || fail "gd winehub install $app did not report installed/already-installed"
    done

    echo "== stage 4: first run — the exe-adjacent INI appears (poll ≤ 600s each)"
    for app in vara-hf varac; do
        gd winehub start "$app" | grep -q "^running: $app" || fail "gd winehub start $app"
        i=0
        while gd config show "$app" 2>/dev/null | grep -q 'not created yet'; do
            [ "$i" -lt 120 ] || fail "$app: INI still absent after 600s — see the winehub install/start logs under the state dir"
            i=$((i + 1))
            sleep 5
        done
        echo "  $app: INI present beside the exe"
        gd winehub stop "$app" | grep -q "^stopped: $app" || fail "gd winehub stop $app"
    done

    echo "== stage 5: config show inventory → evidence (p3-key-inventory/)"
    ev=/var/tmp/grid-down-evidence/p3-key-inventory
    mkdir -p "$ev"
    for app in vara-hf vara-fm varac; do
        gd config show "$app" > "$ev/$app.txt"
        gd config show --json "$app" > "$ev/$app.json"
        grep -q '^registration: ' "$ev/$app.txt" || fail "inventory $app: no registration line (read() output changed?)"
        echo "  wrote $ev/$app.txt (+ .json)"
    done

    echo "== stage 6: declared-key apply (dry-run → write + backup → idempotent)"
    if grep -q 'ini_set: \[\]' "$here/install/recipes/vara-hf.yaml"; then
        echo "  SKIPPED: recipes still declare ini_set: [] — pin the keys first (runbook §5a), then re-run"
    else
        python3 - "$here" <<'PYEOF' || fail "station profile has an empty callsign — create ~/.config/grid-down/profiles/p3-e2e.yaml (runbook §1)"
import sys
sys.path.insert(0, sys.argv[1] + "/control")
from core import profile as prof
station = prof.load_profile().get("profile") or {}
sys.exit(0 if str(station.get("callsign") or "").strip() else 3)
PYEOF
        gd config apply vara-hf --dry-run
        gd config apply vara-hf
        gd config apply vara-hf | grep -q 'already in sync' || fail "re-apply was not idempotent (gd config apply vara-hf)"
    fi

    echo "== stage 7: doctor + final status"
    gd doctor | grep -i 'VARA wine hub'
    gd winehub status
    echo "P3-E2E: PASS"
}

rehearsal() {
    echo "== rehearsal: any arch, no wine — fixture prefix + the real adapter code"
    echo "arch: $(uname -m) (live mode refuses anything but x86_64 — stage 0)"

    fx=$(mktemp -d /var/tmp/gd-p3-rehearsal.XXXXXX)
    trap 'rm -rf "$fx"' EXIT
    echo "fixture: $fx (removed at exit)"

    echo "== R1: fixture prefix + candidate-pin recipes (dummy exes/INIs, masked reg keys)"
    python3 - "$here" "$fx" <<'PYEOF'
import re
import sys
from pathlib import Path

suite, fx = Path(sys.argv[1]), Path(sys.argv[2])


def write(path, data, binary=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


# Dummy exes: the probe only checks basename + is_file — the same index the
# live prefix walk builds (winehub._exe_index).
write(fx / "prefix/drive_c/VARA HF/VARA.exe", b"MZ\x90\x00gd-p3-rehearsal-dummy", binary=True)
write(fx / "prefix/drive_c/VarAC/VarAC.exe", b"MZ\x90\x00gd-p3-rehearsal-dummy", binary=True)

# Fixture INIs exe-adjacent, with registration-shaped keys (read() must mask
# them) and stale values (apply() must replace MyCall, add GridSquare).
write(
    fx / "prefix/drive_c/VARA HF/VARA.ini",
    "[Common]\nPTTPort=NONE\nMyCall=OLD\n\n[Registration]\nRegKey=SECRET-123\nRegStation=K1ABC\n",
)
write(
    fx / "prefix/drive_c/VarAC/VarAC.ini",
    "[Common]\nMyCall=OLD\n\n[Registration]\nRegKey=SECRET-456\n",
)

# Candidate pins — CANDIDATE only: the real key names are unknown until the
# live E2E reads actual VARA INIs (runbook §5a). Fixture recipes are copies
# of the shipped ones with ini_set rows injected; the shipped stay untouched.
ROWS = (
    "    ini_set:   # CANDIDATE pins — rehearsal fixture only, NOT confirmed key names\n"
    "      - section: Common\n"
    "        key: MyCall\n"
    '        value: "{callsign}"\n'
    "      - section: Common\n"
    "        key: GridSquare\n"
    '        value: "{grid}"\n'
)
for app in ("vara-hf", "varac"):
    text = (suite / "install/recipes" / f"{app}.yaml").read_text(encoding="utf-8")
    new, n = re.subn(r"    ini_set:\s*\[\][^\n]*\n", ROWS, text, count=1)
    assert n == 1, f"{app}: shipped recipe has no ini_set: [] line to inject into"
    write(fx / "recipes" / f"{app}.yaml", new)
print("  fixture ready: 2 exes, 2 INIs, 2 candidate-pin recipes")
PYEOF

    echo "== R2: adapters on the fixture — read/mask, dry-run diff, write+backup, idempotence"
    python3 - "$here" "$fx" <<'PYEOF'
import sys
from pathlib import Path

suite, fx = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(suite / "control"))

from core import adapters, registry, winehub  # noqa: E402

apps = {a["id"]: a for a in registry.load_registry(suite / "control" / "registry" / "apps")}
profile = {"profile": {"callsign": "W1AW", "grid": "FN31pr"}}


def make(app_id):
    app = apps[app_id]
    hub = winehub.WineHub(
        [app], prefix=fx / "prefix", recipes_dir=fx / "recipes", state_dir=fx / "state"
    )
    return adapters.get_adapter(app, hub=hub, recipes_dir=fx / "recipes", state_dir=fx / "state")


for app_id in ("vara-hf", "varac"):
    ada = make(app_id)

    info = ada.read()
    assert info["exists"], f"{app_id}: fixture INI missing"
    assert info["registration"] == "present (values masked)", info["registration"]
    masked = [v for sec in info["sections"].values() for v in sec.values() if v == "***"]
    assert masked, f"{app_id}: registration-shaped values were not masked"
    print(f"  {app_id}: read ok — {Path(info['path']).name} ({len(masked)} masked keys)")

    dry = ada.apply(profile, dry_run=True)
    assert dry["changed"] and dry["dry_run"] and not dry.get("wrote"), dry
    assert "+MyCall=W1AW" in dry["diff"] and "+GridSquare=FN31pr" in dry["diff"], dry["diff"]
    print(f"  {app_id}: dry-run diff ok ({len(dry['diff'].splitlines())} lines, nothing written)")

    try:
        ada.apply({"profile": {"callsign": "", "grid": "FN31pr"}})
        raise SystemExit(f"FAIL: {app_id}: apply accepted an empty callsign")
    except adapters.ConfigError as exc:
        assert "callsign" in str(exc), exc
    print(f"  {app_id}: empty-profile refusal ok")

    out = ada.apply(profile)
    assert out.get("wrote") and out.get("backup"), out
    backup = Path(out["backup"])
    assert backup.is_file() and b"MyCall=OLD" in backup.read_bytes(), backup
    written = Path(out["path"]).read_text(encoding="utf-8")
    assert "MyCall=W1AW" in written and "GridSquare=FN31pr" in written, written
    print(f"  {app_id}: wrote ok — backup {backup.name} keeps the old values")

    again = ada.apply(profile)
    assert not again["changed"] and "already in sync" in again.get("note", ""), again
    print(f"  {app_id}: idempotent ok")
PYEOF

    echo "== R3: generator --check baseline (recipes stay generator-owned; dev-box baseline: 37 DIFFs)"
    n=$( (cd "$here" && python3 tools/gen-registry-stubs.py --check || true) | grep -c '^DIFF' || true)
    echo "  DIFF files: $n (expected ${GD_GEN_DIFF_BASELINE:-37})"
    [ "$n" -eq "${GD_GEN_DIFF_BASELINE:-37}" ] || fail "generator --check DIFF count drifted from the baseline — see runbook §5a"

    echo "== R4: self-test — the live arch guard refuses this arch"
    if [ "$(uname -m)" = "x86_64" ]; then
        echo "  SKIPPED on x86_64 (the guard only refuses non-x86_64 archs)"
    else
        rc=0
        sh "$0" </dev/null 2>/tmp/gd-p3-guard.$$ || rc=$?
        guard_ok=0
        [ "$rc" -eq 2 ] && grep -q 'x86_64 carrier' /tmp/gd-p3-guard.$$ && guard_ok=1
        if [ "$guard_ok" -ne 1 ]; then
            cat /tmp/gd-p3-guard.$$ >&2
            rm -f /tmp/gd-p3-guard.$$
            fail "arch guard did not refuse cleanly (exit $rc, message above)"
        fi
        echo "  refused cleanly (exit 2, runbook pointer present)"
        rm -f /tmp/gd-p3-guard.$$
    fi

    echo "P3-REHEARSAL: PASS"
}

case "${1:-}" in
    --rehearsal) rehearsal ;;
    "") live ;;
    *) echo "usage: $0 [--rehearsal]" >&2
       echo "  (no flags)  live E2E — x86_64 guest with wine + staged kit (runbook §5)" >&2
       echo "  --rehearsal fixture-only pinning rehearsal — any arch, no wine (runbook §4)" >&2
       exit 2 ;;
esac
