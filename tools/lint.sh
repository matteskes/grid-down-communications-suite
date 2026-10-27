#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Grid Down Authors — see LICENSE at the repository root.
# Style gates (spec §10 hygiene): ruff lint → black --check → isort --check-only.
# Wired into git via tools/pre-commit; also runnable by hand. Tooling lives in
# .venv and is pinned in requirements-dev.txt.
set -eu
cd "$(git rev-parse --show-toplevel)"

if [ ! -x .venv/bin/ruff ]; then
    echo "lint: .venv tooling missing — run: .venv/bin/pip install -r requirements-dev.txt" >&2
    exit 1
fi

status=0
echo "==> ruff check control tests"
.venv/bin/ruff check control tests || status=1
echo "==> black --check control tests"
.venv/bin/black --check control tests || status=1
echo "==> isort --check-only control tests"
.venv/bin/isort --check-only control tests || status=1

if [ "$status" -ne 0 ]; then
    echo "lint: FAILED — auto-fix with: .venv/bin/isort control tests && .venv/bin/black control tests && .venv/bin/ruff check --fix control tests" >&2
fi
exit "$status"
