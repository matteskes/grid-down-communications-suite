# P4 USB round-trip — M12 gate evidence

**Date:** 2026-10-31 (M12, `19fb0f2`). Pack/unpack run on the macOS host
(python3 tarfile + POSIX split — dependency-free, Rosetta-safe discipline);
the install leg in Docker Desktop `--platform linux/amd64`, `--network none`.

## The gate

`tools/roundtrip-usb.sh` — the P4 transport loop end to end:

1. build a real kit (`build-kit.sh --no-pack`; direwolf+fldigi+hamlib);
2. pack it (`tools/make-usb-kit.sh`): deterministic tar (sorted python3
   tarfile) split into FAT32-safe parts (≤ 3800 MiB default) with a
   `usb-manifest.sha256` sidecar over the parts;
3. **wipe the staging tree** — the USB layout is the only source left,
   exactly like a wiped station beside a stick;
4. restore (`tools/unpack-usb-kit.sh`): every part hashed against the
   sidecar, reassembled, extracted (`tarfile` `filter="data"`), then the
   restored tree re-hashed against the kit's OWN `manifest.sha256` — the
   same strict check `gd_kit_verify` enforces at install time, so the tool
   refuses any layout the installer would refuse;
5. tamper probe: one flipped byte in a part must be refused loudly;
6. air-gap install from the restored tree (operator via `--user`):
   bootstrap reaches `install complete`, dialout applied, the units step
   delegates to the kit's control/ suite, re-run is a no-op with
   `state.json` byte-identical.

Gate line: `ROUNDTRIP OK — usb pack/unpack (M12 gate)`.

## Results (2026-10-31, x86_64, no rpmfusion)

| stage | result |
| --- | --- |
| pack | 308 MB kit → 1 part + sidecar (sub-second; multi-part proven by the synthetic smoke below) |
| restore | 655 kit files re-hashed against the kit's manifest — clean (1 s) |
| tamper probe | `FATAL: …tar.part-… hash mismatch (transport corruption)` — refused, nothing extracted |
| install (restored tree) | `install complete`; units delegated into the operator's user unit dir; re-run byte-identical no-op |
| gate | `ROUNDTRIP OK — usb pack/unpack (M12 gate) — 77s total` |

Synthetic smoke (separate, for the multi-part + doctor paths): a tiny kit
with a control/ subtree packed at `--part-mib 1` → 3 parts + sidecar;
restored byte-clean after the source was wiped; `gd doctor --kit` on the
restored tree reports `[ok] kit self-containment (§6.9, M12)`; a live
flipped-byte probe refuses with the part-hash FATAL.

## Design notes (M12 plan bullets vs shipped)

- **Split parts, not one `.tar.zst`** — FAT32 caps a file at 4 GiB − 1, so a
  single ball cannot cross anyway; the payload is already compressed (rpms,
  tar.zst artifacts, zstd prefix), so zstd adds ~nothing while costing a
  host dependency. Plain tar + `split(1)` is dependency-free and
  byte-stable. The plan's README-on-the-stick lives in the tools' headers
  and this file instead.
- **The kit is self-contained first** — control/ + profiles/ ride the KIT
  from the builder (M12), so the USB tools only transport bytes; the suite
  snapshot is not a stick-level assembly step.
- **Symlinks are safe** — they ride inside the tar and are restored on the
  POSIX side; the stick only ever holds flat part files (FAT32-safe).
- **bootstrap takes the restored dir verbatim** (mount-path agnostic, as
  planned); `unpack-usb-kit.sh` is the drills' unpack-from-mount step.

## Companion gates (same tree, same day)

- `ROUNDTRIP OK — incl. postinst (M11d gate) — 78s` (lean) and `— 265s
  (wine=yes)`: the delegation assertions against a real kit — including
  `install: varac  installed  exe: …/drive_c/VarAC/VarAC.exe`, the silent
  kit-import installer running under wine as the operator, air-gapped.
- `P4: PASS` ×3 (container E2E, gates reading the suite + installer from
  $KIT — see `docs/audit/p4-container-e2e.md`).
