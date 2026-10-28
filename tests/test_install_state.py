# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""Install-state probe tests (M5): dnf batch / PATH probe / docroot / prefix.

Run: python3 -m pytest tests/test_install_state.py -q
"""

from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core import install_state, winehub  # noqa: E402

_BIND_SEQ = itertools.count(1)


def _app(app_id: str, strategy: str, cmd: str = "", packages: list | None = None) -> dict:
    install: dict = {"strategy": strategy}
    if packages is not None:
        install["packages"] = packages
    return {"id": app_id, "install": install, "launch": {"cmd": cmd}}


def _bind_path(tmp_path: Path, monkeypatch, scripts: dict[str, str]) -> Path:
    """Fresh PATH dir + generated executables — deterministic which() results."""
    bindir = tmp_path / f"bin{next(_BIND_SEQ)}"
    bindir.mkdir(exist_ok=True)
    for name, body in scripts.items():
        p = bindir / name
        p.write_text(f"#!/bin/sh\n{body}\n")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    return bindir


APPS = [
    _app("fldigi", "dnf", "fldigi", ["fldigi", "flrig"]),
    _app("ardop", "build", "ardopc -p 8515"),
    _app("meshtastic-web", "static", "served on 127.0.0.1:8092 by suite"),
    _app("vara-hf", "wine", "VARA.exe"),
    _app("mystery", "ancient-recipe", ""),
]


def test_dnf_batch_reports_partial_with_gaps(tmp_path, monkeypatch):
    script = (
        'echo "$@" >> {count}\n'
        "echo fldigi-4.2-1.fc45.x86_64\n"
        "echo package flrig is not installed\n"
    ).format(count=tmp_path / "rpm-calls")
    _bind_path(tmp_path, monkeypatch, {"rpm": script})
    out = install_state.app_states(APPS)
    assert out["fldigi"] == {"state": "partial", "detail": "missing packages: flrig"}
    # batched: two dnf apps would still be one rpm invocation
    assert len((tmp_path / "rpm-calls").read_text().splitlines()) == 1


def test_dnf_installed_missing_and_unknown(tmp_path, monkeypatch):
    _bind_path(
        tmp_path,
        monkeypatch,
        {
            "rpm": "echo fldigi-4.2-1.fc45.x86_64\necho flrig-1.0-1.fc45.x86_64\n",
        },
    )
    assert install_state.app_states(APPS)["fldigi"]["state"] == "installed"

    _bind_path(tmp_path, monkeypatch, {"rpm": "exit 1"})  # all missing, rc 1
    out = install_state.app_states(APPS)
    assert out["fldigi"] == {"state": "missing", "detail": "not installed: fldigi, flrig"}

    _bind_path(tmp_path, monkeypatch, {})  # no rpm at all (dev box)
    out = install_state.app_states(APPS)
    assert out["fldigi"]["state"] == "unknown"
    assert "rpm not available" in out["fldigi"]["detail"]


def test_which_probe_for_build_strategy(tmp_path, monkeypatch):
    _bind_path(tmp_path, monkeypatch, {"ardopc": "exit 0"})
    out = install_state.app_states(APPS)
    assert out["ardop"]["state"] == "installed"
    assert out["ardop"]["detail"].endswith("/ardopc")

    _bind_path(tmp_path, monkeypatch, {})
    assert install_state.app_states(APPS)["ardop"] == {
        "state": "missing",
        "detail": "ardopc not on PATH",
    }


def test_static_docroot_probe(tmp_path):
    root = tmp_path / "opt"
    out = install_state.app_states(APPS, app_root_path=root)
    assert out["meshtastic-web"]["state"] == "missing"
    assert "docroot" in out["meshtastic-web"]["detail"]
    (root / "meshtastic-web").mkdir(parents=True)
    out = install_state.app_states(APPS, app_root_path=root)
    assert out["meshtastic-web"] == {
        "state": "installed",
        "detail": f"docroot {root / 'meshtastic-web'}",
    }


def test_wine_probe_follows_prefix_then_per_app_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(winehub, "WINE_PREFIX", tmp_path / "prefix")
    out = install_state.app_states(APPS)
    assert out["vara-hf"]["state"] == "missing"
    (tmp_path / "prefix").mkdir()
    out = install_state.app_states(APPS)
    assert out["vara-hf"]["state"] == "prefix"
    assert "gd winehub install vara-hf" in out["vara-hf"]["detail"]
    exe = tmp_path / "prefix" / "drive_c" / "VARA" / "VARA.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    out = install_state.app_states(APPS)
    assert out["vara-hf"] == {"state": "installed", "detail": str(exe)}


def test_unknown_strategy_and_prose_cmds(tmp_path):
    out = install_state.app_states(APPS, app_root_path=tmp_path)
    assert out["mystery"]["state"] == "unknown"
    prose = [_app("web2", "static", "served by suite")]
    out = install_state.app_states(prose, app_root_path=tmp_path)
    assert out["web2"]["state"] == "missing"  # static: docroot decides, cmd ignored


def test_every_app_gets_a_row_and_it_never_raises(tmp_path):
    weird = [
        {},
        {"id": "noinstall"},
        {"id": "prose", "install": {"strategy": "tarball"}, "launch": {"cmd": "<placeholder>"}},
    ]
    out = install_state.app_states(weird, app_root_path=tmp_path)
    assert set(out) == {"noinstall", "prose"}
    assert all(
        r["state"] in ("installed", "partial", "missing", "prefix", "unknown") for r in out.values()
    )
    assert out["prose"]["state"] == "unknown"


def test_state_dir_env_is_untouched_and_module_defaults_hold(tmp_path, monkeypatch):
    """Probe reads only registry + filesystem; state dir stays supervisor-owned."""
    monkeypatch.setenv("GRID_DOWN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    out = install_state.app_states([_app("x", "static", "x")], app_root_path=tmp_path)
    assert out["x"]["state"] == "missing"
    assert install_state.app_root() == Path("/opt/grid-down") or os.environ.get(
        "GRID_DOWN_APP_ROOT"
    )
