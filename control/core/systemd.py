# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""systemd user-unit launch backend (spec §6.5, M4a).

Renders and manages systemd *user* units for supervised apps and session
bundles (docs/SPECIFICATION.md §6.3/§6.5):

    <user_unit_dir>/grid-down-<app>.service     one per supervisable app
    <user_unit_dir>/grid-down-<bundle>.target   one per bundle

Semantics are spec-literal: every service carries ``BindsTo=`` + ``After=``
on each bundle target that contains it, so stopping a target stops (ordered)
its members. Apps in several bundles are bound to all of them — stopping any
one target stops the shared app, which matches the direct backend's
stop_bundle behavior. ``hamlib`` (rigctld) is the suite-owned singleton
(§6.4): it is bound to *no* target and stays reference-counted by the shared
Supervisor logic. ``gd units install`` renders the whole set; Systemd is the
source of truth at runtime — this backend writes no supervisor.json entries.

wine-hub apps (Phase 3) and cli-tool apps get no units, exactly as the direct
backend refuses to daemonize them; targets simply omit them (with the same
operator-facing skip notes at launch time).

Validation without a user session: unit files are pure text (tested here),
`systemd-analyze verify` accepts them in the fedora:45 container, and the
tests drive `SystemdSupervisor` through a fake `systemctl` shim. Real
`systemctl --user` acceptance is the P2 Fedora GUI VM pass (spec §10).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:  # package import (gd CLI, pytest)
    from .supervisor import Supervisor, SupervisorError, RIG_ID
except ImportError:  # pragma: no cover - sibling import inside control/
    from supervisor import Supervisor, SupervisorError, RIG_ID  # type: ignore

UNIT_PREFIX = "grid-down-"
SKIP_VIAS = ("wine-hub", "cli-tool")  # no units, never daemonized (§6.5)


class SystemdError(RuntimeError):
    """systemctl/systemd backend failure with operator-facing text."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------ unit names
def service_name(app_id: str) -> str:
    return f"{UNIT_PREFIX}{app_id}.service"


def target_name(bundle: str) -> str:
    return f"{UNIT_PREFIX}{bundle}.target"


def app_id_from_unit(unit: str) -> str | None:
    if unit.startswith(UNIT_PREFIX) and unit.endswith(".service"):
        return unit[len(UNIT_PREFIX):-len(".service")]
    return None


# ----------------------------------------------------------- unit layout
def user_unit_dir() -> Path:
    """Where user units live ($XDG_CONFIG_HOME/systemd/user by default)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def bundles_for(app_id: str, bundles: dict) -> list[str]:
    """Names of bundles whose member list contains app_id (sorted)."""
    return sorted(name for name, b in bundles.items()
                  if app_id in b.get("members", []))


def target_members(members: list, apps: dict) -> list[str]:
    """Bundle members that get units: wine-hub/cli-tool are never daemonized."""
    return [m for m in members
            if apps.get(m, {}).get("launch", {}).get("via") not in SKIP_VIAS]


# --------------------------------------------------------- unit rendering
def service_unit_text(app: dict, bundles: dict, state_dir: Path, argv: list) -> str:
    """Render one app service. `argv` is the resolved launch command (the
    Supervisor's _resolve_cmd output) — the same command the direct backend
    would exec, quoted with shlex.join for systemd's ExecStart parser."""
    app_id = app["id"]
    log = Path(state_dir) / "logs" / f"{app_id}.log"
    lines = ["[Unit]",
             f"Description=Grid Down — {app.get('name') or app_id}"]
    if app_id != RIG_ID:  # hamlib: unbound singleton (§6.4) — reference-counted
        for name in bundles_for(app_id, bundles):
            lines += [f"BindsTo={target_name(name)}", f"After={target_name(name)}"]
    lines += ["",
              "[Service]",
              "Type=simple",
              f"ExecStart={shlex.join(str(a) for a in argv)}",
              f"StandardOutput=append:{log}",
              f"StandardError=append:{log}",
              "Restart=no",
              ""]
    return "\n".join(lines)


def target_unit_text(name: str, bundle: dict, members: list[str]) -> str:
    """Render one bundle target: pulls in its member services (Wants) and
    orders itself after them (After), so `systemctl --user start
    grid-down-<bundle>.target` brings the session up in one call."""
    lines = ["[Unit]", f"Description=Grid Down session bundle {name}"]
    desc = str(bundle.get("description") or "").strip()
    if desc:
        lines.append(f"# {desc}")
    if members:
        svcs = " ".join(service_name(m) for m in members)
        lines += [f"Wants={svcs}", f"After={svcs}"]
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------- install/uninstall
def installed_unit_paths(unit_dir: Path | None = None) -> list[Path]:
    """All grid-down-* unit files currently on disk (ours exclusively)."""
    d = Path(unit_dir) if unit_dir is not None else user_unit_dir()
    if not d.is_dir():
        return []
    out = sorted(d.glob(f"{UNIT_PREFIX}*.service")) + sorted(d.glob(f"{UNIT_PREFIX}*.target"))
    return sorted(out)


def expected_units(sup: Supervisor) -> dict[str, str]:
    """filename -> unit text for the whole registry/bundle set. Apps whose
    command cannot be resolved yet (missing binary, unresolved placeholders)
    are simply absent — install_units reports them as `unresolved`."""
    out: dict[str, str] = {}
    for app_id, app in sorted(sup.apps.items()):
        if app.get("launch", {}).get("via") in SKIP_VIAS:
            continue
        try:
            argv, _cwd = sup._resolve_cmd(app)
        except SupervisorError:
            continue
        out[service_name(app_id)] = service_unit_text(
            app, sup.bundles, sup.state_dir, argv)
    for name, bundle in sorted(sup.bundles.items()):
        members = target_members(list(bundle.get("members", [])), sup.apps)
        out[target_name(name)] = target_unit_text(name, bundle, members)
    return out


def units_current(sup: Supervisor, unit_dir: Path | None = None) -> bool:
    """True when the installed set exactly equals a fresh render of the
    registry + bundles (no drift, no stale leftovers)."""
    d = Path(unit_dir) if unit_dir is not None else user_unit_dir()
    want = expected_units(sup)
    for fname, text in want.items():
        p = d / fname
        if not p.is_file() or p.read_text() != text:
            return False
    have = {p.name for p in installed_unit_paths(d)}
    return have == set(want)


def install_units(sup: Supervisor, unit_dir: Path | None = None) -> dict:
    """Write the full unit set (deterministic render), then daemon-reload."""
    d = Path(unit_dir) if unit_dir is not None else user_unit_dir()
    d.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    written: list[str] = []
    unresolved: dict[str, str] = {}
    skipped: list[str] = []
    for app_id, app in sorted(sup.apps.items()):
        via = app.get("launch", {}).get("via")
        if via in SKIP_VIAS:
            skipped.append(app_id)
            continue
        try:
            argv, _cwd = sup._resolve_cmd(app)
        except SupervisorError as exc:
            unresolved[app_id] = str(exc)
            continue
        text = service_unit_text(app, sup.bundles, sup.state_dir, argv)
        (d / service_name(app_id)).write_text(text)
        written.append(service_name(app_id))
        installed.append(app_id)
    for name, bundle in sorted(sup.bundles.items()):
        members = target_members(list(bundle.get("members", [])), sup.apps)
        text = target_unit_text(name, bundle, members)
        (d / target_name(name)).write_text(text)
        written.append(target_name(name))
    return {"unit_dir": str(d),
            "installed": sorted(installed),
            "unresolved": dict(sorted(unresolved.items())),
            "skipped": sorted(skipped),
            "written": sorted(written),
            "reloaded": daemon_reload()}


def uninstall_units(unit_dir: Path | None = None) -> list[str]:
    """Remove every grid-down-* unit (the namespace is suite-owned) and
    daemon-reload. Returns the removed file names."""
    removed = [p.name for p in installed_unit_paths(unit_dir)]
    for p in installed_unit_paths(unit_dir):
        p.unlink()
    daemon_reload()
    return removed


# --------------------------------------------------------- systemctl glue
def _run(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["systemctl", "--user", *args],
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise SystemdError(
            "systemctl not found — the systemd backend needs a systemd host "
            "(Fedora 45 ships systemd; check your PATH)") from None
    except subprocess.TimeoutExpired:
        raise SystemdError(
            f"systemctl --user {' '.join(args)} timed out after {timeout:.0f}s") from None


def systemctl_user(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Run `systemctl --user <args>`; raise SystemdError on nonzero exit."""
    res = _run(*args, timeout=timeout)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip() or f"exit code {res.returncode}"
        raise SystemdError(f"systemctl --user {' '.join(args)} failed: {detail}")
    return res


def backend_available() -> tuple[bool, str]:
    """(usable, reason): systemctl present, a user-session bus reachable,
    systemd running (or degraded — still usable)."""
    if shutil.which("systemctl") is None:
        return False, "systemctl not found on PATH (not a systemd host?)"
    if not os.environ.get("XDG_RUNTIME_DIR"):
        return (False, "XDG_RUNTIME_DIR unset — no systemd user session "
                       "(log into a graphical/systemd session first)")
    try:
        res = _run("is-system-running")
    except SystemdError as exc:
        return False, str(exc)
    if res.returncode not in (0, 1):  # 0=running, 1=degraded (still usable)
        detail = (res.stderr or res.stdout).strip().splitlines()
        return False, detail[0] if detail else f"is-system-running exit {res.returncode}"
    return True, "ok"


def daemon_reload() -> bool:
    """Best-effort `systemctl --user daemon-reload`; False without a bus."""
    ok, _why = backend_available()
    if not ok:
        return False
    try:
        systemctl_user("daemon-reload")
        return True
    except SystemdError:
        return False


# ------------------------------------------------------- backend selection
def resolve_backend(profile: dict | None = None, flag: str | None = None) -> str:
    """Decide direct|systemd. --backend flag > profile supervisor.backend >
    'auto'. auto = systemd only when a user bus is reachable AND grid-down
    units are installed; otherwise direct (never fails). An explicit systemd
    request that cannot be honored raises with the fix in the message."""
    want = str(flag or ((profile or {}).get("supervisor", {}) or {}).get("backend")
               or "auto").strip().lower()
    if want not in ("auto", "direct", "systemd"):
        raise SupervisorError(
            f"unknown supervisor backend '{want}' — use auto|direct|systemd")
    if want == "direct":
        return "direct"
    if want == "systemd":
        ok, why = backend_available()
        if not ok:
            raise SupervisorError(f"systemd backend unavailable: {why}")
        if not installed_unit_paths():
            raise SupervisorError(
                "systemd backend unavailable: no grid-down units installed — "
                "install them with: gd units install")
        return "systemd"
    ok, _why = backend_available()
    return "systemd" if (ok and installed_unit_paths()) else "direct"


class SystemdSupervisor(Supervisor):
    """systemd user-unit backend behind the unchanged Supervisor API (§6.5).

    Differences from the direct backend: systemd owns instance truth (no
    supervisor.json instance writes); start/stop go through `systemctl
    --user`; a bundle start/stop is a single target-unit operation. Failed
    units surface as state "failed" (Doctor treats a service outliving all
    its bound targets as an error in M4b). Unit *contents* are fixed at
    `gd units install` time — re-run it after registry/profile changes
    (Doctor flags drift); launch still validates the current resolution so
    unknown binaries and placeholders fail exactly as in the direct backend.
    """

    BACKEND = "systemd"

    # -- queries ---------------------------------------------------------
    def _unit_states(self) -> dict[str, str]:
        """app_id -> running | failed | - (one list-units call)."""
        try:
            res = _run("list-units", "--all", f"{UNIT_PREFIX}*.service",
                       "--no-legend", "--plain")
        except SystemdError as exc:
            raise SupervisorError(str(exc)) from None
        states: dict[str, str] = {}
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            app_id = app_id_from_unit(parts[0])
            if app_id is None:
                continue
            active = parts[2]
            if active in ("active", "activating", "reloading"):
                states[app_id] = "running"
            elif active == "failed":
                states[app_id] = "failed"
            else:
                states[app_id] = "-"
        return states

    def _state_of(self, app_id: str) -> str:
        return self._unit_states().get(app_id, "-")

    def is_running(self, app_id: str) -> bool:
        return self._state_of(app_id) == "running"

    def running_ids(self) -> list[str]:
        states = self._unit_states()
        return sorted(a for a, s in states.items()
                      if s == "running" and a in self.apps)

    def stale_ids(self) -> list[str]:
        """Failed units are the systemd analogue of the direct backend's
        stale entries (operator attention / stop clears them)."""
        states = self._unit_states()
        return sorted(a for a, s in states.items()
                      if s == "failed" and a in self.apps)

    def _main_pid(self, app_id: str) -> int | None:
        try:
            res = systemctl_user("show", service_name(app_id),
                                 "--property=MainPID", "--value")
        except SystemdError as exc:
            raise SupervisorError(str(exc)) from None
        val = res.stdout.strip().splitlines()[0] if res.stdout.strip() else "0"
        return int(val) if val.isdigit() else None

    # -- lifecycle -------------------------------------------------------
    def launch(self, app_id: str, bundle: str | None = None) -> dict:
        app = self.app(app_id)
        if self.is_running(app_id):
            return {"id": app_id, "state": "already-running",
                    "pid": self._main_pid(app_id)}
        if self.is_rig_dependent(app_id):
            self._ensure_rig(bundle=bundle)
        entry = self._spawn(app, bundle)
        return {"id": app_id, "state": "running",
                "pid": entry["pid"], "log": entry["log"]}

    def _spawn(self, app: dict, bundle: str | None) -> dict:
        app_id = app["id"]
        argv, _cwd = self._resolve_cmd(app)  # validation; systemd runs the installed unit
        del argv
        log = str(self.state_dir / "logs" / f"{app_id}.log")
        try:
            systemctl_user("start", service_name(app_id))
        except SystemdError as exc:
            raise SupervisorError(f"{app_id}: {exc}") from None
        return {"pid": self._main_pid(app_id), "pgid": None,
                "started": _now(), "bundle": bundle, "log": log}

    def launch_bundle(self, name: str) -> dict:
        bundle = self.bundles.get(name)
        if bundle is None:
            known = ", ".join(sorted(self.bundles)) or "none"
            raise SupervisorError(f"unknown bundle: {name} (known: {known})")
        notes: list[str] = []
        members = list(bundle.get("members", []))
        for app_id in members:
            via = self.app(app_id).get("launch", {}).get("via")
            if via in SKIP_VIAS:
                notes.append(f"skip {app_id} ({via}: managed elsewhere / interactive)")
        if RIG_ID in members:
            res = self.launch(RIG_ID, bundle=name)  # rig first, unbound singleton (§6.4)
            notes.append(f"{res['state']}: {RIG_ID}")
        target = target_name(name)
        try:
            systemctl_user("start", target)
        except SystemdError as exc:
            raise SupervisorError(f"bundle {name}: {exc}") from None
        up = sorted(set(self.running_ids()) & (set(members) - {RIG_ID}))
        notes.append(f"target {target} active — {len(up)} unit(s) running: "
                     f"{', '.join(up) if up else '-'}")
        return {"bundle": name, "notes": notes}

    def stop(self, app_id: str, force: bool = False) -> dict:
        self.app(app_id)
        state = self._state_of(app_id)
        if state == "failed":  # stop clears a failed unit (stale-entry parity)
            try:
                systemctl_user("stop", service_name(app_id))
            except SystemdError as exc:
                raise SupervisorError(f"{app_id}: {exc}") from None
            return {"id": app_id, "state": "failed-cleared", "notes": []}
        if state != "running":
            raise SupervisorError(f"{app_id}: not running")
        if app_id == RIG_ID and not force:
            blockers = [a for a in self.running_ids() if self.is_rig_dependent(a)]
            if blockers:
                raise SupervisorError(
                    f"refusing to stop {RIG_ID}: rig-dependent apps still running "
                    f"({', '.join(blockers)}) — stop them first or use --force (spec §6.4)")
        try:
            systemctl_user("stop", service_name(app_id))
        except SystemdError as exc:
            raise SupervisorError(f"{app_id}: {exc}") from None
        return {"id": app_id, "state": "stopped", "notes": []}

    def stop_bundle(self, name: str, force: bool = False) -> dict:
        bundle = self.bundles.get(name)
        if bundle is None:
            known = ", ".join(sorted(self.bundles)) or "none"
            raise SupervisorError(f"unknown bundle: {name} (known: {known})")
        members = set(bundle.get("members", []))
        target = target_name(name)
        try:
            systemctl_user("stop", target)
        except SystemdError as exc:
            raise SupervisorError(f"bundle {name}: {exc}") from None
        notes = [f"target {target} stopped"]
        if RIG_ID in members and self.is_running(RIG_ID):
            others = [a for a in self.running_ids()
                      if self.is_rig_dependent(a) and a not in members]
            if others:
                notes.append(f"{RIG_ID} left running (still used by: {', '.join(others)})")
            else:
                self.stop(RIG_ID, force=force)
                notes.append(f"stopped: {RIG_ID}")
        return {"bundle": name, "notes": notes}

    # -- status ----------------------------------------------------------
    def status(self, include_stopped: bool = False) -> dict:
        states = self._unit_states()
        rows = []
        for app_id in sorted(self.apps):
            s = states.get(app_id, "-")
            if s == "-" and not include_stopped:
                continue
            rows.append({
                "id": app_id,
                "state": s,
                "pid": self._main_pid(app_id) if s == "running" else None,
                "started": None,
                "bundle": None,
                "via": self.apps[app_id].get("launch", {}).get("via", "?"),
                "log": str(self.state_dir / "logs" / f"{app_id}.log")
                       if s == "running" else None,
            })
        bundles = {}
        for name, bundle in self.bundles.items():
            members = bundle.get("members", [])
            bundles[name] = {"running": [m for m in members
                                         if states.get(m) == "running"],
                             "total": len(members)}
        return {"apps": rows,
                "bundles": bundles,
                "updated": _now(),
                "backend": self.BACKEND,
                "state_file": str(self.state_file()),
                "unit_dir": str(user_unit_dir())}




