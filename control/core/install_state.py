# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Install-state probe — "is this app installed on this box?" (spec §7, M5).

One state word + a detail line per registry app, computed from cheap local
signals (no root, no network):

- dnf / dnf-verify   one batched ``rpm -q pkg…`` (query needs no root);
                     installed | partial | missing, detail names gaps
- build / tarball /  launch.cmd binary on PATH (shutil.which);
  pip-vendored       installed (detail = resolved path) | missing
- static             <app_root>/<id> docroot present (supervisor
                     convention, §6.6) | missing
- wine               shared prefix present → ``prefix`` (per-app exe
                     probes are Phase 3); prefix absent → missing
- anything else      ``unknown`` (prose cmds, no recognizable signal)

``rpm`` missing or too slow ⇒ ``unknown`` with the reason — this module
never raises for foreseeable conditions, so both the CLI and the GUI can
render the honest state on any box.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

try:  # package import (gd CLI, pytest)
    from . import winehub
except ImportError:  # pragma: no cover - sibling import inside control/
    import winehub  # type: ignore

RPM_TIMEOUT_S = 15
DNF_STRATEGIES = ("dnf", "dnf-verify")
WHICH_STRATEGIES = ("build", "tarball", "pip-vendored")
PROSE_RE = re.compile(r"\([^)]*\)")
PLACEHOLDER_RE = re.compile(r"<[a-z_]+>")
NOT_INSTALLED_RE = re.compile(r"^package (.+) is not installed")


def app_root() -> Path:
    env = os.environ.get("GRID_DOWN_APP_ROOT")
    if env:
        return Path(env)
    return Path("/opt/grid-down")


def _cmd_binary(app: dict) -> str | None:
    """First real token of launch.cmd (prose + <placeholders> dropped)."""
    cmd = str((app.get("launch") or {}).get("cmd") or "")
    for tok in cmd.split():
        if PROSE_RE.fullmatch(tok) or PLACEHOLDER_RE.fullmatch(tok):
            continue
        return tok
    return None


def _rpm_installed(pkgs: list[str], rpm_bin: str) -> dict[str, bool] | None:
    """One batched `rpm -q` over every dnf package.

    Returns {pkg: installed} or None when rpm itself is unavailable/too
    slow (those become `unknown`, not errors). rpm answers in request
    order, so found lines are matched positionally against the
    not-yet-resolved packages (names may contain dashes — python3-hamlib).
    """
    if not pkgs:
        return {}
    try:
        proc = subprocess.run(
            [rpm_bin, "-q", *pkgs],
            capture_output=True,
            text=True,
            timeout=RPM_TIMEOUT_S,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    res: dict[str, bool] = {}
    idx = 0
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        m = NOT_INSTALLED_RE.match(line)
        if m:
            res[m.group(1)] = False
            continue
        while idx < len(pkgs) and pkgs[idx] in res:
            idx += 1
        if idx < len(pkgs):
            res[pkgs[idx]] = True
            idx += 1
    return res


def app_states(
    apps: list[dict],
    app_root_path: Path | str | None = None,
    rpm_bin: str = "rpm",
) -> dict[str, dict[str, str]]:
    """{app_id: {"state": …, "detail": …}} for every registry app.

    State vocabulary: installed | partial | missing | prefix | unknown.
    Never raises for foreseeable conditions (rpm unavailable ⇒ unknown).
    """
    root = Path(app_root_path) if app_root_path is not None else app_root()
    dnf_pkgs: dict[str, list[str]] = {}
    for app in apps:
        if not (app or {}).get("id"):
            continue
        install = app.get("install") or {}
        if str(install.get("strategy") or "") in DNF_STRATEGIES:
            pkgs = [str(p) for p in install.get("packages") or []]
            if pkgs:
                dnf_pkgs[str(app["id"])] = pkgs
    rpm_res = _rpm_installed([p for pkgs in dnf_pkgs.values() for p in pkgs], rpm_bin)

    out: dict[str, dict[str, str]] = {}
    for app in apps:
        if not (app or {}).get("id"):
            continue  # malformed entry: no row rather than a raise
        app_id = str(app["id"])
        strategy = str((app.get("install") or {}).get("strategy") or "")
        try:
            out[app_id] = _probe(app, app_id, strategy, dnf_pkgs, rpm_res, root)
        except OSError as exc:  # e.g. unreadable docroot parent — degrade, never raise
            out[app_id] = {"state": "unknown", "detail": f"probe failed: {exc}"}
    return out


def _probe(
    app: dict,
    app_id: str,
    strategy: str,
    dnf_pkgs: dict[str, list[str]],
    rpm_res: dict[str, bool] | None,
    root: Path,
) -> dict[str, str]:
    """One app's install state; only OSError is allowed to escape (degraded)."""
    if app_id in dnf_pkgs:
        pkgs = dnf_pkgs[app_id]
        if rpm_res is None:
            return {
                "state": "unknown",
                "detail": f"rpm not available here (strategy {strategy}: {', '.join(pkgs)})",
            }
        hits = [p for p in pkgs if rpm_res.get(p)]
        gaps = [p for p in pkgs if not rpm_res.get(p)]
        if not hits:
            return {"state": "missing", "detail": f"not installed: {', '.join(pkgs)}"}
        if gaps:
            return {"state": "partial", "detail": f"missing packages: {', '.join(gaps)}"}
        return {"state": "installed", "detail": f"packages: {', '.join(pkgs)}"}
    if strategy in WHICH_STRATEGIES:
        binary = _cmd_binary(app)
        if binary is None:
            return {"state": "unknown", "detail": f"no launch.cmd binary (strategy {strategy})"}
        found = shutil.which(binary)
        if found:
            return {"state": "installed", "detail": found}
        return {"state": "missing", "detail": f"{binary} not on PATH"}
    if strategy == "static":
        docroot = root / app_id
        if docroot.is_dir():
            return {"state": "installed", "detail": f"docroot {docroot}"}
        return {
            "state": "missing",
            "detail": f"no docroot at {docroot} (kit install: bootstrap.sh --kit)",
        }
    if strategy == "wine":
        if winehub.WINE_PREFIX.is_dir():
            return {
                "state": "prefix",
                "detail": f"shared prefix {winehub.WINE_PREFIX} (per-app probes: Phase 3)",
            }
        return {
            "state": "missing",
            "detail": f"prefix {winehub.WINE_PREFIX} absent (kit postinst creates it)",
        }
    return {"state": "unknown", "detail": f"strategy {strategy!r} has no local probe"}
