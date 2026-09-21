#!/usr/bin/env bash
# Graceful runner shutdown, full-account read-only verification and evidence upload.
set -Eeuo pipefail
source "${TRADER_ROOT:-/root/trader}/scripts/ops/common.sh"
exec >>"$REPO/logs/automation_${TODAY}.log" 2>&1
echo "[$(date --iso-8601=seconds)] automatic EOD close-out requested"
exec 9>"$LOCK_DIR/.eod_close.lock"
flock -n 9 || { echo "EOD/start/preflight operation already active; close-out skipped"; exit 75; }
FAILURE="$REPO/logs/AUTOMATION_FAILURE_${TODAY}.txt"
finish() {
    rc=$?
    trap - EXIT
    touch "$REPO/KILL_SWITCH"
    rm -f "$ARMED_FILE"
    if test "$rc" -ne 0; then
        printf 'EOD close-out incomplete at %s (exit %s). Review automation log.\n' \
            "$(date --iso-8601=seconds)" "$rc" >>"$FAILURE"
        ops_alert eod_failed "EOD close-out incomplete for $TODAY. Review runner, broker and publication status."
    fi
    exit "$rc"
}
trap finish EXIT
# The kill switch blocks new entries while the runner can still manage exits.
touch "$REPO/KILL_SWITCH"
rm -f "$ARMED_FILE"
runner_pids() {
    # Both absolute and relative script arguments used by the launchers match.
    pgrep -f '[s]cripts/session_runner[.]py([[:space:]]|$)' 2>/dev/null || true
}
wait_for_runner_exit() {
    local seconds="$1" elapsed=0
    while test -n "$(runner_pids)" && test "$elapsed" -lt "$seconds"; do
        sleep 5
        elapsed=$((elapsed + 5))
    done
    test -z "$(runner_pids)"
}
if test -n "$(runner_pids)"; then
    echo "Runner active; allowing 120s natural grace"
    wait_for_runner_exit 120 || true
fi
if test -n "$(runner_pids)"; then
    mapfile -t pids < <(runner_pids)
    for pid in "${pids[@]}"; do
        if [[ "$pid" =~ ^[0-9]+$ ]]; then kill -TERM "$pid" 2>/dev/null || true; fi
    done
    echo "Graceful SIGTERM requested; allowing 180s"
    wait_for_runner_exit 180 || true
fi
BROKER_CLEAN=0
if "$PYTHON" scripts/session_ops.py verify-flat; then BROKER_CLEAN=1; fi
if test -n "$(runner_pids)"; then
    printf 'Runner remained active after grace and SIGTERM; no SIGKILL issued. Broker-flat snapshot=%s.\n' "$BROKER_CLEAN" >"$FAILURE"
    echo "CRITICAL: runner still active; preserving process, local evidence and failure marker"
    ops_alert runner_shutdown_failed "Runner did not stop for $TODAY. No SIGKILL issued. Broker-flat snapshot=$BROKER_CLEAN. Manual review required."
    # Repeat the independent broker check, but a clean snapshot cannot clear this failure.
    "$PYTHON" scripts/session_ops.py verify-flat || true
    exit 1
fi
# Wait briefly for the launcher's exit trap, and exclude any concurrent new runner.
exec 8>"$LOCK_DIR/.session.lock"
flock -w 10 8 || { echo "CRITICAL: session lock remains held"; exit 75; }
if test -n "$(runner_pids)"; then echo "CRITICAL: runner appeared during close-out"; exit 1; fi
RESULT=0
if "$PYTHON" scripts/session_ops.py verify-flat; then
    BROKER_CLEAN=1
    rm -f "$FAILURE"
    echo "Broker EOD state clean: 0 positions, 0 open orders"
else
    BROKER_CLEAN=0
    RESULT=1
    echo "EOD broker verification failed. Manual intervention required." >"$FAILURE"
    ops_alert eod_exposure_unverified "EOD broker verification failed for $TODAY. Manual intervention required."
fi
if "$PYTHON" scripts/session_ops.py check-config; then
    if ! "$PYTHON" scripts/shadow_report.py --date "$TODAY" >"$REPO/logs/shadow_report_${TODAY}.txt" 2>&1; then
        RESULT=1
        ops_alert shadow_report_failed "EOD shadow analysis failed for $TODAY. Raw evidence is preserved."
    fi
else
    RESULT=1
    echo "Operations configuration incomplete; external shadow analysis skipped"
fi
# The publisher takes the session lock itself. Keep the EOD lock until exit so
# a new launcher cannot race the final kill-switch/arming-marker cleanup.
flock -u 8
exec 8>&-
# Archive raw evidence even when broker verification or analysis failed.
if ! "$PYTHON" scripts/session_ops.py publish --date "$TODAY"; then
    RESULT=1
    ops_alert artifact_publication_failed "Artifact publication failed for $TODAY. Local archive is preserved."
fi
if test "$RESULT" -ne 0; then exit "$RESULT"; fi
echo "[$(date --iso-8601=seconds)] automatic EOD close-out complete"
