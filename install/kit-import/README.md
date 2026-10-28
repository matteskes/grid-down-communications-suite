# kit-import — user-supplied artifacts staging

Drop files here **on the online build machine only**; they get baked into the kit.
This directory's contents are never committed (see `.gitignore`) and installers run
in place from here, never copied into the repo or a kit (G6).

## Staged (2026-10-27, official sources)

| File | sha256 | Source |
|---|---|---|
| `VARA HF v4.9.0  setup.zip` (double space, as published) | `5ad7d75c722e4414705dec998c28a711b7567f8568bea75cb84e8aa7c991f48a` | downloads.winlink.org `VARA Products/` |
| `VARA FM v4.4.0 setup.zip` | `5c3ee6a6a124e25aaabf5e494b33d268d37b21e4f1462e798f271070a6bc5915` | downloads.winlink.org `VARA Products/` |
| `VarAC v16.1.8.zip` (portable — no installer) | `e543116ade4b7c920f52869d4509d31a702634eef341f68f37e0612da38241b7` | varac-hamradio.com `download-varac-zip-package` |

The two VARA zips wrap an Inno Setup exe (`VARA … setup (Run as Administrator).exe`)
that `gd winehub install <app>` runs with `/VERYSILENT` switches. The VarAC zip is
a plain portable bundle (`installer: portable` in its recipe): it is unpacked into
`drive_c\VarAC`, never executed.

## Still operator-supplied (not in any public mirror)

- `VARA SAT v4.4.5 setup.zip` — downloads.winlink.org `VARA Products/`
- `VARA Chat v1.4.3 setup.zip` — downloads.winlink.org `VARA Products/`
- `VARA Terminal v1.2.2 setup.zip` — downloads.winlink.org `VARA Products/`
- VARA license registration (per your VARA support email; applied in-app, G6)

The kit builder fails closed if a recipe's `installer_glob` matches nothing here (G6).

