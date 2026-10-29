# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""ptybridge tests (P3b): real pty pairs, real bytes, no wine, no pactl.

The bridge's hub refusals are winehub's own (test_winehub.py owns those);
here we prove the harness itself: map → round-trip → teardown, on pty and
TCP peers, with the dosdevices map left exactly as it was.
"""

from __future__ import annotations

import os
import select
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))

from core import ptybridge, winehub  # noqa: E402
from core.winehub import WinehubError  # noqa: E402

APPS = [
    {
        "id": "vara-hf",
        "install": {"strategy": "wine"},
        "launch": {"via": "wine-hub", "cmd": "VARA.exe (via gd winehub)"},
    },
]


@pytest.fixture
def hub(tmp_path) -> winehub.WineHub:
    prefix = tmp_path / "prefix"
    prefix.mkdir()  # map_serial requires the prefix directory to exist
    return winehub.WineHub(APPS, state_dir=tmp_path / "state", prefix=prefix)


def _read(fd: int, count: int, timeout: float = 5.0) -> bytes:
    """Read exactly count bytes from fd, or fail the test with the shortfall."""
    buf = b""
    deadline = time.monotonic() + timeout
    while len(buf) < count:
        left = deadline - time.monotonic()
        if left <= 0:
            raise AssertionError(f"timed out reading {count} bytes (got {len(buf)})")
        ready, _, _ = select.select([fd], [], [], min(left, 0.5))
        if not ready:
            continue
        chunk = os.read(fd, count - len(buf))
        if chunk:
            buf += chunk
    return buf


def _serve(bridge: ptybridge.ComBridge):
    stop = threading.Event()
    thread = threading.Thread(target=bridge.serve, args=(stop,), daemon=True)
    thread.start()
    return stop, thread


def test_open_maps_com_through_the_wine_hub(hub):
    bridge = ptybridge.ComBridge(hub)
    info = bridge.open(5)
    assert info["com"] == "com5"
    assert info["wine_side"] == str(hub.prefix / "dosdevices" / "com5")
    assert os.path.islink(info["wine_side"])  # the real wine-hub map
    assert os.path.realpath(info["wine_side"]) == info["com_pts"]
    # raw-mode pty endpoints: both slave nodes exist and are distinct (the
    # paths are /dev/pts/N on Linux, /dev/ttysNN on macOS — never hardcode)
    assert os.path.exists(info["com_pts"]) and os.path.exists(info["peer"])
    assert info["com_pts"] != info["peer"]
    assert info["peer_kind"] == "pty"
    assert info["map_state"] == "mapped"
    st = hub.status()  # the map is visible to `gd winehub status` like hardware
    assert st["com"] == {"com5": info["com_pts"]}
    out = bridge.close()
    assert out["state"] == "closed" and out["unmap"] == "unmapped"


def test_pty_round_trip_both_directions(hub):
    bridge = ptybridge.ComBridge(hub)
    info = bridge.open(5)
    com_side = os.open(info["wine_side"], os.O_RDWR | os.O_NOCTTY)  # what wine holds
    rig_side = os.open(info["peer"], os.O_RDWR | os.O_NOCTTY)  # what the fake rig holds
    stop, thread = _serve(bridge)
    try:
        os.write(com_side, b"FA00007123450;")
        assert _read(rig_side, 14) == b"FA00007123450;"
        os.write(rig_side, b"FA00007123450;OK")
        assert _read(com_side, 16) == b"FA00007123450;OK"
    finally:
        stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        os.close(com_side)
        os.close(rig_side)
    out = bridge.close()
    assert out["bytes"] == {"wine_to_peer": 14, "peer_to_wine": 16}
    # teardown left no trace: the COM map is gone and both pts nodes died
    assert not (hub.prefix / "dosdevices" / "com5").is_symlink()
    assert not os.path.exists(info["com_pts"]) and not os.path.exists(info["peer"])


def test_tcp_round_trip_and_ephemeral_port(hub):
    bridge = ptybridge.ComBridge(hub)
    info = bridge.open(6, peer="tcp", tcp_port=0)  # port 0 = ephemeral, like the CLI --tcp-port
    assert info["peer_kind"] == "tcp"
    host, port = info["peer"].removeprefix("tcp://").rsplit(":", 1)
    com_side = os.open(info["wine_side"], os.O_RDWR | os.O_NOCTTY)
    client = socket.create_connection((host, int(port)), timeout=5)
    stop, thread = _serve(bridge)
    try:
        client.sendall(b"PING from the fake-rig shim")
        assert _read(com_side, 27) == b"PING from the fake-rig shim"
        os.write(com_side, b"ACK")
        client.settimeout(5)
        assert client.recv(3) == b"ACK"
        client.close()  # hangup: the bridge must keep serving for the next client
        time.sleep(0.5)
        client2 = socket.create_connection((host, int(port)), timeout=5)
        client2.sendall(b"second")
        assert _read(com_side, 6) == b"second"
        client2.close()
    finally:
        stop.set()
        thread.join(timeout=5)
        os.close(com_side)
    out = bridge.close()
    assert out["unmap"] == "unmapped" and out["com"] == "com6"


def test_close_is_idempotent(hub):
    bridge = ptybridge.ComBridge(hub)
    assert bridge.close() == {"state": "not-open", "com": None, "unmap": "n/a"}
    bridge.open(5)
    assert bridge.close()["state"] == "closed"
    assert bridge.close()["state"] == "not-open"


def test_double_open_refused(hub):
    bridge = ptybridge.ComBridge(hub)
    bridge.open(5)
    try:
        with pytest.raises(ptybridge.BridgeError, match="already open"):
            bridge.open(6)
    finally:
        bridge.close()


def test_bad_com_refuses_and_cleans_up(hub):
    bridge = ptybridge.ComBridge(hub)
    with pytest.raises(WinehubError, match="bad COM name"):
        bridge.open(99)
    assert bridge.com_fd is None  # the pty fd did not leak


def test_existing_map_conflict_refuses_and_cleans_up(hub):
    (hub.prefix / "dosdevices").mkdir(parents=True, exist_ok=True)
    (hub.prefix / "dosdevices" / "com5").symlink_to("/dev/null")  # someone else's map
    bridge = ptybridge.ComBridge(hub)
    with pytest.raises(WinehubError, match="already maps to"):
        bridge.open(5)
    assert bridge.com_fd is None  # cleaned up before the hub's error surfaced


def test_tcp_port_conflict_cleans_up(hub):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    bridge = ptybridge.ComBridge(hub)
    with pytest.raises(OSError):
        bridge.open(5, peer="tcp", tcp_port=port)
    blocker.close()
    assert bridge.com_fd is None and bridge.listener is None
    assert not (hub.prefix / "dosdevices" / "com5").is_symlink()  # map rolled back
