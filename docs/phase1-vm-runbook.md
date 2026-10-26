# Phase 1 VM runbook — dnf-only kit install in the `fedora-arm-ks` VM

Goal (spec §11 P1 acceptance): **direwolf + fldigi installed from an offline kit
inside the `fedora-arm-ks` VM (aarch64, GNOME) with networking removed.**
The automated half runs in a `--network none` container (`tools/roundtrip-dnf.sh`);
this runbook is the human half.

## 1. Build the aarch64 kit (on the Mac, network on)

```sh
cd grid-down

# optional: automated container proof for aarch64 rpms
tools/roundtrip-dnf.sh --arch aarch64

# the kit for the VM (packed tarball):
install/bundle-builder/build-kit.sh --arch aarch64
#   → kit/grid-down-kit-<date>-aarch64.tar.zst
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

Then detach the NIC in VMPal/VM settings, **or** inside the VM:

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
