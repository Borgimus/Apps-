#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# One-shot VPS bootstrap for the Phase 3 paper-trading sessions.
#
# Run on a FRESH Debian 12 VPS as a user with sudo (or as root):
#   bash vps_bootstrap.sh
#
# What it does:
#   1. Installs git, Python 3.11, tmux, Tailscale
#   2. Sets the server timezone to America/New_York (cron runs in ET)
#   3. Joins your tailnet (prints an auth URL to open on your phone)
#   4. Clones Borgimus/Apps- and checks out the session branch
#   5. Creates the venv and installs pinned dependencies
#   6. Writes .env from your Alpaca paper credentials (prompted, not echoed)
#   7. Publishes the dashboard tailnet-only via `tailscale serve`
#   8. Installs ~/start_session.sh, ~/stop_entries.sh, ~/eod_close.sh
#   9. Optionally installs a weekday 09:31 ET cron auto-launch
#  10. Runs the broker + fingerprint verification
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BRANCH="claude/options-trading-research-system-TIU0p"
REPO_DIR="$HOME/trader"
TS_HOSTNAME="trader"

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

SUDO="sudo"; [ "$(id -u)" = "0" ] && SUDO=""

# ── 0. Sanity ────────────────────────────────────────────────────────────────
grep -q 'VERSION_CODENAME=bookworm' /etc/os-release 2>/dev/null \
  || echo "WARNING: not Debian 12 (bookworm). Python 3.11 must be available as python3.11."

# ── 1. Packages ──────────────────────────────────────────────────────────────
say "Installing packages"
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq git python3.11 python3.11-venv tmux curl ca-certificates cron

say "Setting timezone to America/New_York (cron schedules run in ET)"
$SUDO timedatectl set-timezone America/New_York
timedatectl show -p NTPSynchronized | grep -q yes || echo "WARNING: NTP not synchronized — check timedatectl"

# ── 2. Tailscale ─────────────────────────────────────────────────────────────
if ! command -v tailscale >/dev/null; then
  say "Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | $SUDO sh
fi
say "Joining tailnet — open the printed URL on your phone to authorize"
$SUDO tailscale up --ssh --hostname "$TS_HOSTNAME"

# ── 3. Repo ──────────────────────────────────────────────────────────────────
if [ ! -d "$REPO_DIR/.git" ]; then
  say "Cloning Borgimus/Apps-"
  read -rsp "GitHub personal access token (repo contents read/write): " GH_TOKEN; echo
  git clone "https://${GH_TOKEN}@github.com/Borgimus/Apps-.git" "$REPO_DIR"
  unset GH_TOKEN
  echo "Note: the token is stored in $REPO_DIR/.git/config for future pushes."
fi
cd "$REPO_DIR"
git checkout "$BRANCH"
if ! git config user.name >/dev/null 2>&1; then
  read -rp "git user.name for session commits: " GN
  git config user.name "$GN"
fi
if ! git config user.email >/dev/null 2>&1; then
  read -rp "git user.email: " GE
  git config user.email "$GE"
fi

# ── 4. Python env ────────────────────────────────────────────────────────────
say "Creating venv and installing pinned dependencies (a few minutes)"
python3.11 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.lock

# ── 5. Credentials ───────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  say "Alpaca PAPER credentials (input hidden; written to .env, chmod 600)"
  read -rsp "ALPACA_API_KEY: " AK; echo
  read -rsp "ALPACA_SECRET_KEY: " AS; echo
  umask 177
  cat > .env <<EOF
LIVE_TRADING_ENABLED=false
BROKER=alpaca
ALPACA_API_KEY=${AK}
ALPACA_SECRET_KEY=${AS}
ALPACA_BASE_URL=https://paper-api.alpaca.markets
DATABASE_URL=sqlite+aiosqlite:///./trading.db
LOG_LEVEL=INFO
EOF
  umask 022
  unset AK AS
fi

# ── 6. Tailnet-only dashboard ────────────────────────────────────────────────
# Dashboard binds 127.0.0.1:8000; tailscale serve proxies it to the tailnet
# over HTTPS. NEVER bind it publicly — the kill-switch endpoint has no auth.
say "Publishing dashboard to the tailnet"
$SUDO tailscale serve --bg 8000 || echo "WARNING: 'tailscale serve' failed — you can still reach the dashboard via SSH port-forward"

# ── 7. Helper scripts ────────────────────────────────────────────────────────
say "Installing helper scripts in \$HOME (kept OUT of the repo: an untracked file inside it fails the fingerprint clean-tree check)"

cat > "$HOME/start_session.sh" <<'EOF'
#!/usr/bin/env bash
# Launch a paper-trading session (idempotent; used by cron and manually).
set -euo pipefail
cd "$HOME/trader"
exec 9>"$HOME/.session.lock"
flock -n 9 || { echo "session already running"; exit 0; }
mkdir -p logs
# Dashboard (monitoring API) if not already up
if ! curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  nohup ./.venv/bin/python main.py dashboard >> logs/dashboard.log 2>&1 &
  sleep 3
fi
LOG="logs/session_$(date +%F).log"
echo "Launching session runner → $LOG"
exec ./.venv/bin/python -u scripts/session_runner.py --poll 30 --reconcile-interval 10 >> "$LOG" 2>&1
EOF

cat > "$HOME/stop_entries.sh" <<'EOF'
#!/usr/bin/env bash
# Kill switch: halts NEW entries; exits and EOD liquidation keep running.
# Do NOT kill the session process while positions are open.
touch "$HOME/trader/KILL_SWITCH"
curl -sf -X POST http://127.0.0.1:8000/kill-switch/activate >/dev/null 2>&1 || true
echo "Kill switch active. Remove with: rm ~/trader/KILL_SWITCH"
EOF

cat > "$HOME/eod_close.sh" <<'EOF'
#!/usr/bin/env bash
# Post-session close-out: broker reconciliation, then commit + push artifacts.
set -euo pipefail
cd "$HOME/trader"
./.venv/bin/python scripts/eod_check.py
git add -f logs/ evaluation/
if git diff --cached --quiet; then
  echo "Nothing new to commit."
else
  git commit -m "Record Phase 3 session raw artifacts ($(date +%F))"
  git push origin "$(git rev-parse --abbrev-ref HEAD)"
fi
EOF

chmod +x "$HOME/start_session.sh" "$HOME/stop_entries.sh" "$HOME/eod_close.sh"

# ── 8. Optional cron auto-launch ─────────────────────────────────────────────
read -rp "Install weekday 09:31 ET auto-launch cron job? [y/N] " CRON_YN
if [[ "${CRON_YN,,}" == y* ]]; then
  ( crontab -l 2>/dev/null | grep -v start_session.sh; \
    echo "31 9 * * 1-5 $HOME/start_session.sh" ) | crontab -
  echo "Installed. NOTE: cron does not know market holidays — remove or ignore those days."
fi

# ── 9. Verification ──────────────────────────────────────────────────────────
say "Verifying broker connectivity"
./.venv/bin/python scripts/test_alpaca.py || fail "Broker check failed — recheck .env keys"
say "Verifying Phase 3 fingerprint (frozen hashes + clean tree + broker 0/0)"
./.venv/bin/python scripts/capture_session_fingerprint.py --verify --check-broker \
  || fail "Fingerprint verification failed — do not run a session until resolved"

say "DONE"
cat <<EOF

Next steps from your phone:
  Monitor:   https://${TS_HOSTNAME}.<your-tailnet>.ts.net/api/session/pulse
  SSH:       Termius → host '${TS_HOSTNAME}' (Tailscale SSH, no keys needed)
  Launch:    ~/start_session.sh   (or let cron do it at 09:31 ET weekdays)
  Watch log: tail -f ~/trader/logs/session_\$(date +%F).log
  Stop new entries:  ~/stop_entries.sh
  After 12:30 ET:    ~/eod_close.sh
EOF
