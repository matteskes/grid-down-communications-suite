# Config adapters (Phase 3/4)

One module per app that writes the app's **native** config format (spec §6.2/G3):

- contract: `read() -> dict`, `apply(profile, dry_run) -> diff`
- mandatory: timestamped `backup()` + atomic write (tmpfile + `os.replace`)
- `gd config apply <app>` prints the unified diff before writing

Adapter ids are referenced by registry entries (`config.adapter`). Phase 0 stubs
carry `adapter: none` or a placeholder id — implementations arrive with each app.
