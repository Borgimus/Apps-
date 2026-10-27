#!/usr/bin/env bash
# Free read-only equity observer; never calls the options session launcher.
set -Eeuo pipefail
source "${TRADER_ROOT:-/root/trader-release}/scripts/ops/common.sh"
export TRADER_RUN_MODE=equity_research
exec >>"$REPO/logs/equity_research_${TODAY}.log" 2>&1
"$PYTHON" scripts/equity_research.py "$@"
