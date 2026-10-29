# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Wine hub — shared prefix + start/stop/COM mapping for the VARA family (§6.4).

One Wine prefix at ``/opt/grid-down/wine-prefix`` serves every wine-hub app
(VARA HF/FM/Sat/Chat/Term, VarAC). The supervisor refuses to daemonize these
apps (spec §6.5); this module owns their lifecycle instead:

- ``status()``             prefix presence, wine binary, COM device map, pids,
                           per-app install probe
- ``start(app_id)``        detached ``wine <exe>`` spawn + pid table
- ``stop(app_id)``         SIGTERM → SIGKILL teardown, table cleanup
- ``map_serial``/``unmap`` ``dosdevices/comN`` symlinks so Windows apps see
                           a COM port for a real serial device (§6.4)
- ``init_prefix()``        ``wineboot -i`` for the dev path (the kit postinst
                           builds the prefix from the cached tarball, §5)
- ``install(app_id)``      silent install of the user-supplied VARA installer
                           (.exe, or the .zip wrapping it) from
                           ``install/kit-import/`` + install probe; recipe
                           ``wine.installer:`` picks the mode — ``setup``
                           (Inno ``/VERYSILENT``, the VARA family) or
                           ``portable`` (a plain zip unpacked into
                           ``wine.install_dir`` — VarAC ships no installer)

The prefix itself is created by the kit postinst (§5) from the cached prefix
tarball — or by ``init_prefix()`` on a dev box; without it every verb that
needs it refuses cleanly naming the fix. Licenses are applied in-app by the
operator (registration is per program and user-supplied, G6); unregistered
installs stay fully functional with VARA's trial delay, which is all the P3
integration path needs (§10).

Installers never leave ``install/kit-import/`` (G6): ``install()`` executes
the user-supplied artifact in place — never copied into the repo or a kit.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import zipfile
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
# Recipe + staging dirs (repo-relative so the control tree finds them anywhere;
# kit-import overridable for dev/VM layouts).
RECIPES_DIR = Path(__file__).resolve().parents[2] / "install" / "recipes"
KIT_IMPORT = Path(
    os.environ.get("GRID_DOWN_KIT_IMPORT")
    or Path(__file__).resolve().parents[2] / "install" / "kit-import"
)
# The VARA installers are Inno Setup packages; these switches install silently.
# WINEDLLOVERRIDES skips the wine-mono/gecko download dialogs on first run.
SILENT_INSTALL_FLAGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
WINE_SUPPRESS_ENV = {"WINEDLLOVERRIDES": "mscoree,mshtml=", "WINEDEBUG": "-all"}
INIT_TIMEOUT_S = 600
INSTALL_TIMEOUT_S = 900


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


def load_wine_recipe(app_id: str, recipes_dir: Path | None = None) -> dict:
    """The ``recipe.wine`` block of an app's install recipe, {} when absent.

    Recipes are the declared install strategy — ``installer_glob`` and the
    ``exes`` candidates live there (single source). A missing/unreadable
    recipe degrades to {} (probes fall back to launch.cmd's .exe) instead of
    breaking ``status`` on any box.
    """
    d = Path(recipes_dir) if recipes_dir else RECIPES_DIR
    path = d / f"{app_id}.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml  # PyYAML is a runtime dep (§3), same as the registry loader
    except ImportError:  # pragma: no cover
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    wine = (data.get("recipe") or {}).get("wine")
    return dict(wine) if isinstance(wine, dict) else {}


def candidate_exes(app: dict, recipes_dir: Path | None = None) -> list[str]:
    """Windows executables to probe for, recipe ``wine.exes`` first.

    Upstream names drift across VARA versions (VARA FM has shipped FM.exe and
    VARAFM.exe), so recipes may list candidates; launch.cmd's .exe token is
    always the last resort. Order preserved, case-insensitively deduplicated.
    """
    recipe = load_wine_recipe(str(app.get("id") or ""), recipes_dir)
    names = [str(n) for n in recipe.get("exes") or []]
    try:
        names.append(exe_for(app))
    except WinehubError:
        pass
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def find_exe(prefix: Path, name: str) -> list[Path]:
    """Files under ``<prefix>/drive_c`` named `name` (case-insensitive).

    Install directories vary per program and version (C:\\VARA, C:\\VARA HF,
    C:\\Program Files\\…), so the probe matches the basename anywhere on the
    C: drive instead of pinning a path.
    """
    drive_c = prefix / "drive_c"
    if not drive_c.is_dir():
        return []
    want = name.lower()
    return sorted(p for p in drive_c.rglob("*") if p.name.lower() == want and p.is_file())


class WineHub:
    """Lifecycle + COM mapping over the shared VARA wine prefix (§6.4)."""

    def __init__(
        self,
        apps: list[dict],
        state_dir: Path | None = None,
        prefix: Path | None = None,
        recipes_dir: Path | None = None,
        kit_import: Path | None = None,
    ):
        self.apps: dict[str, dict] = {a["id"]: a for a in apps}
        self.wine_apps = sorted(i for i, a in self.apps.items() if self._is_wine(a))
        self.state_dir = Path(state_dir) if state_dir else sup.default_state_dir()
        # GRID_DOWN_WINE_PREFIX (P3b): rehearsals on a bench without wine point
        # the default prefix at a fixture directory — map/unmap/status work
        # unchanged there; an explicit prefix= still wins over the env.
        self.prefix = (
            Path(prefix) if prefix else Path(os.environ.get("GRID_DOWN_WINE_PREFIX") or WINE_PREFIX)
        )
        self.recipes_dir = Path(recipes_dir) if recipes_dir else RECIPES_DIR
        self.kit_import = Path(kit_import) if kit_import else KIT_IMPORT

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

    # --------------------------------------------------------------- probes
    def _exe_index(self) -> dict[str, list[Path]]:
        """One walk of drive_c: {lower-cased exe name: [paths]} for probes."""
        drive_c = self.prefix / "drive_c"
        idx: dict[str, list[Path]] = {}
        if not drive_c.is_dir():
            return idx
        for p in drive_c.rglob("*"):
            if p.name.lower().endswith(".exe") and p.is_file():
                idx.setdefault(p.name.lower(), []).append(p)
        for paths in idx.values():
            paths.sort()
        return idx

    def probe(self, app_id: str) -> dict:
        """Install probe for one wine-hub app over the shared prefix.

        ``installed`` = some candidate .exe (recipe ``wine.exes`` first,
        launch.cmd's token last) exists under ``drive_c``. Never raises for a
        missing prefix or app — that is an honest ``installed: False``.
        """
        candidates = candidate_exes(self.app(app_id), self.recipes_dir)
        idx = self._exe_index()
        for name in candidates:
            hits = idx.get(name.lower())
            if hits:
                return {"installed": True, "exe_path": str(hits[0]), "candidates": candidates}
        return {"installed": False, "exe_path": None, "candidates": candidates}

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
        idx = self._exe_index()
        apps = []
        for app_id in self.wine_apps:
            try:
                exe = exe_for(self.apps[app_id])
            except WinehubError:
                exe = ""
            exe_path = None
            for name in candidate_exes(self.apps[app_id], self.recipes_dir):
                hits = idx.get(name.lower())
                if hits:
                    exe_path = str(hits[0])
                    break
            apps.append(
                {
                    "id": app_id,
                    "exe": exe,
                    "installed": exe_path is not None,
                    "exe_path": exe_path,
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

    # ------------------------------------------------------------- install
    def _wine_env(self) -> dict[str, str]:
        return dict(os.environ, WINEPREFIX=str(self.prefix), **WINE_SUPPRESS_ENV)

    def init_prefix(self) -> dict:
        """Create the prefix with ``wineboot -i`` — the dev path.

        The kit postinst unpacks the cached prefix tarball instead (§5);
        this verb exists so a box without a kit can still reach an installed,
        running VARA. Idempotent: an initialized prefix is reported as such.
        """
        wine = self._require_wine()
        if (self.prefix / "drive_c").is_dir():
            return {"state": "already-initialized", "prefix": str(self.prefix)}
        log_dir = self.state_dir / "logs" / "winehub"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "wineboot.log"
        try:
            self.prefix.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WinehubError(
                f"cannot create {self.prefix} ({exc}) — it is root-owned on the target "
                "(the kit postinst creates it from the tarball, §5); sudo the wineboot by "
                "hand or fix ownership"
            ) from exc
        with open(log_path, "ab") as log:
            try:
                proc = subprocess.run(
                    [wine, "wineboot", "-i"],
                    cwd=str(self.prefix),
                    env=self._wine_env(),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=INIT_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired as exc:
                raise WinehubError(
                    f"wineboot -i still running after {INIT_TIMEOUT_S}s — log: {log_path}"
                ) from exc
        if proc.returncode != 0 or not (self.prefix / "drive_c").is_dir():
            raise WinehubError(f"wineboot -i failed (rc {proc.returncode}) — log: {log_path}")
        return {"state": "initialized", "prefix": str(self.prefix), "log": str(log_path)}

    def resolve_installer(self, app_id: str, explicit: str | Path | None = None) -> Path:
        """The user-supplied installer to run: an explicit path, else the
        single ``install/kit-import/`` match of the recipe's
        ``installer_glob`` (case-insensitive).

        G6: the artifact is user-supplied and never redistributed — it runs
        in place, never copied into the repo or a kit.
        """
        if explicit is not None:
            p = Path(explicit).expanduser()
            if not p.is_file():
                raise WinehubError(f"installer {p} does not exist")
            return p
        pattern = str(
            load_wine_recipe(app_id, self.recipes_dir).get("installer_glob") or ""
        ).strip()
        if not pattern:
            raise WinehubError(
                f"{app_id}: no installer_glob in install/recipes/{app_id}.yaml (wine block) — "
                f"pass the installer path explicitly: gd winehub install {app_id} <path.exe|.zip>"
            )
        if not self.kit_import.is_dir():
            raise WinehubError(
                f"installer staging dir {self.kit_import} is absent — create it and drop the "
                f"user-supplied {app_id} download there (G6), or pass its path explicitly"
            )
        matches = sorted(
            p
            for p in self.kit_import.iterdir()
            if p.is_file() and fnmatch.fnmatch(p.name.lower(), pattern.lower())
        )
        if not matches:
            raise WinehubError(
                f"{app_id}: nothing matching '{pattern}' in {self.kit_import} — drop the "
                f"user-supplied download there (G6; the .exe or the .zip wrapping it), or "
                f"pass its path: gd winehub install {app_id} <path>"
            )
        if len(matches) > 1:
            listing = ", ".join(p.name for p in matches)
            raise WinehubError(
                f"{app_id}: {len(matches)} files match '{pattern}' in {self.kit_import} — "
                f"keep one (move the rest out) and retry: {listing}"
            )
        return matches[0]

    def _unpack_zip(self, artifact: Path) -> tuple[Path, Path | None]:
        """VARA downloads are zips wrapping the setup .exe: extract to a temp
        dir and return ``(setup_exe, temp_dir)`` — the caller cleans temp_dir
        up. A plain .exe passes through untouched."""
        if artifact.suffix.lower() != ".zip":
            return artifact, None
        out = Path(tempfile.mkdtemp(prefix="gd-installer-"))
        try:
            with zipfile.ZipFile(artifact) as zf:
                zf.extractall(out)
        except (zipfile.BadZipFile, OSError) as exc:
            shutil.rmtree(out, ignore_errors=True)
            raise WinehubError(f"{artifact.name}: unreadable zip ({exc})") from exc
        exes = sorted(p for p in out.rglob("*") if p.is_file() and p.suffix.lower() == ".exe")
        if not exes:
            shutil.rmtree(out, ignore_errors=True)
            raise WinehubError(
                f"{artifact.name}: the zip holds no .exe installer — extract it into "
                f"{self.kit_import} by hand and pass the setup exe path explicitly"
            )
        picks = [p for p in exes if "setup" in p.name.lower()] or exes
        if len(picks) > 1:
            listing = ", ".join(p.name for p in picks)
            shutil.rmtree(out, ignore_errors=True)
            raise WinehubError(
                f"{artifact.name}: {len(picks)} candidate installers in the zip ({listing}) — "
                f"extract it into {self.kit_import} and pass the right one explicitly"
            )
        return picks[0], out

    def install(self, app_id: str, installer: str | Path | None = None) -> dict:
        """Silent install of one user-supplied VARA artifact into the prefix.

        ``setup`` mode (the VARA family's Inno installers) runs
        ``wine <setup.exe> /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`` with
        WINEPREFIX set, creating the prefix first if needed. ``portable``
        mode (``wine.installer: portable`` — VarAC ships a plain zip, not an
        installer) never runs wine: it unpacks into the recipe's
        ``wine.install_dir`` under ``drive_c``. Either way the probe after
        the step decides success: an installer that exits 0 without
        producing a candidate exe failed silently (it likely needed the
        GUI), and a portable zip without one is refused.
        """
        app = self.app(app_id)
        if not self._is_wine(app):
            raise WinehubError(
                f"{app_id}: not a wine-hub app — install is the VARA family's own; the "
                "supervisor owns the rest (§6.4)"
            )
        before = self.probe(app_id)
        if before["installed"]:
            return {"id": app_id, "state": "already-installed", "exe_path": before["exe_path"]}
        recipe = load_wine_recipe(app_id, self.recipes_dir)
        mode = str(recipe.get("installer") or "setup").strip().lower()
        if mode not in ("setup", "portable"):
            raise WinehubError(f"{app_id}: recipe wine.installer {mode!r} is not setup|portable")
        wine = None if mode == "portable" else self._require_wine()
        artifact = self.resolve_installer(app_id, installer)
        setup, temp_dir = self._unpack_zip(artifact)
        try:
            if mode == "portable":
                return self._install_portable(app_id, recipe, temp_dir)
            if not (self.prefix / "drive_c").is_dir():
                self.init_prefix()
            log_dir = self.state_dir / "logs" / "winehub"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"install-{app_id}.log"
            with open(log_path, "ab") as log:
                log.write(
                    f"--- install {app_id}: {setup.name} (from {artifact}) {_now()} ---\n".encode()
                )
                try:
                    proc = subprocess.run(
                        [wine, str(setup), *SILENT_INSTALL_FLAGS],
                        cwd=str(setup.parent),
                        env=self._wine_env(),
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=INSTALL_TIMEOUT_S,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise WinehubError(
                        f"{app_id}: installer still running after {INSTALL_TIMEOUT_S}s — it is "
                        f"probably showing a dialog; run it by hand: wine '{setup}' "
                        f"(log: {log_path})"
                    ) from exc
            if proc.returncode != 0:
                raise WinehubError(
                    f"{app_id}: installer exited {proc.returncode} — log: {log_path}"
                )
            after = self.probe(app_id)
            if not after["installed"]:
                raise WinehubError(
                    f"{app_id}: installer exited 0 but no candidate exe appeared "
                    f"({', '.join(after['candidates'])}) — it may install elsewhere or need "
                    f"the GUI; run by hand: wine '{setup}' (log: {log_path})"
                )
            return {
                "id": app_id,
                "state": "installed",
                "exe_path": after["exe_path"],
                "log": str(log_path),
            }
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _install_portable(self, app_id: str, recipe: dict, temp_dir: Path | None) -> dict:
        """``wine.installer: portable`` — unpack a plain-zip app (VarAC).

        No wine run: the zip's files land under the recipe's
        ``wine.install_dir`` inside ``drive_c`` and the probe decides
        success. The prefix must already exist (kit postinst tarball or
        ``gd winehub init``) — placing files into a half-made prefix would
        hide that from the operator.
        """
        install_dir = str(recipe.get("install_dir") or "").strip()
        if not install_dir:
            raise WinehubError(
                f"{app_id}: portable install needs wine.install_dir in the recipe — "
                f'the Windows dir the zip unpacks to under drive_c (e.g. "C:\\VarAC")'
            )
        m = re.fullmatch(r"[Cc]:(\\.*)", install_dir)
        if not m:
            raise WinehubError(
                f"{app_id}: recipe wine.install_dir {install_dir!r} is not a C:\\… "
                f'Windows path (e.g. "C:\\VarAC")'
            )
        parts = [p for p in m.group(1).split("\\") if p]
        if any(p in ("..", ".") for p in parts):
            raise WinehubError(
                f"{app_id}: recipe wine.install_dir {install_dir!r} would escape drive_c"
            )
        if temp_dir is None:
            raise WinehubError(
                f"{app_id}: portable install needs the .zip (a bare .exe has nothing "
                "to unpack — set the recipe's wine.installer to setup instead)"
            )
        self._require_prefix()
        target = self.prefix / "drive_c"
        for part in parts:
            target = target / part
        target.mkdir(parents=True, exist_ok=True)
        log_dir = self.state_dir / "logs" / "winehub"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"install-{app_id}.log"
        with open(log_path, "ab") as log:
            log.write(f"--- install {app_id}: portable unpack -> {target} {_now()} ---\n".encode())
        shutil.copytree(temp_dir, target, dirs_exist_ok=True)
        after = self.probe(app_id)
        if not after["installed"]:
            raise WinehubError(
                f"{app_id}: unpacked into {target} but no candidate exe appeared "
                f"({', '.join(after['candidates'])}) — the zip's layout may have "
                f"changed; check it by hand (log: {log_path})"
            )
        return {
            "id": app_id,
            "state": "installed",
            "mode": "portable",
            "dir": str(target),
            "exe_path": after["exe_path"],
            "log": str(log_path),
        }
