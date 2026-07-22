#!/usr/bin/env bash
# Install or refresh unattended Phase 3 session automation on an existing VPS.
#
# This script only writes helper scripts under $HOME and updates the user's
# crontab. It does not modify the frozen trading branch or trading configuration.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/trader}"
START_TIME_HOUR="${START_TIME_HOUR:-9}"
START_TIME_MINUTE="${START_TIME_MINUTE:-30}"
CLOSE_TIME_HOUR="${CLOSE_TIME_HOUR:-12}"
CLOSE_TIME_MINUTE="${CLOSE_TIME_MINUTE:-35}"

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -d "$REPO_DIR/.git" ] || fail "Trading repository not found at $REPO_DIR"
[ -x "$REPO_DIR/.venv/bin/python" ] || fail "Python venv not found at $REPO_DIR/.venv"
[ -f "$REPO_DIR/scripts/session_runner.py" ] || fail "session_runner.py not found"
[ -f "$REPO_DIR/scripts/capture_session_fingerprint.py" ] || fail "fingerprint verifier not found"

say "Setting server timezone to America/New_York"
CURRENT_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
if [ "$CURRENT_TZ" != "America/New_York" ]; then
  if command -v sudo >/dev/null 2>&1; then
    sudo timedatectl set-timezone America/New_York
  elif [ "$(id -u)" = "0" ]; then
    timedatectl set-timezone America/New_York
  else
    fail "Timezone is $CURRENT_TZ and cannot be changed without sudo/root"
  fi
fi

say "Writing idempotent 09:30 ET session launcher"
cat > "$HOME/start_session.sh" <<'START_SCRIPT'
#!/usr/bin/env bash
# Automatic Phase 3 launcher. Safe to run repeatedly: flock permits one runner.
set -euo pipefail
export TZ=America/New_York
REPO="$HOME/trader"
TODAY="$(date +%F)"
mkdir -p "$REPO/logs"
AUTO_LOG="$REPO/logs/automation_${TODAY}.log"
exec >>"$AUTO_LOG" 2>&1

echo "[$(date --iso-8601=seconds)] automatic start requested"
cd "$REPO"

# Hold this lock for the full session. A duplicate cron/manual start exits cleanly.
exec 9>"$HOME/.session.lock"
flock -n 9 || { echo "[$(date --iso-8601=seconds)] session already running; no action"; exit 0; }

if [ -f "$REPO/KILL_SWITCH" ]; then
  echo "[$(date --iso-8601=seconds)] ABORT: KILL_SWITCH is active"
  exit 1
fi

# Cron runs on weekdays, and this broker-calendar gate skips exchange holidays.
set +e
"$REPO/.venv/bin/python" - <<'PY'
import asyncio
import sys

async def main() -> int:
    from app.config import get_settings
    from app.brokers import get_broker

    settings = get_settings()
    if settings.live_trading_enabled:
        print("ABORT: LIVE_TRADING_ENABLED=true")
        return 1

    broker = get_broker(settings)
    try:
        ok, reason = broker.verify_paper_endpoint()
        if not ok:
            print(f"ABORT: paper endpoint verification failed: {reason}")
            return 1
        account = await broker.get_account()
        if not account.is_paper:
            print("ABORT: broker account is not paper")
            return 1
        is_session = await broker.is_market_session_today()
        if not is_session:
            print("Market calendar reports no trading session today; skipping launch")
            return 3
        print("Market calendar and paper-account gate passed")
        return 0
    finally:
        await broker.close()

raise SystemExit(asyncio.run(main()))
PY
GATE_RC=$?
set -e
case "$GATE_RC" in
  0) ;;
  3) exit 0 ;;
  *) echo "[$(date --iso-8601=seconds)] ABORT: market/paper gate failed"; exit "$GATE_RC" ;;
esac

# The frozen-code, clean-tree, connectivity, and broker 0/0 gate must pass daily.
"$REPO/.venv/bin/python" scripts/capture_session_fingerprint.py --verify --check-broker

# Keep the read-only monitoring API available over Tailscale.
if ! curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  nohup "$REPO/.venv/bin/python" main.py dashboard >> "$REPO/logs/dashboard.log" 2>&1 &
  sleep 3
fi

SESSION_LOG="$REPO/logs/session_${TODAY}.log"
echo "[$(date --iso-8601=seconds)] launching Phase 3 runner -> $SESSION_LOG"
set +e
PAPER_EVALUATION_MODE=true "$REPO/.venv/bin/python" -u scripts/session_runner.py \
  --eval --poll 30 --reconcile-interval 10 >> "$SESSION_LOG" 2>&1
RUN_RC=$?
set -e
echo "[$(date --iso-8601=seconds)] runner exited rc=$RUN_RC"
printf '%s\n' "$RUN_RC" > "$REPO/logs/session_${TODAY}.exitcode"
exit "$RUN_RC"
START_SCRIPT

say "Writing safe 12:35 ET shutdown and close-out watchdog"
cat > "$HOME/auto_close_session.sh" <<'CLOSE_SCRIPT'
#!/usr/bin/env bash
# Ensure the runner has terminated, verify broker 0/0, generate shadow analysis,
# and commit/push raw artifacts. Never SIGKILLs while broker exposure is present.
set -uo pipefail
export TZ=America/New_York
REPO="$HOME/trader"
TODAY="$(date +%F)"
mkdir -p "$REPO/logs"
AUTO_LOG="$REPO/logs/automation_${TODAY}.log"
exec >>"$AUTO_LOG" 2>&1

echo "[$(date --iso-8601=seconds)] automatic EOD close-out requested"
cd "$REPO" || exit 1
exec 8>"$HOME/.eod_close.lock"
flock -n 8 || { echo "[$(date --iso-8601=seconds)] EOD close-out already running"; exit 0; }

runner_pids() {
  pgrep -f '[/]scripts/session_runner.py' 2>/dev/null || true
}

wait_for_runner_exit() {
  local seconds="$1"
  local elapsed=0
  while [ -n "$(runner_pids)" ] && [ "$elapsed" -lt "$seconds" ]; do
    sleep 5
    elapsed=$((elapsed + 5))
  done
  [ -z "$(runner_pids)" ]
}

# Normal behavior is self-termination at 12:30. Give post-session work time first.
if [ -n "$(runner_pids)" ]; then
  echo "[$(date --iso-8601=seconds)] runner still active; allowing 120s natural grace"
  wait_for_runner_exit 120 || true
fi

# If still alive, request the runner's built-in graceful SIGTERM shutdown path.
if [ -n "$(runner_pids)" ]; then
  PIDS="$(runner_pids | tr '\n' ' ')"
  echo "[$(date --iso-8601=seconds)] runner exceeded grace; sending SIGTERM to $PIDS"
  kill -TERM $PIDS 2>/dev/null || true
  wait_for_runner_exit 180 || true
fi

BROKER_CLEAN=0
if "$REPO/.venv/bin/python" scripts/capture_session_fingerprint.py --check-broker; then
  BROKER_CLEAN=1
fi

# A wedged process can be killed only after the broker independently confirms 0/0.
if [ -n "$(runner_pids)" ]; then
  if [ "$BROKER_CLEAN" -eq 1 ]; then
    PIDS="$(runner_pids | tr '\n' ' ')"
    echo "[$(date --iso-8601=seconds)] broker is clean but process is wedged; sending SIGKILL to $PIDS"
    kill -KILL $PIDS 2>/dev/null || true
  else
    FAILURE="$REPO/logs/AUTOMATION_FAILURE_${TODAY}.txt"
    echo "Runner remained active and broker was not clean. Process left alive to manage exits." > "$FAILURE"
    echo "[$(date --iso-8601=seconds)] CRITICAL: runner left alive because broker exposure may remain"
  fi
fi

# Re-check after any shutdown action. This is the authoritative EOD safety gate.
if "$REPO/.venv/bin/python" scripts/capture_session_fingerprint.py --check-broker; then
  BROKER_CLEAN=1
  rm -f "$REPO/logs/AUTOMATION_FAILURE_${TODAY}.txt"
  echo "[$(date --iso-8601=seconds)] broker EOD state clean: 0 positions, 0 open orders"
else
  BROKER_CLEAN=0
  printf 'EOD broker verification failed at %s. Manual intervention required.\n' \
    "$(date --iso-8601=seconds)" > "$REPO/logs/AUTOMATION_FAILURE_${TODAY}.txt"
  echo "[$(date --iso-8601=seconds)] CRITICAL: broker EOD verification failed"
fi

# Shadow analysis is observational and stored with the raw session artifacts.
if [ -f scripts/shadow_report.py ]; then
  "$REPO/.venv/bin/python" scripts/shadow_report.py --date "$TODAY" \
    > "$REPO/logs/shadow_report_${TODAY}.txt" 2>&1 || true
fi

# Preserve raw evidence even when the session or broker check failed.
git add -f logs/ evaluation/ 2>/dev/null || true
PUSH_OK=1
if git diff --cached --quiet; then
  echo "[$(date --iso-8601=seconds)] no new artifacts to commit"
else
  if git commit -m "Record Phase 3 session raw artifacts (${TODAY})"; then
    if git push origin "$(git rev-parse --abbrev-ref HEAD)"; then
      echo "[$(date --iso-8601=seconds)] artifacts committed and pushed"
    else
      PUSH_OK=0
      echo "[$(date --iso-8601=seconds)] ERROR: artifact push failed"
    fi
  else
    PUSH_OK=0
    echo "[$(date --iso-8601=seconds)] ERROR: artifact commit failed"
  fi
fi

if [ "$BROKER_CLEAN" -ne 1 ] || [ "$PUSH_OK" -ne 1 ]; then
  exit 1
fi

echo "[$(date --iso-8601=seconds)] automatic EOD close-out complete"
CLOSE_SCRIPT

# Preserve the familiar manual command as an alias for the automated close-out.
cat > "$HOME/eod_close.sh" <<'EOD_ALIAS'
#!/usr/bin/env bash
exec "$HOME/auto_close_session.sh" "$@"
EOD_ALIAS

chmod +x "$HOME/start_session.sh" "$HOME/auto_close_session.sh" "$HOME/eod_close.sh"
bash -n "$HOME/start_session.sh"
bash -n "$HOME/auto_close_session.sh"
bash -n "$HOME/eod_close.sh"

say "Installing managed cron block"
TMP_CRON="$(mktemp)"
trap 'rm -f "$TMP_CRON"' EXIT
crontab -l 2>/dev/null \
  | sed '/# BEGIN PHASE3 SESSION AUTOMATION/,/# END PHASE3 SESSION AUTOMATION/d' \
  > "$TMP_CRON" || true
cat >> "$TMP_CRON" <<EOF
# BEGIN PHASE3 SESSION AUTOMATION
${START_TIME_MINUTE} ${START_TIME_HOUR} * * 1-5 $HOME/start_session.sh
${CLOSE_TIME_MINUTE} ${CLOSE_TIME_HOUR} * * 1-5 $HOME/auto_close_session.sh
# END PHASE3 SESSION AUTOMATION
EOF
crontab "$TMP_CRON"

say "Installed"
printf 'Start: weekdays at %02d:%02d ET, with Alpaca market-calendar holiday gate\n' \
  "$START_TIME_HOUR" "$START_TIME_MINUTE"
printf 'Close: weekdays at %02d:%02d ET, with graceful watchdog and broker 0/0 gate\n' \
  "$CLOSE_TIME_HOUR" "$CLOSE_TIME_MINUTE"
echo
echo "Current managed cron entries:"
crontab -l | sed -n '/# BEGIN PHASE3 SESSION AUTOMATION/,/# END PHASE3 SESSION AUTOMATION/p'
echo
echo "Manual tests that do not start a trading session:"
echo "  bash -n ~/start_session.sh ~/auto_close_session.sh"
echo "  crontab -l"
echo "  timedatectl | grep 'Time zone'"
echo
echo "Monitor tomorrow after 09:30 ET:"
echo "  https://trader.tail0e7f84.ts.net/api/session/pulse"
