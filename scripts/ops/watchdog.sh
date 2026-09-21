#!/usr/bin/env bash
set -Eeuo pipefail
# Monitoring must still start when preflight's shell environment fails to load.
export TZ=America/New_York BROKER=alpaca LIVE_TRADING_ENABLED=false
REPO="${TRADER_ROOT:-/root/trader}"
TODAY="$(date +%F)"
cd "$REPO"
mkdir -p "$REPO/logs"
exec >>"$REPO/logs/watchdog_${TODAY}.log" 2>&1
"$REPO/.venv/bin/python" scripts/session_ops.py watchdog
