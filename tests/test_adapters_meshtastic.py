# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""meshtastic device adapter tests (P4 M9): the CLI adapter's refusal surface.

The one adapter that owns no file: probe order (cli_path override > PATH >
kit venv), pinned command lines per device type, dry-run that provably never
spawns, payload shape, PSK masking (--seturl shows *** in display, the real
argv carries it), and every refusal with its exact fix — plus the registry
wiring (state: active, mesh bundle without meshtasticd).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core import registry  # noqa: E402  (after sys.path insert)
from core.adapters import ConfigError, get_adapter  # noqa: E402
from core.adapters import meshtastic_cli as mcli  # noqa: E402
from core.adapters.meshtastic_cli import MeshtasticCliAdapter  # noqa: E402

URL = "https://meshtastic.org/e/#CUST0MPSK"


@pytest.fixture
def box(tmp_path, monkeypatch):
    """Fake station: /opt root; PATH probe pinned to no-hit; a ready CLI."""
    root = tmp_path / "opt"
    vbin = root / "meshtastic" / "venv" / "bin"
    vbin.mkdir(parents=True)
    cli = vbin / "meshtastic"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setattr(mcli.shutil, "which", lambda name: None)

    def adapter(**kw) -> MeshtasticCliAdapter:
        app = {"id": "meshtastic", "config": {"adapter": "meshtastic-cli"}}
        return MeshtasticCliAdapter(app, install_root=root, **kw)

    return SimpleNamespace(tmp=tmp_path, root=root, cli=cli, adapter=adapter)


def _profile(dev: dict, node: dict | None = None, daemon: dict | None = None) -> dict:
    return {
        "profile": {"id": "d", "callsign": "W1AW", "grid": "FN31"},
        "mesh": {
            "device": dev,
            "node": node or {},
            "daemon": daemon if daemon is not None else {"enabled": False},
        },
    }


def _serial(path: Path, node: dict | None = None, daemon: dict | None = None) -> dict:
    return _profile({"type": "serial", "path": str(path), "host": None}, node, daemon)


# -- registry wiring ----------------------------------------------------------


def test_registry_wiring_and_mesh_bundle():
    apps = registry.load_registry(ROOT / "control" / "registry" / "apps")
    app = next(a for a in apps if a["id"] == "meshtastic")
    assert app["state"] == "active"
    assert app["config"]["adapter"] == "meshtastic-cli"
    assert "TBD" not in str(app["config"].get("native_path"))
    assert isinstance(get_adapter(app), MeshtasticCliAdapter)
    bundles = registry.load_bundles(ROOT / "control" / "registry" / "bundles.yaml")
    assert set(bundles["mesh"]["members"]) == {"meshtastic", "meshtastic-web"}
    assert "meshtasticd" not in bundles["mesh"]["members"]


def test_read_reports_probe_not_file(box):
    info = box.adapter().read()
    assert info["exists"] is True
    assert info["path"] == str(box.cli)
    assert info["registration"] == "present"
    assert "dry-run" in info["note"]
    # CLI verb contract: path/exists/registration always renderable
    for key in ("app", "path", "exists", "registration"):
        assert key in info


def test_read_without_cli(box, monkeypatch):
    box.cli.unlink()
    info = box.adapter().read()
    assert info["exists"] is False
    assert "not found" in info["path"]
    assert info["registration"].startswith("absent")


# -- CLI resolution -----------------------------------------------------------


def test_cli_resolution_order(box, tmp_path, monkeypatch):
    assert box.adapter().resolve_cli() == box.cli  # venv fallback (PATH pinned off)
    on_path = tmp_path / "path-cli"
    on_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mcli.shutil, "which", lambda name: str(on_path))
    assert box.adapter().resolve_cli() == on_path  # PATH wins over the venv
    fake = box.adapter(cli_path=box.cli)  # explicit override wins over both
    monkeypatch.setattr(mcli.shutil, "which", lambda name: str(on_path))
    assert fake.resolve_cli() == box.cli
    with pytest.raises(ConfigError, match="cli_path override"):
        box.adapter(cli_path=tmp_path / "missing")._require_cli()


def test_apply_refuses_without_cli(box, tmp_path):
    box.cli.unlink()
    dev = tmp_path / "ttyUSB1"
    dev.touch()
    with pytest.raises(ConfigError, match="not installed") as exc:
        box.adapter().apply(_serial(dev, {"long_name": "Hilltop"}))
    assert "PATH" in str(exc.value) and str(box.cli) in str(exc.value)


# -- dry run: pinned commands, payload shape, no spawn ------------------------


def test_dry_run_serial_pins_commands_and_never_spawns(box, tmp_path, monkeypatch):
    dev = tmp_path / "ttyUSB1"
    dev.touch()
    node = {
        "long_name": "Hilltop",
        "short_name": "HILL",
        "region": "US",
        "modem_preset": "LONG_FAST",
    }
    cli = str(box.cli)

    def no_spawn(*a, **k):  # dry run must not touch the device at all
        raise AssertionError("subprocess spawned during dry_run")

    monkeypatch.setattr(mcli.subprocess, "run", no_spawn)
    out = box.adapter().apply(_serial(dev, node), dry_run=True)
    assert out["dry_run"] is True and out["changed"] is False
    assert out["commands"] == [f"{cli} --port {dev} configure"]
    assert out["target"] == f"serial {dev}"
    assert out["payload"] == {
        "config": {
            "user": {"long_name": "Hilltop", "short_name": "HILL"},
            "lora": {"region": "US", "modem_preset": "LONG_FAST"},
        }
    }
    assert "dry run" in out["note"]


def test_dry_run_tcp_ble_and_seturl_masking(box, tmp_path):
    cli = str(box.cli)
    tcp = _profile({"type": "tcp", "path": None, "host": "meshtastic.lan:4403"}, {"region": "US"})
    out = box.adapter().apply(tcp, dry_run=True)
    assert out["commands"] == [f"{cli} --host meshtastic.lan:4403 configure"]

    ble = _profile({"type": "ble", "path": None, "host": None}, {"region": "US"})
    out = box.adapter().apply(ble, dry_run=True)
    assert out["commands"] == [f"{cli} configure"]
    assert "auto-discovers" in out["target"]

    dev = tmp_path / "ttyUSB1"
    dev.touch()
    serial_url = _profile(
        {"type": "serial", "path": str(dev), "host": None},
        {"channel_url": URL},
    )
    out = box.adapter().apply(serial_url, dry_run=True)  # only the URL: payload stays {}
    assert out["payload"] == {}
    assert out["commands"] == [
        f"{cli} --port {dev} configure",
        f"{cli} --port {dev} --seturl ***",
    ]
    assert not any(URL in line for line in out["commands"]), "PSK leaked into display"


# -- real run: fake subprocess, PSK only on the real argv ----------------------


def test_apply_runs_cli_and_keeps_psk_off_display(box, tmp_path, monkeypatch):
    dev = tmp_path / "ttyUSB1"
    dev.touch()
    calls = []

    def fake_run(cmd, input=None, capture_output=False, text=False, timeout=None, check=False):
        calls.append((list(cmd), input))
        return SimpleNamespace(returncode=0, stdout="Wrote config\n", stderr="")

    monkeypatch.setattr(mcli.subprocess, "run", fake_run)
    prof = _profile(
        {"type": "serial", "path": str(dev), "host": None},
        {"region": "US", "channel_url": URL},
    )
    out = box.adapter().apply(prof)
    assert len(calls) == 2
    assert calls[0][0] == [str(box.cli), "--port", str(dev), "configure"]
    assert calls[0][1].startswith("config:\n") and "region: US" in calls[0][1]
    assert calls[1][0][-2:] == ["--seturl", URL]  # real argv carries the PSK
    assert not any(URL in tok for cmd, _ in calls for tok in cmd[:-1])
    assert len(out["ran"]) == 2 and out["ran"][0].endswith("→ ok (Wrote config)")
    assert "applied to" in out["note"]
    assert not any(URL in line for line in out["ran"])


def test_apply_failure_surfaces_stderr(box, tmp_path, monkeypatch):
    dev = tmp_path / "ttyUSB1"
    dev.touch()

    def failing(*a, **k):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom: no device")

    monkeypatch.setattr(mcli.subprocess, "run", failing)
    prof = _profile({"type": "serial", "path": str(dev), "host": None}, {"region": "US"})
    with pytest.raises(ConfigError, match=r"failed \(exit 1\) — boom: no device"):
        box.adapter().apply(prof)


# -- refusals (§6.2: exact fix) ------------------------------------------------


def test_refusals_name_the_exact_fix(box, tmp_path):
    dev = tmp_path / "ttyUSB1"
    dev.touch()
    cases = [
        (
            _serial(dev, daemon={"enabled": True}),
            r"daemon\.enabled is true",
            "meshtasticd",
        ),
        (
            _profile({"type": "serial", "path": "", "host": None}, {"region": "US"}),
            r"mesh\.device\.path is empty",
            "by-id",
        ),
        (
            _serial(tmp_path / "absent", {"region": "US"}),
            "is not present",
            "attach the handset",
        ),
        (
            _profile({"type": "tcp", "path": None, "host": None}, {"region": "US"}),
            r"mesh\.device\.host is empty",
            "192.168.1.50",
        ),
        (
            _profile({"type": "carrier-pigeon", "path": "", "host": None}, {"region": "US"}),
            "not serial\\|tcp\\|ble",
            "spec §4",
        ),
        (
            _serial(dev),  # nothing set at all
            "nothing to configure",
            "gd profile mesh",
        ),
    ]
    for prof, match, fix in cases:
        with pytest.raises(ConfigError, match=match) as exc:
            box.adapter().apply(prof)
        assert fix in str(exc.value), prof
