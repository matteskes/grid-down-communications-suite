#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
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

# Read a key back (empty when absent; non-string values print as JSON) — the
# M11c re-run guards and resume watermarks read state through this.
gd_state_get() {
    # $1 key
    [ -s "$(gd_state_file)" ] || return 0
    python3 - "$(gd_state_file)" "$1" <<'PYEOF' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
v = data.get(sys.argv[2], "")
print(v if isinstance(v, str) else json.dumps(v))
PYEOF
}

# Record one token under state[group] — a deduplicated list; a non-list value
# already there is replaced (migrates M11a's postinst_done "yes" sentinel).
gd_state_mark() {
    # $1 group, $2 token
    gd_state_init
    python3 - "$(gd_state_file)" "$1" "$2" <<'PYEOF' || gd_warn "state update failed"
import json, sys
path, group, tok = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(path))
lst = data.get(group)
if not isinstance(lst, list):
    lst = []
if tok not in lst:
    lst.append(tok)
data[group] = lst
json.dump(data, open(path, "w"), indent=2, sort_keys=True)
PYEOF
}

# Exit 0 when the token is already recorded — the postinst resume skip.
gd_state_marked() {
    # $1 group, $2 token
    [ -s "$(gd_state_file)" ] || return 1
    python3 - "$(gd_state_file)" "$1" "$2" <<'PYEOF'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
lst = data.get(sys.argv[2])
sys.exit(0 if isinstance(lst, list) and sys.argv[3] in lst else 1)
PYEOF
}
