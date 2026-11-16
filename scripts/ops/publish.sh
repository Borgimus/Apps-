#!/usr/bin/env bash
set -Eeuo pipefail
source "${TRADER_ROOT:-/root/trader}/scripts/ops/common.sh"
exec >>"$REPO/logs/publication_${TODAY}.log" 2>&1
"$PYTHON" scripts/session_ops.py publish --date "${1:-$TODAY}"
