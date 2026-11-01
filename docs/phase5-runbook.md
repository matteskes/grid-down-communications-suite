# Phase 5 VM runbook — the human clean-host rebuild from the M12 USB layout

Goal (spec §10 P5, phase5-plan M13): P5's second leg — a person executes one
clean-host rebuild on a fresh x86_64 Fedora 45 GUI VM, starting from the M12
USB layout and nothing else, and commits the evidence. The first leg is
automated: `tools/drill-clean-rebuild.sh` runs N air-gapped container rebuilds
to a "P5: PASS" banner each (results in `docs/audit/p5-clean-rebuild.md`).
This runbook is the field-shaped version of the same drill: a real desktop, a
real user account, the network unplugged by hand, and the layout's bytes moved
the way a field operator would move them.

| Lane | Container drill (agent) | This runbook (human) |
|---|---|---|
| Carrier | fresh fedora:45 container, `--network none` | fresh F45 x86_64 GUI VM, NIC detached by hand |
| Source | `usb-kit/` mounted read-only | the same layout copied in (or written to a FAT32 stick) |
| Install user | container root + postinst `operator` | your sudo user, end to end |
| Gate driver | `tools/p5-drill-gates.sh` (G0–G8 → `P5: PASS`) | the §5 checks, by hand |
| Evidence | run logs (`gd-p5-drill-logs/`) | §6 transcripts → `docs/audit/p5-clean-rebuild.md` |

## 0. What you need

- The repo at HEAD on the Mac, and the built layout at `grid-down/usb-kit/`
  (`gd-kit-<date>-x86_64.tar.part-*` + `usb-manifest.sha256`). If it is
  missing: `tools/drill-clean-rebuild.sh --runs 1` rebuilds it (build alone is
  ~45–70 min under Rosetta; the layout needs ~2 × 4 GiB of space in the VM).
- A fresh Fedora 45 **x86_64** VM (UTM or VMPal): ≥ 8 GB RAM, ≥ 40 GB disk, a
  sudo user you can log into on the desktop.
- ~30–45 min of attention.

## 1. Prep (record from the top)

Log into the VM's desktop and record the baseline:

```sh
uname -m                 # x86_64 — the field kit is x86_64-only
cat /etc/fedora-release  # Fedora release 45
df -h /                  # ≥ 20 GB free under /
id                       # your user, uid >= 1000 (the install target)
```

## 2. Move the suite + the layout into the VM (two-end sha256 discipline)

The VM needs `tools/unpack-usb-kit.sh` (in the suite) and the layout. Move the
suite with the P3 recipe, the layout with `vmpal cp` (or a FAT32 stick):

```sh
# on the Mac:
cd grid-down
git archive --format=tar.gz -o /tmp/grid-down-suite.tar.gz HEAD
shasum -a 256 /tmp/grid-down-suite.tar.gz                 # copy for the two-end check
vmpal cp /tmp/grid-down-suite.tar.gz "<VM>":/var/tmp/
shasum -a 256 usb-kit/* > /tmp/usb-kit-sha256.txt         # two-end record
vmpal cp usb-kit "<VM>":/var/tmp/
# on the guest:
sha256sum /var/tmp/grid-down-suite.tar.gz                 # must match
mkdir -p /var/tmp/grid-down && tar -xzf /var/tmp/grid-down-suite.tar.gz -C /var/tmp/grid-down
cd /var/tmp/grid-down && sha256sum -c /tmp/usb-kit-sha256.txt   # from a dir where the paths resolve
```

Full-fidelity variant: write `usb-kit/*` onto a ≥ 8 GB FAT32 stick on the Mac
and mount it in the VM instead of `vmpal cp` — the sidecar verify in §4 pins
the same bytes either way.

Never unpack with macOS bsdtar into the VM and never skip the two sha256s —
the P3 transfer discipline carries over verbatim.

## 3. Unplug the network BEFORE unpacking

Detach the VM's NIC (UTM/VMPal: network → none/disconnect, or pull the shared
bridge). Record the proof:

```sh
ip addr   # no carrier / no global addresses on any NIC — screenshot this
```

From here to the end of §5 nothing touches a network. That is the point of the
leg: the stick's bytes are the only input.

## 4. Unpack + install (the kit's own tools, as a real user)

```sh
cd /var/tmp/grid-down
python3 tools/unpack-usb-kit.sh --from /var/tmp/usb-kit --out /var/tmp/restore
```

Expected: per-part sha256 verify, reassembly, kit-manifest re-verify — and a
hard refuse on any tampered part. Then install:

```sh
sudo bash /var/tmp/restore/kit-staging/install/bootstrap.sh \
    --kit /var/tmp/restore/kit-staging --user "$USER" 2>&1 | tee /var/tmp/p5-install.log
```

Expected ending: `install complete:` with the app list, the kit line, and the
`next: gd doctor · gd units install` hints. (dnf runs against the kit repo
only; payloads land under /opt/grid-down; postinst renders your units and
installs the VARA wine apps onto the shared prefix.)

## 5. The human gates

`gd` is the kit's own suite — set it once per shell:
`gd() { python3 /var/tmp/restore/kit-staging/control/cli/gd.py "$@"; }`

| # | Check | Pass looks like |
|---|---|---|
| H1 | `cat /var/tmp/restore/kit-staging/ARCH` | `x86_64` (the drill's G1 proves the wrong-arch refusal; here we record the field kit's arch) |
| H2 | `gd apps list \| wc -l` | `38` — the full M13 set (41 registry ids minus the three unstaged VARA imports) |
| H3 | `gd doctor; echo $?` | exit `0`; WARN lines are caveats to record, not failures |
| H4 | `gd units list` | your rendered `grid-down-*` set, no unresolved rows |
| H5 | `gd winehub status` | `prefix: present (/opt/grid-down/wine-prefix)`; `vara-hf`, `vara-fm`, `varac` `installed` |
| H6 | `rpm -q hamlib fldigi direwolf wine wsjtx js8call audacity kiwix-desktop` | every canary installed (dnf closure from the kit repo) |
| H7 | `ls ~/.config/systemd/user/grid-down-*.service \| wc -l` | ≥ 1 (units rendered for YOUR user, not root's) |
| H8 | `id -nG "$USER" \| tr ' ' '\n' \| grep -x dialout` | `dialout` |

Record each line's output verbatim (§6) — that transcript is the evidence.

## 6. Evidence + report

```sh
mkdir -p /var/tmp/grid-down-evidence/p5
# tee the §5 table's commands into files there (doctor.txt, winehub-status.txt, …)
# on the Mac:
vmpal cp "<VM>":/var/tmp/grid-down-evidence/p5 /tmp/
vmpal cp "<VM>":/var/tmp/p5-install.log /tmp/
```

Paste the transcript + timings (unpack, install, §5 total) into
`docs/audit/p5-clean-rebuild.md` under **human leg**, then commit. The audit
doc's caveats section carries whatever WARN classes H3 surfaced.

## Caveats

- The VM needs no GUI acceleration and no extra packages: unpack needs
  bash + sha256sum + python3, all stock F45.
- H5's wine apps install headless (the same path the container drill uses).
  If a VARA setup.exe refuses silent mode under wine, record the stderr as a
  caveat — the container drill's first run is the canary for this; see the
  audit doc before assuming the VM is at fault.
- This runbook assumes the automated leg already passed (its findings are
  fixed at HEAD). Run §0's rebuild command first if `usb-kit/` is stale.