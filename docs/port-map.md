# Port map — loopback services (spec §6.6)

Authoritative source: `PORTS` in `control/core/supervisor.py`; the registry
launch blocks define how each service starts. Every port binds `127.0.0.1`
only — nothing listens on a routable interface (G2 air-gap; Doctor scans for
loopback-only binding and port conflicts).

| Port | App | Protocol | Started as | Health check |
|---|---|---|---|---|
| 4532 | hamlib | rigctld TCP (rig control) | managed-service (unit `grid-down-hamlib.service`) | `nc -z 127.0.0.1 4532` |
| 8001 | direwolf | KISS over TCP (APRS/AX.25 frames) | managed-service (unit `grid-down-direwolf.service`) | `nc -z 127.0.0.1 8001` |
| 8090 | commstatone | HTTP static dashboard | local-service (suite-served) | `curl -sf http://127.0.0.1:8090/` |
| 8091 | js8spotter | HTTP (PHP built-in server) | local-service (suite-served) | `curl -sf http://127.0.0.1:8091/` |
| 8092 | meshtastic-web | HTTP static dashboard (+ Web Serial from browser) | local-service (suite-served) | `curl -sf http://127.0.0.1:8092/` |
| 8515 | ardop | ARDOP TNC TCP command port | managed-service (unit `grid-down-ardop.service`) | `nc -z 127.0.0.1 8515` |

## How each one starts

### 4532 — hamlib (`gd start hamlib` / bundle target)
Registry cmd is a placeholder: the supervisor builds rigctld's full argv from
`profile.rig` (`model`, `path`, `baud`; spec §4/§6.4) and always binds
`-T 127.0.0.1 -t 4532`. Member apps reach it via the same loopback address;
the rig singleton is reference-counted against bundle membership (spec §6.4).

### 8001 — direwolf (dnf package, `direwolf`)
`direwolf -c /etc/direwolf.conf -l /var/log/direwolf`. The kit's direwolf.conf
sets `KISSPORT 8001` (AGWPE off). Clients (pat/winlink chains) open KISS
frames on `127.0.0.1:8001`.

### 8090 / 8092 — static dashboards (`commstatone`, `meshtastic-web`)
Suite-served, no app binary: the supervisor runs the host `python3` as
`python3 -m http.server <port> --bind 127.0.0.1 --directory <docroot>` with
cwd set to the docroot. Docroot is `<app_root>/<app_id>` — i.e.
`/opt/grid-down/commstatone` and `/opt/grid-down/meshtastic-web`, staged by
the kit (content itself is operator-supplied, spec §6.8). Refuses to start
when the docroot directory is missing (install-state error, not a crash).

### 8091 — js8spotter (kit tarball app, PHP)
`php -S 127.0.0.1:8091 -t /opt/grid-down/js8spotter` with the docroot as cwd.
Requires `php-cli` on PATH (comes with the kit's dnf recipe set); without it
the launch fails with an install-state error naming the fix, never a
half-started service. Content: JS8Spotter tarball extracted flat into
`/opt/grid-down/js8spotter/`.

### 8515 — ardop (`ardopcf`, M7/M8)
`/opt/grid-down/ardop/ardopcf 8515 <capture> <playback>` — first argument is
the TCP command port, then the ALSA capture/playback devices. Launch args
come from `<state>/app-args/ardop.args` when the M7 config adapter
(`ardop-cfg`) has written it — one arg per line, `#` comments, station tokens
expanded at apply time; the file's args then replace everything after the
binary and audio presets are not consulted. Absent/empty file falls back to
`profile.audio.presets.ardop` (`<capture>`/`<playback>` → the suite-owned
PipeWire endpoint names, spec §6.6 M2 note). Units render from the same
resolution path, and `gd doctor` re-renders — an args-file edit after
`gd units install` is flagged as drift until re-installed.

## Verify round (M8, 2026-10-30, Fedora 45 Workstation VM)

`gd units install` → 12 services + 9 targets, daemon-reload ok;
`systemd-analyze verify grid-down-*.{service,target}` → clean (exit 0, no
output); ardop ExecStart rendered both ways (args file → pinned `hw:`
devices, no file → preset endpoints); commstatone (8090) and meshtastic-web
(8092) launched, `ss -tlnp` confirmed loopback-only binding, `curl` → 200;
js8spotter exercised its php-cli refusal branch (VM offline — happy path
needs `php-cli`, deferred to M10 live E2E); `gd doctor` → 0 fail, units
"current", drift check catches args-file edits. Evidence: this file,
`docs/phase4-plan.md` §M8.
