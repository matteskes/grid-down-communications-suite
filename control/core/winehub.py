# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Wine hub — shared prefix + start/stop/COM mapping for the VARA family (§6.4).

One Wine prefix at ``/opt/grid-down/wine-prefix`` serves every wine-hub app
(VARA HF/FM/Sat/Chat/Term, VarAC). The supervisor refuses to daemonize these
apps (spec §6.5); this module owns their lifecycle instead:

- ``status()``             prefix presence, wine binary, COM device map, pids
- ``start(app_id)``        detached ``wine <exe>`` spawn + pid table
- ``stop(app_id)``         SIGTERM → SIGKILL teardown, table cleanup
- ``map_serial``/``unmap`` ``dosdevices/comN`` symlinks so Windows apps see
                           a COM port for a real serial device (§6.4)

The prefix itself is created by the kit postinst (§5) from the cached prefix
tarball; without it every verb refuses cleanly naming the fix. VARA
end-to-end (licenses, per-app install probes) is Phase 3 (§10).

State layout (override dir with $GRID_DOWN_STATE_DIR):
    <state>/winehub.json           pid table (single-writer; atomic writes)
    <state>/logs/winehub/<id>.log  per-app stdout+stderr capture

Installers/licenses never leave ``install/kit-import/`` (G6) — nothing here
reads or copies them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

try:  # package import (gd CLI, pytest)
    from . import supervisor as sup
except ImportError:  # pragma: no cover - sibling import inside control/
    import supervisor as sup  # type: ignore

WINE_PREFIX = Path("/opt/grid-down/wine-prefix")
STATE_VERSION = 1
TERM_GRACE_S = sup.TERM_GRACE_S
# DOS-era COM ports; wine's dosdevices convention accepts com1..com32.
COM_RE = re.compile(r"^com([1-9]|[12][0-9]|3[0-2])$")
EXE_RE = re.compile(r"^[A-Za-z0-9._-]+\.exe$", re.IGNORECASE)


class WinehubError(RuntimeError):
    """Refusal with operator-facing text (same contract as SupervisorError)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_table(state_dir: Path) -> dict:
    path = state_dir / "winehub.json"
    if not path.exists():
        return {"version": STATE_VERSION, "instances": {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError, OSError:
        return {"version": STATE_VERSION, "instances": {}}
    data.setdefault("instances", {})
    return data


def _save_table(state_dir: Path, table: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "winehub.json"
    tmp = path.with_suffix(".json.tmp")
    table["updated"] = _now()
    tmp.write_text(json.dumps(table, indent=2) + "\n")
    os.replace(tmp, path)  # atomic (adapter convention, §6.2)


def exe_for(app: dict) -> str:
    """The Windows executable a wine-hub app launches (registry launch.cmd).

    Prose parentheticals ("VARA.exe (via gd winehub)") and <placeholders>
    are dropped, mirroring the supervisor's cmd parsing; the remaining
    token must name an .exe — wine-hub entries never carry plain commands.
    """
    cmd = str((app.get("launch") or {}).get("cmd") or "")
    tokens = [
        t
        for t in cmd.split()
        if not re.fullmatch(r"\([^)]*\)", t) and not re.fullmatch(r"<[a-z_]+>", t)
    ]
    for tok in tokens:
        if EXE_RE.match(tok):
            return tok
    known = app.get("id", "?")
    raise WinehubError(
        f"{known}: launch.cmd names no Windows executable (.exe) — "
        f"wine-hub apps need one to start under wine (got: {cmd!r})"
    )


class WineHub:
    """Lifecycle + COM mapping over the shared VARA wine prefix (§6.4)."""

    def __init__(
        self,
        apps: list[dict],
        state_dir: Path | None = None,
        prefix: Path | None = None,
    ):
        self.apps: dict[str, dict] = {a["id"]: a for a in apps}
        self.wine_apps = sorted(i for i, a in self.apps.items() if self._is_wine(a))
        self.state_dir = Path(state_dir) if state_dir else sup.default_state_dir()
        self.prefix = Path(prefix) if prefix else WINE_PREFIX

    @staticmethod
    def _is_wine(app: dict) -> bool:
        return (app.get("launch") or {}).get("via") == "wine-hub"

    def app(self, app_id: str) -> dict:
        try:
            return self.apps[app_id]
        except KeyError:
            known = ", ".join(sorted(self.apps))
            raise WinehubError(f"unknown app id: {app_id} (registered: {known})") from None

    # ------------------------------------------------------------- prefixes
    def _require_prefix(self) -> None:
        if not self.prefix.is_dir():
            raise WinehubError(
                f"wine prefix {self.prefix} not installed yet — it is created by the "
                "kit postinst (bootstrap.sh --kit) from the cached prefix tarball "
                "(spec §5); VARA installers stay in install/kit-import/ (G6)"
            )

    def _require_wine(self) -> str:
        wine = shutil.which("wine")
        if not wine:
            raise WinehubError(
                "wine binary not found on PATH — install it first "
                "(Fedora 45: sudo dnf install wine, spec §8)"
            )
        return wine

    # --------------------------------------------------------------- status
    def com_map(self) -> dict[str, str]:
        """COM symlink map from <prefix>/dosdevices (comN -> device path)."""
        dd = self.prefix / "dosdevices"
        if not dd.is_dir():
            return {}
        out: dict[str, str] = {}
        for p in sorted(dd.iterdir()):
            if COM_RE.match(p.name) and p.is_symlink():
                out[p.name] = str(Path(os.readlink(p)))
        return out

    def status(self) -> dict:
        """Prefix/wine/COM/pid report; never raises for missing pieces."""
        table = _load_table(self.state_dir)
        running = {
            app_id: entry
            for app_id, entry in table["instances"].items()
            if sup.pid_alive(int(entry.get("pid", -1)))
        }
        apps = []
        for app_id in self.wine_apps:
            try:
                exe = exe_for(self.apps[app_id])
            except WinehubError:
                exe = ""
            apps.append(
                {
                    "id": app_id,
                    "exe": exe,
                    "running": app_id in running,
                    "pid": running[app_id]["pid"] if app_id in running else None,
                }
            )
        return {
            "prefix": self.prefix.is_dir(),
            "prefix_path": str(self.prefix),
            "wine": shutil.which("wine"),
            "com": self.com_map(),
            "apps": apps,
        }

    # ------------------------------------------------------- start and stop
    def start(self, app_id: str) -> dict:
        app = self.app(app_id)
        if not self._is_wine(app):
            raise WinehubError(
                f"{app_id}: not a wine-hub app — the supervisor owns its launch "
                "(gd launch); the wine hub owns the VARA family only (§6.4)"
            )
        exe = exe_for(app)
        table = _load_table(self.state_dir)
        entry = table["instances"].get(app_id)
        if entry and sup.pid_alive(int(entry.get("pid", -1))):
            return {"id": app_id, "state": "already-running", "pid": entry["pid"]}
        self._require_prefix()
        wine = self._require_wine()
        log_dir = self.state_dir / "logs" / "winehub"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{app_id}.log"
        env = dict(os.environ, WINEPREFIX=str(self.prefix))
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(
                [wine, exe],
                cwd=str(self.prefix),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        table["instances"][app_id] = {
            "pid": proc.pid,
            "pgid": proc.pid,
            "exe": exe,
            "started": _now(),
        }
        _save_table(self.state_dir, table)
        return {"id": app_id, "state": "running", "pid": proc.pid, "log": str(log_path)}

    def stop(self, app_id: str) -> dict:
        self.app(app_id)  # unknown ids refuse before any table surgery
        table = _load_table(self.state_dir)
        entry = table["instances"].get(app_id)
        if entry and not sup.pid_alive(int(entry.get("pid", -1))):
            del table["instances"][app_id]  # stale row: pid died on its own
            _save_table(self.state_dir, table)
            return {"id": app_id, "state": "stopped (stale entry removed)"}
        if not entry:
            raise WinehubError(f"{app_id}: not running under the wine hub (gd winehub status)")
        pid = int(entry["pid"])
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + TERM_GRACE_S
        while sup.pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if sup.pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        del table["instances"][app_id]
        _save_table(self.state_dir, table)
        return {"id": app_id, "state": "stopped"}

    # ---------------------------------------------------------- COM mapping
    def map_serial(self, com: str, device: str) -> dict:
        """Symlink dosdevices/comN -> device so the app sees a COM port."""
        com = com.lower()
        if not COM_RE.match(com):
            raise WinehubError(
                f"bad COM name {com!r} — expected com1..com32 (dosdevices convention)"
            )
        self._require_prefix()
        target = Path(device)
        if not target.exists():
            raise WinehubError(
                f"device {device} does not exist — plug it in or pass its real "
                "node (e.g. /dev/ttyUSB0; dialout group membership required, §6.7)"
            )
        dd = self.prefix / "dosdevices"
        dd.mkdir(parents=True, exist_ok=True)
        link = dd / com
        if link.is_symlink():
            if os.readlink(link) == str(target):
                return {"com": com, "device": str(target), "state": "already-mapped"}
            raise WinehubError(
                f"{com} already maps to {os.readlink(link)} — unmap it first "
                f"(gd winehub unmap {com[3:]})"
            )
        if link.exists():
            raise WinehubError(f"{link} exists and is not a symlink — refusing to overwrite")
        os.symlink(str(target), link)
        return {"com": com, "device": str(target), "state": "mapped"}

    def unmap(self, com: str) -> dict:
        com = com.lower()
        if not COM_RE.match(com):
            raise WinehubError(f"bad COM name {com!r} — expected com1..com32")
        link = self.prefix / "dosdevices" / com
        if not link.is_symlink():
            raise WinehubError(f"{com} is not mapped (gd winehub status)")
        target = os.readlink(link)
        os.unlink(link)
        return {"com": com, "device": target, "state": "unmapped"}
