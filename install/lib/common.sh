#!/usr/bin/env bash
# Grid Down install-library helpers (Phase 0).
# Sourced by bootstrap.sh and bundle-builder scripts. No side effects on source.

GD_LOG_TAG="${GD_LOG_TAG:-grid-down}"

gd_log()  { printf '[%s] %s %s\n' "$GD_LOG_TAG" "$(date -u +%H:%M:%S)" "$*" >&2; }
gd_info() { gd_log "INFO   " "$*"; }
gd_warn() { gd_log "WARN   " "$*"; }
gd_err()  { gd_log "ERROR  " "$*"; }
gd_die()  { gd_err "$*"; exit 1; }

gd_require_root() {
    [ "$(id -u)" -eq 0 ] || gd_die "must run as root (use sudo)"
}

gd_is_fedora() {
    [ -r /etc/fedora-release ]
}

gd_state_dir()  { printf '%s' "${GD_STATE_DIR:-/var/lib/grid-down}"; }
gd_state_file() { printf '%s' "$(gd_state_dir)/state.json"; }

gd_state_init() {
    mkdir -p "$(gd_state_dir)"
    [ -s "$(gd_state_file)" ] || printf '{}\n' > "$(gd_state_file)"
}

# Record a key/value pair into state.json via python3 (Phase 0: best effort).
gd_state_set() {
    # $1 key, $2 value
    gd_state_init
    python3 - "$(gd_state_file)" "$1" "$2" <<'PYEOF' || gd_warn "state update failed"
import json, sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(path))
data[key] = val
json.dump(data, open(path, "w"), indent=2, sort_keys=True)
PYEOF
}
