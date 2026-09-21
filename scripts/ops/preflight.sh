#!/usr/bin/env bash
set -Eeuo pipefail
source "${TRADER_ROOT:-/root/trader}/scripts/ops/common.sh"
exec >>"$REPO/logs/preflight_${TODAY}.log" 2>&1
echo "=== PREFLIGHT $(date --iso-8601=seconds) ==="
OWN_STATE=0
finish() {
    rc=$?
    trap - EXIT
    if test "$rc" -ne 0; then
        if test "$OWN_STATE" = 1; then
            touch "$REPO/KILL_SWITCH"
            rm -f "$ARMED_FILE"
        fi
        ops_alert preflight_failed "Preflight failed for $TODAY (exit $rc). Session was not armed by this run."
    fi
    exit "$rc"
}
trap finish EXIT
if test "$#" -gt 1 || { test "$#" = 1 && test "$1" != --check-only; }; then
    echo "Usage: preflight.sh [--check-only]"
    exit 64
fi
# Acquire the same lock as the launcher BEFORE changing the kill switch.
exec 9>"$LOCK_DIR/.eod_close.lock"
flock -sn 9 || exit 75
exec 8>"$LOCK_DIR/.session.lock"
flock -n 8 || exit 75
exec 7>"$LOCK_DIR/.session-preflight.lock"
flock -n 7 || exit 75
if pgrep -af '[s]cripts/session_runner.py'; then exit 76; fi
OWN_STATE=1
touch "$REPO/KILL_SWITCH"
rm -f "$ARMED_FILE"
BRANCH=agent/paper-scaled-sizing-250-10
git fetch origin "$BRANCH"
test "$(git branch --show-current)" = "$BRANCH"
LOCAL_SHA="$(git rev-parse HEAD)"
test "$LOCAL_SHA" = "$(git rev-parse "origin/$BRANCH")" || {
    echo "FAIL: code differs from remote; explicit deployment is required"
    exit 78
}
"$PYTHON" scripts/session_ops.py check-config
"$PYTHON" -m pytest tests/test_session_safety_hardening.py tests/test_position_manager.py tests/test_market_data_observer.py -q -p no:warnings
"$PYTHON" scripts/capture_session_fingerprint.py --verify --check-broker
calendar_rc=0
"$PYTHON" scripts/session_ops.py calendar || calendar_rc=$?
if test "$calendar_rc" = 3; then
    echo "MARKET CLOSED: session remains disarmed"
    exit 0
fi
test "$calendar_rc" = 0
if test "${1:-}" = --check-only; then
    echo "CHECK-ONLY PASS: kill switch remains active"
    exit 0
fi
umask 077
printf '%s %s\n' "$TODAY" "$LOCAL_SHA" >"$ARMED_FILE.tmp"
mv "$ARMED_FILE.tmp" "$ARMED_FILE"
rm -f "$REPO/KILL_SWITCH"
echo "PREFLIGHT PASS: session armed for $TODAY at $LOCAL_SHA"
