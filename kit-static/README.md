# kit-static/ — operator-staged static payload content

Static-strategy apps ship an empty payload slot in the kit; the *content* is
operator-supplied (spec §6.8). Stage it here, one directory per app id:

    kit-static/<app-id>/...   ← mirrors kit/static/<app-id>/ in the kit

Layout rules:

- `build-kit.sh` bind-mounts this directory read-only into the build container
  (`/kit-static`); `container-build.sh` copies each non-empty `<app-id>/` into
  the kit's `static/<id>/` **before** `manifest.sha256` is hashed, so operator
  content is kit-manifest-verified air-gap side (`gd_kit_verify` is strict).
- Empty or missing directory → empty slot (bootstrap warns; `gd doctor` WARNs).
- Nothing here is committed (`.gitignore`: everything but this README).

The Phase 4 M10 container E2E stages `meshtastic-web/` (the mesh bundle's
localhost web client) — `tools/container-p4-e2e.sh` fetches the pinned
upstream release, verifies its sha256, and extracts it flat into
`kit-static/meshtastic-web/` when this directory lacks the content.
