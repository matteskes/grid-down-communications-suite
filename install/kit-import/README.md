# kit-import — user-supplied artifacts staging

Drop files here **on the online build machine only**; they get baked into the kit.
This directory ships empty except for this README, and its contents are never
committed (see `.gitignore`).

Expected during Phase 3+:

- `VARA_HF_vX.Y.Z.zip` / `VARA FM` / `VARA SAT` / `VARA Chat` / `VARA Terminal` installers
- `VarAC_vX.Y.Z.zip`
- VARA license registration files (per your VARA support email)

The kit builder fails closed if these are referenced by a recipe but missing (G6).
