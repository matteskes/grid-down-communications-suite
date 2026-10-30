# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
"""meshtastic device adapter (``meshtastic-cli``): the profile drives the node (§6.11, M9).

The other adapters own files; this one owns a *device*: it renders the
profile's ``mesh.node`` block into a payload and pushes it with the meshtastic
Python CLI the kit ships (recipe: pip-vendored wheels into
``/opt/grid-down/meshtastic/venv`` — install/recipes/meshtastic.yaml), so the
payload never lands on disk and the node keeps its firmware-side state.

CLI resolution order: an explicit ``cli_path=`` (tests / operator override),
the ``PATH``, then the kit's venv. Refusal names both probed locations.

Payload shape follows upstream's YAML config format (``config.user`` for the
node names, ``config.lora`` for region/modem preset), piped to
``meshtastic configure`` on stdin; the channel URL goes as ``--seturl`` (its
value can carry the PSK, so it travels on the real argv only — display, logs
and JSON mask it as ``***``). Target selection follows ``mesh.device.type``:
serial gets ``--port <path>`` (the path must exist; the exact fix is named
otherwise), tcp gets ``--host <host>``, ble gets none (the CLI discovers the
handset by itself). Exact upstream flag names are re-verified at the live E2E
(M10); everything pin-dependent lives in ``_target_args``/``_payload``.

Refusals (§6.2: exact fix, no half-applies): CLI missing, serial without a
path / with an absent path, tcp without a host, an unknown device type, an
empty ``mesh.node`` (nothing to push), and ``mesh.daemon.enabled: true`` — a
HAT station's radio belongs to meshtasticd, so the adapter refuses and the
operator clears the flag first (or configures the daemon's drop-in instead:
``gd config apply meshtasticd``).
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import yaml

from .base import BaseAdapter, ConfigError

DEVICE_TYPES = ("serial", "tcp", "ble")


class MeshtasticCliAdapter(BaseAdapter):
    """§6.2 contract over a device instead of a file (``gd config show|apply``)."""

    format = "device"

    def __init__(self, app: dict, *, install_root=None, cli_path=None, **kwargs):
        super().__init__(app, **kwargs)
        self._install_root = Path(install_root) if install_root else Path("/opt/grid-down")
        self._cli_path = Path(cli_path) if cli_path else None

    # -- CLI probe ---------------------------------------------------------------

    def venv_cli(self) -> Path:
        """The kit recipe's CLI (install/recipes/meshtastic.yaml pip.install)."""
        return self._install_root / "meshtastic" / "venv" / "bin" / "meshtastic"

    def resolve_cli(self) -> Path | None:
        """Explicit ``cli_path=``, else ``PATH``, else the kit venv — or None."""
        if self._cli_path is not None:
            return self._cli_path if self._cli_path.is_file() else None
        on_path = shutil.which("meshtastic")
        if on_path:
            return Path(on_path)
        venv = self.venv_cli()
        return venv if venv.is_file() else None

    def _require_cli(self) -> Path:
        cli = self.resolve_cli()
        if cli is None:
            if self._cli_path is not None:
                raise ConfigError(
                    f"{self.app_id}: cli_path override {self._cli_path} does not exist"
                )
            raise ConfigError(
                f"{self.app_id}: the meshtastic CLI is not installed — neither on PATH nor at "
                f"{self.venv_cli()}. Install the kit app first (recipe: pip-vendored wheels), "
                f"then re-run `gd config apply {self.app_id}`."
            )
        return cli

    # -- profile mesh: block -----------------------------------------------------

    @staticmethod
    def _mesh(profile) -> tuple[dict, dict, dict]:
        mesh = (profile or {}).get("mesh") or {}
        return mesh.get("device") or {}, mesh.get("node") or {}, mesh.get("daemon") or {}

    def _validate(self, dev: dict, node: dict, daemon: dict) -> None:
        dtype = str(dev.get("type") or "").strip().lower()
        if dtype not in DEVICE_TYPES:
            raise ConfigError(
                f"{self.app_id}: mesh.device.type {dtype!r} is not {'|'.join(DEVICE_TYPES)}"
                " — fix the profile (spec §4; `gd profile mesh` / GUI Mesh tab)"
            )
        if daemon.get("enabled"):
            raise ConfigError(
                f"{self.app_id}: mesh.daemon.enabled is true — the LoRa HAT's radio belongs to "
                "meshtasticd, not the handset payload. For the USB-handset path set "
                "mesh.daemon.enabled=false; for the HAT path configure the daemon instead "
                "(`gd config apply meshtasticd`)."
            )
        if dtype == "serial":
            path = str(dev.get("path") or "").strip()
            if not path:
                raise ConfigError(
                    f"{self.app_id}: mesh.device.type is serial but mesh.device.path is empty "
                    "— set it (e.g. /dev/ttyUSB1 — see `ls /dev/serial/by-id/`)"
                )
            if not Path(path).exists():
                raise ConfigError(
                    f"{self.app_id}: serial path {path} is not present — attach the handset "
                    "(cp210x/ch341), then re-run (`ls /dev/serial/by-id/`)"
                )
        elif dtype == "tcp" and not str(dev.get("host") or "").strip():
            raise ConfigError(
                f"{self.app_id}: mesh.device.type is tcp but mesh.device.host is empty — set "
                "it (IP or hostname, e.g. 192.168.1.50 or meshtastic.lan:4403)"
            )
        # ble: no path/host by design — the CLI discovers the handset.

    @staticmethod
    def _payload(node: dict) -> dict:
        """``config.user``/``config.lora`` from the non-empty mesh.node fields."""
        user = {}
        if str(node.get("long_name") or "").strip():
            user["long_name"] = str(node["long_name"]).strip()
        if str(node.get("short_name") or "").strip():
            user["short_name"] = str(node["short_name"]).strip()
        lora = {}
        if str(node.get("region") or "").strip():
            lora["region"] = str(node["region"]).strip()
        if str(node.get("modem_preset") or "").strip():
            lora["modem_preset"] = str(node["modem_preset"]).strip()
        config: dict = {}
        if user:
            config["user"] = user
        if lora:
            config["lora"] = lora
        return {"config": config} if config else {}

    def _target_args(self, dev: dict) -> tuple[list[str], str]:
        dtype = str(dev.get("type") or "").strip().lower()
        if dtype == "serial":
            path = Path(str(dev.get("path")).strip())
            return ["--port", str(path)], f"serial {path}"
        if dtype == "tcp":
            host = str(dev.get("host")).strip()
            return ["--host", host], f"tcp {host}"
        return [], "ble (CLI auto-discovers the handset)"

    def _commands(self, cli: Path, target_args: list[str], node: dict) -> list[list[str]]:
        """The argv list: ``configure`` (payload on stdin) + optional ``--seturl``."""
        cmds = [[str(cli), *target_args, "configure"]]
        url = str(node.get("channel_url") or "").strip()
        if url:
            cmds.append([str(cli), *target_args, "--seturl", url])
        return cmds

    @staticmethod
    def _display(commands: list[list[str]]) -> list[str]:
        """Command lines for humans/logs — the --seturl PSK shows as ``***``."""
        out = []
        for cmd in commands:
            shown, hide = [], False
            for tok in cmd:
                if hide:
                    shown.append("***")
                    hide = False
                else:
                    shown.append(shlex.quote(tok))
                    hide = tok == "--seturl"
            out.append(" ".join(shown))
        return out

    # -- §6.2 contract -------------------------------------------------------------

    def read(self) -> dict:
        """Probe report — no on-disk file to show; apply previews the payload.

        ``path``/``exists``/``registration`` keep `gd config show` uniform with
        the file adapters (the CLI verb renders them unconditionally).
        """
        cli = self.resolve_cli()
        return {
            "app": self.app_id,
            "adapter": "meshtastic-cli",
            "path": str(cli) if cli else "(meshtastic CLI not found)",
            "exists": bool(cli),
            "format": self.format,
            "probe": [f"PATH ({shutil.which('meshtastic') or 'no hit'})", str(self.venv_cli())],
            "registration": (
                "present" if cli else "absent — install the kit app (pip-vendored wheels)"
            ),
            "note": (
                "device adapter — the payload comes from the profile's mesh.node block; "
                f"preview with `gd config apply {self.app_id} --dry-run`"
            ),
        }

    def apply(self, profile: dict, dry_run: bool = False) -> dict:
        dev, node, daemon = self._mesh(profile)
        self._validate(dev, node, daemon)
        payload = self._payload(node)
        url = str(node.get("channel_url") or "").strip()
        if not payload and not url:
            raise ConfigError(
                f"{self.app_id}: mesh.node has nothing to configure — fill long_name/"
                "short_name/region/modem_preset/channel_url in the profile "
                "(`gd profile mesh` / GUI Mesh tab)"
            )
        cli = self._require_cli()
        target_args, target = self._target_args(dev)
        commands = self._commands(cli, target_args, node)
        out = {
            "app": self.app_id,
            "adapter": "meshtastic-cli",
            "target": target,
            "cli": str(cli),
            "dry_run": dry_run,
            "changed": False,  # nothing on this box changes — the node does
            "commands": self._display(commands),
            "payload": payload,
        }
        if dry_run:
            out["note"] = (
                f"dry run — nothing sent to the node; re-run without --dry-run to push to {target}"
            )
            return out
        payload_text = yaml.safe_dump(payload, sort_keys=False) if payload else ""
        ran: list[str] = []
        for i, cmd in enumerate(commands):
            proc = subprocess.run(  # argv list, no shell — by design (§9)
                cmd,
                input=payload_text if i == 0 else None,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            line = self._display([cmd])[0]
            if proc.returncode != 0:
                raise ConfigError(
                    f"{self.app_id}: {line} failed (exit {proc.returncode}) — "
                    f"{(proc.stderr or proc.stdout or 'no output').strip()[:400]}"
                )
            tail = (proc.stdout or "").strip().splitlines()
            ran.append(line + " → ok" + (f" ({tail[-1][:120]})" if tail else ""))
        out["ran"] = ran
        out["note"] = f"applied to {target} ({len(ran)} command(s)) — verify on the node"
        return out
