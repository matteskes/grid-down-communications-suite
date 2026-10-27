# Phase 2 VM acceptance — GUI + systemd user-unit pass

Spec §11 P2. Runbook: `docs/phase2-vm-runbook.md` · backend: `control/core/systemd.py`
(M4a) + supervisor parity (M4b) · GUI: `control/gui/` (M3a–c). Dev-side evidence
below is already recorded; the human half is the GUI-VM pass (§ VM acceptance).

## Dev-box / container evidence (2026-10-26)

- **Unit render + verify**: `tools/systemd-analyze-verify.sh` — 2 services +
  9 targets on the dev box (29 unresolved apps get no unit by design, 10
  wine-hub/cli-tool skipped); `systemd-analyze verify rc=0` inside
  `fedora:45`, systemd 262.1-1.fc45. Full transcript + the ordering-cycle
  design fix it caught: `docs/audit/f45-package-audit.md` § *M4 systemd unit
  verify*.
- **Backend tests**: 96/96 suite green (systemd backend driven through a fake
  `systemctl` shim; rendering, drift, outliving check, launch/stop parity).
- **Tooling gates**: ruff + black + isort clean (`tools/lint.sh`, pre-commit
  hook installed); selfcheck unchanged: apps=41, bundles=9, doctor checks=18.

## VM acceptance (Fedora 45 GUI VM, aarch64 or x86_64)

Status: **PENDING (human half)** — runbook: `docs/phase2-vm-runbook.md`.

Checklist to record here (paste outputs verbatim):

| # | Gate (runbook §) | Expected | Observed |
|---|---|---|---|
| 1 | Kit install incl. js8call + qsstv (§4) | `install complete`; `rpm -q` ×5 ok | |
| 2 | `gd doctor` full pass (§5) | fail=0, real PipeWire | |
| 3 | `gd audio up` (§5) | 3 `grid_down_*` sinks + 3 sources | |
| 4 | Direct backend launch/stop (§5) | fldigi up → down; packet partial-start notes | |
| 5 | `gd units install` (§6) | 5 services + 9 targets, daemon-reload ok | |
| 6 | **`BindsTo=` stop propagation on the real bus (§6)** | `gd stop sstv` kills qsstv; target + service inactive | |
| 7 | Log capture (§6) | suite log + `journalctl --user` agree | |
| 8 | Auto backend resolution (§6) | `gd status` reports `backend: systemd` | |
| 9 | Doctor systemd check (§6) | ok — "current with registry/profile" | |
| 10 | `gd gui --selfcheck` (§7) | real Wayland window, exit 0 | |
| 11 | Five views (§7) | Dashboard / Bundles / Catalog / Doctor / Profiles all render | |
| 12 | **Bundle start/stop from the GUI (§7 — the P2 acceptance)** | sstv start+stop via Bundles tiles | |

## Notes / caveats

- The dev-box unit counts differ from the VM by design: only kit-installed
  apps resolve to services (2 on the dev box, 5 on the VM); targets always
  render for all 9 bundles, omitting unresolved members.
- `hamlib` (rigctld) is the suite-owned singleton: it gets a service but no
  `BindsTo=` and is reference-counted at bundle stop — it stops with the last
  rig-dependent app (§6.4).
- No lingering/`loginctl enable-linger` step: user units run under the active
  GNOME session; headless persistence is out of P2 scope.
