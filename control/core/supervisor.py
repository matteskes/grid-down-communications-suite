# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Process supervisor for Grid Down (direct backend + backend-neutral API).

Launches, monitors, and tears down app processes and localhost services
(docs/SPECIFICATION.md §6.5). This module is the direct process-group backend
(JSON state file so separate `gd` invocations share one view of reality) and
defines the backend-neutral `Supervisor` API both the CLI and the GUI program
against. The systemd-user-unit backend (M4, spec §6.5) subclasses it in
core/systemd.py — unit rendering, `systemctl --user` lifecycle, backend
resolution; GUI/CLI callables are unchanged by the backend choice.

Suite rules honored (spec §6.4, G4):
- `hamlib` (rigctld, 127.0.0.1:4532) is the suite-owned rig singleton. It is
  started automatically before any rig-dependent app and stopped only when no
  rig-dependent app is running. Rig-dependency is data-driven: an app is
  rig-dependent when it is a member of any bundle that includes `hamlib`
  (control/registry/bundles.yaml, §6.3).
- wine-hub apps (VARA family) are owned by `gd winehub` (Phase 3) — refused here.
- cli-tool apps are interactive; the supervisor refuses to daemonize them.
- local-service apps are served by the suite on the §6.6 loopback ports from
  /opt/grid-down/<id>.

State layout (override dir with $GRID_DOWN_STATE_DIR):
    <state>/supervisor.json   instance table (single-writer; atomic writes)
    <state>/logs/<app>.log    per-app stdout+stderr capture

Known limitations (M1): no port-ready wait after spawn (Doctor reports port
state), PID reuse is not detected, concurrent writers are last-write-wins.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:  # package import (gd CLI, pytest)
    from . import audio
except ImportError:  # pragma: no cover - sibling import inside control/
    import audio  # type: ignore

RIG_ID = "hamlib"      # suite-owned rigctld singleton (spec §6.4)
TERM_GRACE_S = 5.0     # SIGTERM → SIGKILL escalation window
STATE_VERSION = 1

# Loopback services owned by the suite (spec §6.6)
PORTS = {
    "hamlib": 4532,
    "direwolf": 8001,
    "commstatone": 8090,
    "js8spotter": 8091,
    "meshtastic-web": 8092,
    "ardop": 8515,
}


class SupervisorError(RuntimeError):
    """Launch/stop/precondition failure with operator-facing text."""


def default_state_dir() -> Path:
    env = os.environ.get("GRID_DOWN_STATE_DIR")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(xdg) / "grid-down"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pid_alive(pid: int) -> bool:
    """True when pid names a live process (cross-platform, no /proc needed)."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


class Supervisor:
    """Direct-backend supervisor over the app registry (spec §6.1/§6.5)."""

    BACKEND = "direct"  # reported in status(); SystemdSupervisor overrides

    def __init__(self, apps: list[dict], bundles: dict | None = None,
                 state_dir: Path | None = None, app_root: Path | None = None,
                 profile: dict | None = None):
        self.apps: dict[str, dict] = {a["id"]: a for a in apps}
        self.bundles: dict = bundles or {}
        self.state_dir = Path(state_dir) if state_dir else default_state_dir()
        root = app_root or os.environ.get("GRID_DOWN_APP_ROOT") or "/opt/grid-down"
        self.app_root = Path(root).expanduser()
        self.profile = profile or {}   # station profile (rig + audio.presets, §4)
        self._rig_dependents = self._compute_rig_dependents()
        self.state = self._load_state()

    # ------------------------------------------------------------- rig set
    def _compute_rig_dependents(self) -> set[str]:
        deps: set[str] = set()
        for bundle in self.bundles.values():
            members = set(bundle.get("members", []))
            if RIG_ID in members:
                deps |= members
        deps.discard(RIG_ID)
        return deps

    def is_rig_dependent(self, app_id: str) -> bool:
        return app_id in self._rig_dependents

    # --------------------------------------------------------------- state
    def state_file(self) -> Path:
        return self.state_dir / "supervisor.json"

    def _load_state(self) -> dict:
        path = self.state_file()
        if not path.exists():
            return {"version": STATE_VERSION, "instances": {}}
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"version": STATE_VERSION, "instances": {}}
        data.setdefault("instances", {})
        return data

    def _save_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state["updated"] = _now()
        tmp = self.state_file().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=2) + "\n")
        os.replace(tmp, self.state_file())  # atomic (adapter convention, §6.2)

    def _entry(self, app_id: str) -> dict | None:
        entry = self.state["instances"].get(app_id)
        if entry and pid_alive(entry.get("pid", -1)):
            return entry
        return None

    # ---------------------------------------------------------------- info
    def app(self, app_id: str) -> dict:
        try:
            return self.apps[app_id]
        except KeyError:
            known = ", ".join(sorted(self.apps))
            raise SupervisorError(f"unknown app id: {app_id} (registered: {known})") from None

    def running_ids(self) -> list[str]:
        return sorted(a for a in self.state["instances"] if self._entry(a))

    def stale_ids(self) -> list[str]:
        return sorted(a for a, e in self.state["instances"].items()
                      if not pid_alive(e.get("pid", -1)))

    def is_running(self, app_id: str) -> bool:
        return self._entry(app_id) is not None

    # -------------------------------------------------------------- launch
    def launch(self, app_id: str, bundle: str | None = None) -> dict:
        app = self.app(app_id)
        entry = self._entry(app_id)
        if entry:
            return {"id": app_id, "state": "already-running", "pid": entry["pid"]}
        if self.is_rig_dependent(app_id):
            self._ensure_rig(bundle=bundle)  # rig singleton before any radio app (§6.4)
        entry = self._spawn(app, bundle)
        self.state["instances"][app_id] = entry
        self._save_state()
        return {"id": app_id, "state": "running", "pid": entry["pid"], "log": entry["log"]}

    def launch_bundle(self, name: str) -> dict:
        bundle = self.bundles.get(name)
        if bundle is None:
            known = ", ".join(sorted(self.bundles))
            raise SupervisorError(f"unknown bundle: {name} (known: {known})")
        notes: list[str] = []
        for app_id in bundle.get("members", []):
            via = self.app(app_id).get("launch", {}).get("via")
            if via in ("wine-hub", "cli-tool"):
                notes.append(f"skip {app_id} ({via}: managed elsewhere / interactive)")
                continue
            res = self.launch(app_id, bundle=name)
            notes.append(f"{res['state']}: {app_id}"
                         + (f" (pid {res['pid']})" if res.get("pid") else ""))
        return {"bundle": name, "notes": notes}

    def _ensure_rig(self, bundle: str | None) -> None:
        if self.is_running(RIG_ID):
            return
        if RIG_ID not in self.apps:
            raise SupervisorError(
                f"{RIG_ID} (rigctld) is not in the registry — refusing to launch a "
                "radio app without rig control (spec §6.4)")
        self.launch(RIG_ID, bundle=bundle)

    def rig_argv(self) -> tuple[list[str], list[str]]:
        """rigctld argv from the station profile (spec §4 rig:, §6.4/§6.6).

        Returns (argv, notes). The binary name comes from the registry entry
        (first token of hamlib launch.cmd); every flag is profile-derived so
        rigctld remains the only serial owner (G4). Empty rig.model falls back
        to the hamlib Dummy backend (id 1) with an explicit note — safe on a
        bench, wrong in the field (Doctor warns). Non-numeric models are
        refused: rigctld takes only the numeric id column of `rigctl -l`.
        """
        reg_cmd = (self.apps.get(RIG_ID, {}).get("launch", {}) or {}).get("cmd") or ""
        tokens = shlex.split(reg_cmd)
        argv0 = os.path.expandvars(os.path.expanduser(tokens[0])) if tokens else "rigctld"
        rig = (self.profile.get("rig", {}) or {}) if self.profile else {}
        argv = [argv0, "-T", "127.0.0.1", "-t", str(PORTS[RIG_ID]), "-v"]
        notes: list[str] = []
        model = str(rig.get("model") or "").strip()
        if not model:
            argv += ["-m", "1"]
            notes.append("rig.model unset — hamlib Dummy backend (loopback-safe, "
                         "no radio); set rig.model to the numeric id from "
                         "`rigctl -l` for field use")
        elif model.isdigit():
            argv += ["-m", model]
        else:
            raise SupervisorError(
                f"rig.model {model!r} is not a numeric hamlib id — rigctld accepts "
                "the id column of `rigctl -l` (e.g. 3073); leave rig.model empty "
                "for the Dummy backend")
        if rig.get("path"):
            argv += ["-r", str(rig["path"])]
        if rig.get("baud"):
            argv += ["-s", str(rig["baud"])]
        return argv, notes

    def _spawn(self, app: dict, bundle: str | None) -> dict:
        argv, cwd = self._resolve_cmd(app)
        log = self.state_dir / "logs" / f"{app['id']}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as fh:
            fh.write(f"\n=== {_now()} gd launch: {shlex.join(argv)}\n")
            fh.flush()
            try:
                proc = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                    start_new_session=True, cwd=str(cwd) if cwd else None)
            except FileNotFoundError as exc:
                raise SupervisorError(
                    f"{app['id']}: executable not found ({exc.filename}) — is the app "
                    "installed? air-gap install: bootstrap.sh --kit <dir>") from None
            except PermissionError as exc:
                raise SupervisorError(f"{app['id']}: {argv[0]} is not executable") from None
        # fast-fail: apps with bad args usually die instantly
        time.sleep(0.3)
        rc = proc.poll()
        if rc is not None:
            self._reap(proc.pid)
            raise SupervisorError(f"{app['id']}: exited immediately (rc={rc}) — see {log}")
        return {"pid": proc.pid, "pgid": proc.pid, "argv": argv,
                "via": app.get("launch", {}).get("via", "?"),
                "bundle": bundle, "started": _now(), "log": str(log)}

    def _resolve_cmd(self, app: dict) -> tuple[list[str], Path | None]:
        app_id = app["id"]
        launch = app.get("launch", {})
        via = launch.get("via")
        cmd = (launch.get("cmd") or "").strip()
        if app_id == RIG_ID:  # rig flags are profile-built (spec §4), not registry
            return self.rig_argv()[0], None
        if via == "wine-hub":
            raise SupervisorError(f"{app_id}: wine-hub apps are managed by 'gd winehub' (Phase 3)")
        if via == "cli-tool":
            raise SupervisorError(f"{app_id}: interactive CLI tool — run it in your terminal; "
                                  "the supervisor manages services and desktop apps")
        if via == "local-service":
            return self._local_service_cmd(app)
        if not cmd:
            raise SupervisorError(f"{app_id}: registry entry has no launch.cmd")
        # drop prose parentheticals like "(served by suite)"
        raw = [t for t in shlex.split(cmd) if not re.fullmatch(r"\([^)]*\)", t)]
        if not raw:
            raise SupervisorError(f"{app_id}: launch.cmd is only prose: {cmd!r}")
        tokens = []
        for t in raw:  # <placeholders> resolve through audio.presets (spec §6.6)
            m = re.fullmatch(r"<([a-z_]+)>", t)
            if m:
                try:
                    tokens.append(audio.resolve_preset(app_id, m.group(1), self.profile))
                except audio.AudioError as exc:
                    raise SupervisorError(f"{app_id}: {exc}") from None
            else:
                tokens.append(t)
        if any(re.search(r"[<>\[\]{}]", t) for t in tokens):
            raise SupervisorError(f"{app_id}: launch.cmd has placeholders ({cmd}) — needs "
                                  "profile/audio wiring (Phase 3) before it can be supervised")
        argv = [os.path.expandvars(os.path.expanduser(t)) for t in tokens]
        if not shutil.which(argv[0]) and not Path(argv[0]).is_file():
            raise SupervisorError(f"{app_id}: executable not found: {argv[0]} — is the app "
                                  "installed? air-gap install: bootstrap.sh --kit <dir>")
        return argv, None

    def _local_service_cmd(self, app: dict) -> tuple[list[str], Path]:
        app_id = app["id"]
        port = PORTS.get(app_id)
        if port is None:
            raise SupervisorError(f"{app_id}: local-service with no port assigned (spec §6.6)")
        docroot = self.app_root / app_id
        if not docroot.is_dir():
            raise SupervisorError(f"{app_id}: content not installed (expected {docroot}); "
                                  "serving is available after kit install (bootstrap.sh --kit)")
        if app_id == "js8spotter":  # PHP app (spec §6.6)
            php = shutil.which("php")
            if php is None:
                raise SupervisorError("js8spotter needs php-cli on PATH (dnf install php-cli)")
            return [php, "-S", f"127.0.0.1:{port}", "-t", str(docroot)], docroot
        return ([sys.executable, "-m", "http.server", str(port),
                 "--bind", "127.0.0.1", "--directory", str(docroot)], docroot)

    # ---------------------------------------------------------------- stop
    def stop(self, app_id: str, force: bool = False) -> dict:
        self.app(app_id)  # validate id (error text lists registered apps)
        entry = self.state["instances"].get(app_id)
        if entry is None:
            raise SupervisorError(f"{app_id}: not running")
        if not pid_alive(entry.get("pid", -1)):
            del self.state["instances"][app_id]
            self._save_state()
            return {"id": app_id, "state": "stale-cleared", "notes": []}
        if app_id == RIG_ID and not force:
            blockers = [a for a in self.running_ids() if self.is_rig_dependent(a)]
            if blockers:
                raise SupervisorError(
                    f"refusing to stop {RIG_ID}: rig-dependent apps still running "
                    f"({', '.join(blockers)}) — stop them first or use --force (spec §6.4)")
        self._kill_group(entry)
        del self.state["instances"][app_id]
        self._save_state()
        return {"id": app_id, "state": "stopped", "notes": []}

    def _kill_group(self, entry: dict) -> None:
        pid = entry["pid"]
        pgid = entry.get("pgid", pid)
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.monotonic() + TERM_GRACE_S
        while time.monotonic() < deadline and pid_alive(pid):
            time.sleep(0.1)
        if pid_alive(pid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self._reap(pid)

    def _reap(self, pid: int) -> None:
        """Reap if the child belongs to this process; otherwise init reaps it."""
        for _ in range(10):
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return  # not our child (launched by a previous gd invocation)
            if wpid == pid:
                return
            time.sleep(0.05)

    def stop_bundle(self, name: str, force: bool = False) -> dict:
        bundle = self.bundles.get(name)
        if bundle is None:
            known = ", ".join(sorted(self.bundles))
            raise SupervisorError(f"unknown bundle: {name} (known: {known})")
        members = bundle.get("members", [])
        notes: list[str] = []
        errors: list[str] = []
        for app_id in reversed(members):
            if app_id == RIG_ID:
                continue  # handled last — singleton (spec §6.4)
            if app_id not in self.state["instances"]:
                continue
            via = self.app(app_id).get("launch", {}).get("via")
            if via == "wine-hub":
                notes.append(f"skip {app_id} (wine-hub: use 'gd winehub', Phase 3)")
                continue
            try:
                res = self.stop(app_id, force=force)
                notes.append(f"{res['state']}: {app_id}")
            except SupervisorError as exc:
                errors.append(str(exc))
        if RIG_ID in members and self.is_running(RIG_ID):
            others = [a for a in self.running_ids()
                      if self.is_rig_dependent(a) and a not in members]
            if others:
                notes.append(f"{RIG_ID} left running (still used by: {', '.join(others)})")
            else:
                self.stop(RIG_ID, force=force)
                notes.append(f"stopped: {RIG_ID}")
        if errors:
            raise SupervisorError("; ".join(errors))
        return {"bundle": name, "notes": notes}

    def stop_all(self, force: bool = False) -> dict:
        notes: list[str] = []
        errors: list[str] = []
        for app_id in self.running_ids():
            if app_id == RIG_ID:
                continue  # stopped last
            try:
                res = self.stop(app_id, force=force)
                notes.append(f"{res['state']}: {app_id}")
            except SupervisorError as exc:
                errors.append(str(exc))
        if self.is_running(RIG_ID):
            self.stop(RIG_ID, force=force)  # dependents are down; guard cannot trip
            notes.append(f"stopped: {RIG_ID}")
        if errors:
            raise SupervisorError("; ".join(errors))
        return {"bundle": "all", "notes": notes}

    # --------------------------------------------------------------- status
    def status(self, include_stopped: bool = False) -> dict:
        rows = []
        for app_id in sorted(self.apps):
            entry = self.state["instances"].get(app_id)
            if entry and pid_alive(entry.get("pid", -1)):
                state = "running"
            elif entry:
                state = "stale"
            else:
                state = "-"
            if state == "-" and not include_stopped:
                continue
            rows.append({
                "id": app_id, "state": state,
                "pid": entry.get("pid") if entry else None,
                "started": entry.get("started") if entry else None,
                "bundle": entry.get("bundle") if entry else None,
                "via": self.apps[app_id].get("launch", {}).get("via", "?"),
                "log": entry.get("log") if entry else None,
            })
        bundles = {}
        for name, bundle in self.bundles.items():
            members = bundle.get("members", [])
            bundles[name] = {"running": [m for m in members if self.is_running(m)],
                             "total": len(members)}
        return {"apps": rows, "bundles": bundles, "updated": self.state.get("updated"),
                "backend": self.BACKEND, "state_file": str(self.state_file())}