#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Container-side half of tools/systemd-analyze-verify.sh: stub any missing
# ExecStart binaries (verify checks unit syntax, but flags missing executables),
# then run `systemd-analyze verify` over the mounted unit set. Exits nonzero
# on any verify error.
set -uo pipefail

# fedora:45 container image is minimal: systemd-analyze and python3 are needed
dnf install -y -q systemd python3 >/dev/null 2>&1 || dnf install -y systemd python3

python3 - <<'PY'
import os, pathlib, shlex, shutil
TRUE = "/usr/bin/true"  # real ELF: avoids shebang/emu quirks in verify's exec check
for f in sorted(pathlib.Path("/units").glob("*.service")):
    for line in f.read_text().splitlines():
        if line.startswith("ExecStart="):
            argv = shlex.split(line.split("=", 1)[1])
            p = pathlib.Path(argv[0])
            if not p.is_absolute():  # bare name: systemd resolves via PATH
                p = pathlib.Path("/usr/bin") / argv[0]
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                if os.path.exists(TRUE):
                    shutil.copy(TRUE, str(p))
                else:
                    p.write_text("#!/bin/sh\nexit 0\n")
                os.chmod(p, 0o755)
                print(f"stubbed {p} (for verify)")
PY

rc=0
systemd-analyze verify /units/*.service /units/*.target || rc=$?
echo "systemd-analyze verify rc=$rc"
exit "$rc"
