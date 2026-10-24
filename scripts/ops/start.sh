#!/usr/bin/env bash
set -Eeuo pipefail
source "${TRADER_ROOT:-/root/trader}/scripts/ops/common.sh"
exec >>"$REPO/logs/automation_${TODAY}.log" 2>&1
echo "[$(date --iso-8601=seconds)] automatic start requested"
exec 9>"$LOCK_DIR/.eod_close.lock"
flock -sn 9 || { echo "EOD close-out active; start skipped"; exit 75; }
exec 8>"$LOCK_DIR/.session.lock"
flock -n 8 || { echo "Session lock held; duplicate start skipped"; exit 0; }
finish() {
    rc=$?
    trap - EXIT
    touch "$REPO/KILL_SWITCH"
    rm -f "$ARMED_FILE"
    if test "$rc" -ne 0; then
        ops_alert start_or_runner_failed "Session launcher or runner failed for $TODAY (exit $rc)."
    fi
    exit "$rc"
}
trap finish EXIT
test -f "$ARMED_FILE" || { echo "ABORT: no preflight arming marker"; exit 76; }
read -r ARMED_DATE ARMED_SHA < "$ARMED_FILE"
test "$ARMED_DATE" = "$TODAY"
test "$ARMED_SHA" = "$(git rev-parse HEAD)"
test ! -f "$REPO/KILL_SWITCH"
"$PYTHON" scripts/session_ops.py check-config
calendar_rc=0
"$PYTHON" scripts/session_ops.py calendar || calendar_rc=$?
if test "$calendar_rc" = 3; then exit 0; fi
test "$calendar_rc" = 0
"$PYTHON" scripts/capture_session_fingerprint.py --verify --check-broker
if ! curl -sf --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    nohup "$PYTHON" main.py dashboard 8>&- 9>&- >>"$REPO/logs/dashboard.log" 2>&1 </dev/null &
fi
rm -f "$ARMED_FILE"
export SESSION_LOG_REDIRECTED=1
echo "[$(date --iso-8601=seconds)] launching session"
rc=0
# EOD must be able to request graceful shutdown while the runner owns lock 8.
# A close-out racing this transition detects the held session lock and fails closed.
flock -u 9
exec 9>&-
"$PYTHON" -u scripts/session_runner.py --eval --poll 30 --reconcile-interval 10 >>"$REPO/logs/session_${TODAY}.log" 2>&1 || rc=$?
printf '%s\n' "$rc" >"$REPO/logs/session_${TODAY}.exitcode"
echo "[$(date --iso-8601=seconds)] runner exited rc=$rc"
exit "$rc"
