# Phase 1 VM runbook — dnf-only kit install in a Fedora 45 GUI VM

Goal (spec §11 P1 acceptance): **direwolf + fldigi installed from an offline kit
inside a Fedora 45 VM with a desktop session and networking removed.** Any
hypervisor, either arch — x86_64 is the fleet default; aarch64 kits build on any
host via `--forcearch` (spike S4). Prereq: a stock Fedora 45 VM (netinst,
Workstation, or Server + GNOME group) with SSH or shared-folder access.
The automated half runs in a `--network none` container (`tools/roundtrip-dnf.sh`);
this runbook is the human half.

## 1. Build the kit (on the Mac, network on)

```sh
cd grid-down

# optional: automated container proof for the target arch
tools/roundtrip-dnf.sh --arch aarch64        # or x86_64

# the kit for the VM (packed tarball) — match the VM's arch:
install/bundle-builder/build-kit.sh --arch aarch64   # or x86_64 (default)
#   → kit/grid-down-kit-<date>-<arch>.tar.zst
```

(`--with-rpmfusion` adds ffmpeg + the pinned RPM Fusion enablement; not needed for
the P1 acceptance set.)

## 2. Move the kit into the VM

Shared folder or `scp`:

```sh
scp kit/grid-down-kit-*.tar.zst <user>@<vm>:/var/tmp/
```

## 3. Patch the VM, then air-gap it

The kit resolves against the repo snapshot of its build date; the target must be
patched up to (or past) that snapshot **before** the network goes away, or old
system libraries can conflict with the kit's newer ones (observed: libxml2
soname jump vs an older dnf stack — see `docs/audit/phase1-roundtrip.md`).

```sh
sudo dnf -y update && sudo reboot      # do this while still online
```

Then detach the NIC in your hypervisor's VM settings, **or** inside the VM:

```sh
sudo nmcli networking off
```

## 4. Install

```sh
cd /var/tmp
tar --zstd -xf grid-down-kit-*.tar.zst
KIT=$(ls -d grid-down-kit-*/ | head -1)

sudo "./$KIT/install/bootstrap.sh" --kit "./$KIT" --dry-run   # inspect the plan
sudo "./$KIT/install/bootstrap.sh" --kit "./$KIT"             # install
```

Expected: `kit manifest verified: N files` → dnf resolves only from
`file://…/repo` (`--disablerepo='*' --enablerepo=gd-kit`) → `install complete`.

## 5. Verify

```sh
rpm -q direwolf fldigi
direwolf -h | head -3            # usage banner = binary runs
```

GUI (session has audio via PipeWire): launch **fldigi** from the app grid;
`direwolf` is a service-style modem — full TNC bring-up is a Phase 2 concern.

## 6. Report

Paste the bootstrap + verification output into
`docs/audit/phase1-roundtrip.md` (§ *VM acceptance*) — that closes P1.
