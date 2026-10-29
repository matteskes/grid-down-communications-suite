# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""pty-pair COM loopback harness — `gd winehub bridge` (spec §6.4, P3b).

Windows-side VARA talks to its modem over a DOS COM port, which the wine hub
maps as a ``dosdevices/comN`` symlink onto a real serial device
(:meth:`WineHub.map_serial`). On the arm benches there is no serial device
and no wine — but the *mapping discipline* is exactly what a harness needs:
this module holds a pty pair, maps one end onto ``comN`` through the wine
hub's own ``map_serial`` (same validation, same ``dosdevices`` convention,
same teardown via ``unmap``), and relays every byte to a peer — a second pty
(what a fake-rig script opens) or a TCP listener (what a protocol-level fake
speaks).

Design constraints (P3b):

- **Pure stdlib** (``pty.openpty``/``select``/``socket``): no socat, no new
  air-gap dependency — the whole harness is arm-testable on the aarch64 VM
  (docs/phase3b-vm-runbook.md, §B).
- **No wine required to run**: ``map_serial`` only needs the prefix directory
  to exist (``GRID_DOWN_WINE_PREFIX`` points rehearsals at a fixture prefix)
  and the device node to exist — a pty slave does, for as long as the master
  end is held open, which is precisely this module's job.
- **Owned teardown**: SIGINT/SIGTERM stops the relay, unmaps ``comN`` and
  closes both ptys, leaving the ``dosdevices`` map exactly as it was. The
  byte tally is printed so a transcript doubles as evidence.
- **The COM map is real**: while the bridge runs, ``gd winehub status``
  reports ``comN`` like any physical mapping — configuring VARA against a
  bridged port is byte-for-byte the same operation as against hardware.
"""

from __future__ import annotations

import os
import pty
import select
import socket
import time

try:  # package import (gd CLI, pytest)
    from . import winehub
except ImportError:  # pragma: no cover - sibling import inside control/
    import winehub  # type: ignore

READ_CHUNK = 4096
SELECT_TIMEOUT_S = 0.25
# A pty master whose slave end nobody holds open reports readable forever and
# read() fails with EIO (Linux); cap the retry spin while waiting for the
# Windows app (or the test client) to open its side.
EIO_BACKOFF_S = 0.05
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 8300  # VARA HF's command port — the peer a shim binds


class BridgeError(RuntimeError):
    """Refusal with operator-facing text (same contract as WinehubError)."""


def open_pair() -> tuple[int, int, str]:
    """Allocate a pty; return ``(master_fd, slave_fd, slave_path)``.

    The bridge keeps the slave fd open for the pty's whole life (as
    ``*_hold``): the line discipline binds at that first open, so raw mode
    is set on a *live* ldisc, the master never sees the slave-less EIO
    phase, and the pts node stays alive between client sessions (VARA may
    close and reopen its COM port freely).
    """
    master, slave = pty.openpty()
    return master, slave, os.ttyname(slave)


def _set_raw(fd: int) -> None:
    """Raw mode on the pty (termios is shared by both ends).

    A DOS COM port has no line discipline: no canonical buffering, no echo,
    no CR/LF mapping. Without this, VARA frames that carry no newline sit in
    the slave's ICANON queue forever (found on the dev box, macOS; the same
    termios contract holds on the Linux guest).
    """
    try:
        import termios
    except ImportError:  # pragma: no cover - non-POSIX dev box only
        return
    attrs = termios.tcgetattr(fd)
    attrs[0] &= ~(
        termios.IGNBRK
        | termios.BRKINT
        | termios.PARMRK
        | termios.ISTRIP
        | termios.INLCR
        | termios.IGNCR
        | termios.ICRNL
        | termios.IXON
    )
    attrs[1] &= ~termios.OPOST
    attrs[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


class ComBridge:
    """Hold one mapped COM pty and relay it to a peer pty or TCP listener.

    The bridge is the whole lifecycle: :meth:`open` validates through the
    wine hub (bad COM names, missing prefix, existing maps refuse exactly as
    ``gd winehub map`` does), :meth:`serve` pumps bytes until the stop event
    (or SIGINT), and :meth:`close` unmaps and tears the ptys down. Byte
    counters name their direction from the wine side, so a transcript reads
    "what VARA sent" / "what the fake rig answered".
    """

    def __init__(self, hub: winehub.WineHub):
        self.hub = hub
        self.com_fd: int | None = None
        self.com_hold: int | None = None  # our own slave fd: ldisc stays bound
        self.com_name: str | None = None
        self.com_pts: str | None = None
        self.com_dev: str | None = None
        self.peer_fd: int | None = None  # pty peer: master end of the 2nd pair
        self.peer_hold: int | None = None
        self.peer_dev: str | None = None
        self.peer_kind: str | None = None  # "pty" | "tcp"
        self.listener: socket.socket | None = None
        self.conn: socket.socket | None = None
        self.bytes_wine_to_peer = 0
        self.bytes_peer_to_wine = 0

    # ---------------------------------------------------------------- setup
    def open(
        self,
        com: int | str,
        peer: str = "pty",
        tcp_host: str = DEFAULT_TCP_HOST,
        tcp_port: int = DEFAULT_TCP_PORT,
    ) -> dict:
        """Map ``comN`` onto a fresh pty slave and prepare the peer end.

        Mapping goes through the wine hub, so every refusal is the hub's own
        (``bad COM name``, ``prefix not installed yet``, ``already maps to``,
        ``device does not exist`` — the last cannot fire for a held-open pty,
        but the check stays single-source). Any failure after the pty is
        allocated cleans everything up before raising.
        """
        if self.com_fd is not None:
            raise BridgeError("bridge already open — close it first (Ctrl-C tears it down)")
        com_name = f"com{com}".lower()
        self.com_fd, self.com_hold, self.com_pts = open_pair()
        try:
            mapped = self.hub.map_serial(com_name, self.com_pts)
        except Exception:
            self._close_fds()
            self.com_fd, self.com_hold, self.com_pts = None, None, None
            raise
        self.com_name = com_name
        self.com_dev = str(self.hub.prefix / "dosdevices" / com_name)
        try:
            if peer == "pty":
                self.peer_fd, self.peer_hold, peer_pts = open_pair()
                self.peer_dev = peer_pts
                self.peer_kind = "pty"
            elif peer == "tcp":
                self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.listener.bind((tcp_host, tcp_port))
                self.listener.listen(1)
                port = self.listener.getsockname()[1]
                self.peer_dev = f"tcp://{tcp_host}:{port}"
                self.peer_kind = "tcp"
            else:
                raise BridgeError(f"unknown peer kind: {peer!r} (pty|tcp)")
            # both ptys now have a live, bound line discipline (our hold fds):
            # raw mode set once here holds for the bridge's whole life.
            _set_raw(self.com_hold)
            if self.peer_hold is not None:
                _set_raw(self.peer_hold)
            # nonblocking masters: serve() drains them every iteration, so a
            # lost select wakeup can never wedge the relay
            os.set_blocking(self.com_fd, False)
            if self.peer_fd is not None:
                os.set_blocking(self.peer_fd, False)
        except Exception:
            self.close()
            raise
        info = self.info()
        info["map_state"] = mapped["state"]
        return info

    def info(self) -> dict:
        """The printed contract: wine side (symlink + pts) and the peer end."""
        return {
            "com": self.com_name,
            "wine_side": self.com_dev,
            "com_pts": self.com_pts,
            "peer": self.peer_dev,
            "peer_kind": self.peer_kind,
        }

    # ----------------------------------------------------------------- pump
    def serve(self, stop) -> None:
        """Relay until ``stop`` (a ``threading.Event``) is set; SIGINT raises.

        TCP peers are re-accepted one at a time: a client that disconnects
        hands the bridge back for the next one, so a fake-rig script can
        reconnect without remapping COM. The masters are nonblocking and
        drained unconditionally every iteration: select() remains as a sleep
        + hint, but delivery never depends on a wakeup (a lost pty wakeup
        was observed on a Fedora 45 aarch64 guest under automation harnesses
        — polling makes the bridge immune by construction). A read failure
        on the wine side (EIO — nobody holds the pts) is waited out, not
        fatal: Windows apps open and close COM freely.
        """
        debug = os.environ.get("GD_PTYBRIDGE_DEBUG") == "1"
        while not stop.is_set():
            watch = [self.com_fd]
            conn_fd = self.conn.fileno() if self.conn is not None else None
            if self.peer_kind == "pty":
                watch.append(self.peer_fd)
            elif conn_fd is None:
                watch.append(self.listener.fileno())
            else:
                watch.append(conn_fd)
            try:
                ready, _, _ = select.select(watch, [], [], SELECT_TIMEOUT_S)
            except OSError:
                if stop.is_set():
                    break  # close() raced the select: that is teardown, not a fault
                raise
            if debug and ready:
                print(f"gd-bridge: select ready {ready}", flush=True)
            handled = set()
            for fd in ready:
                if self.peer_kind == "tcp" and conn_fd is None and fd == self.listener.fileno():
                    self._accept()
                    handled.add(fd)
                    continue
                if fd == self.com_fd:
                    # wine side out: to the pty peer, or to the connected client
                    other = self.peer_fd if self.peer_kind == "pty" else conn_fd
                else:
                    other = self.com_fd
                if not self._pump(fd, other):
                    self._hangup()  # tcp peer gone: wait for the next client
                    handled.update(watch)
                    break
            # drain anything select's wakeup may have missed (nonblocking)
            for fd in watch:
                if fd in handled or stop.is_set():
                    continue
                if fd == self.com_fd:
                    other = self.peer_fd if self.peer_kind == "pty" else conn_fd
                else:
                    other = self.com_fd
                if not self._pump(fd, other):
                    self._hangup()
                    break

    def _accept(self) -> None:
        conn, addr = self.listener.accept()
        if self.conn is not None:  # one peer at a time: newest wins
            self.conn.close()
        self.conn = conn
        self.peer_dev = f"tcp://{addr[0]}:{addr[1]} (connected)"

    def _hangup(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _pump(self, fd: int, other: int | None) -> bool:
        """One read→write hop. False: a tcp peer hung up (serve re-selects)."""
        try:
            data = os.read(fd, READ_CHUNK)
        except BlockingIOError:
            return True  # nonblocking drain: nothing queued on this fd right now
        except OSError as exc:
            if os.environ.get("GD_PTYBRIDGE_DEBUG") == "1":
                print(f"gd-bridge: read fd {fd} OSError {exc}", flush=True)
            time.sleep(EIO_BACKOFF_S)  # EIO: the pty's other end is not open yet
            return True
        if os.environ.get("GD_PTYBRIDGE_DEBUG") == "1":
            print(f"gd-bridge: fd {fd} -> fd {other}: {data!r}", flush=True)
        if not data:
            if fd == self.com_fd:
                time.sleep(EIO_BACKOFF_S)  # BSD-style master EOF: keep the bridge
                return True
            return False
        if other is not None:
            view = memoryview(data)
            while view:  # full write: ptys/sockets may take less than one chunk
                written = os.write(other, view)
                view = view[written:]
            if fd == self.com_fd:
                self.bytes_wine_to_peer += len(data)
            else:
                self.bytes_peer_to_wine += len(data)
        return True  # no peer connected yet: drop and keep serving

    # ------------------------------------------------------------ teardown
    def _close_fds(self) -> None:
        """Close every held fd (masters + our slave holds), tolerating races."""
        for attr in ("com_fd", "peer_fd", "com_hold", "peer_hold"):
            fd = getattr(self, attr)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, None)

    def close(self) -> dict:
        """Unmap ``comN`` (best effort), close the ptys/sockets, tally bytes.

        Idempotent: a second close on a not-open bridge reports ``not-open``
        instead of raising, so a CLI ``finally`` block is always safe.
        """
        if self.com_fd is None:
            return {"state": "not-open", "com": self.com_name, "unmap": "n/a"}
        com_name = self.com_name
        try:
            unmap = self.hub.unmap(com_name)["state"]
        except winehub.WinehubError as exc:
            unmap = f"failed ({exc})"
        self._close_fds()
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        tally = {
            "wine_to_peer": self.bytes_wine_to_peer,
            "peer_to_wine": self.bytes_peer_to_wine,
        }
        self.com_name, self.com_pts, self.com_dev = None, None, None
        self.peer_dev, self.peer_kind = None, None
        return {"state": "closed", "com": com_name, "unmap": unmap, "bytes": tally}
