#!/usr/bin/env bash
# Sourced by managed wrappers. This file never places orders.
set -Eeuo pipefail
export TZ=America/New_York
REPO="${TRADER_ROOT:-/root/trader}"
PYTHON="$REPO/.venv/bin/python"
LOCK_DIR="${TRADER_LOCK_DIR:-/root}"
ARMED_FILE="$LOCK_DIR/.session_armed"
TODAY="$(date +%F)"
cd "$REPO"
mkdir -p "$REPO/logs" "$LOCK_DIR"
set -a
source "$REPO/.env"
if test -f "${TRADER_OPS_ENV:-/root/trader-ops.env}"; then
    source "${TRADER_OPS_ENV:-/root/trader-ops.env}"
fi
set +a
export BROKER=alpaca LIVE_TRADING_ENABLED=false PAPER_EVALUATION_MODE=true
export TRADIER_CONTRACT_GATE_MODE=off TRADIER_MARKET_DATA_MODE=observe
export POSITION_TRAILING_ACTIVATION_PCT=0.25
export EVALUATION_OUTPUT_DIR="${TRADER_EVIDENCE_DIR:-/root/trader-evidence}"
export EVALUATION_LEDGER_FILE="$EVALUATION_OUTPUT_DIR/ledger.json"
export MARKET_DATA_COMPARISON_FILE="$EVALUATION_OUTPUT_DIR/market_data_comparisons.jsonl"
ops_alert() {
    "$PYTHON" scripts/session_ops.py alert --code "$1" --message "$2" || true
}
