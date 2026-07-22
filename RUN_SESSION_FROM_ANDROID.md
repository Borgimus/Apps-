# Running a Phase 3 Session from an Android Phone (no Claude)

Two ways to do it. **Option A is strongly recommended**: the session must run
uninterrupted 09:30–12:30 ET to be valid (killed early with 0 positions = VOIDED;
killed with open positions = broker reconciliation required). Android aggressively
kills background processes and drops networks, so the safest design is: a small
always-on Linux host runs the session, and your phone starts/monitors/kills it
over Tailscale. Option B runs everything on the phone itself and is workable but
fragile.

Everything below uses branch `claude/options-trading-research-system-TIU0p`.

---

## Option A — Always-on Linux host + Tailscale, phone as the remote control

### What you need

- **Alpaca paper keys** for account `alpaca-paper-4UBN` (API key + secret from the
  Alpaca dashboard, Paper section).
- **A GitHub personal access token** (fine-grained, repo `Borgimus/Apps-`,
  Contents: read/write) — to clone and to push session artifacts.
- **A small always-on Linux box**: any ~$5/mo VPS (Hetzner CX22, DigitalOcean,
  Lightsail) or a spare PC that stays on. Pick a **Debian 12** image — it ships
  Python 3.11 natively, matching `requirements.lock` (pinned on 3.11.15).
- **Tailscale** (free tier) on both the server and the phone.
- An SSH client on the phone: **Termius** (Play Store) or JuiceSSH.

### Step 1 — Tailscale

On the server:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname trader
```

`--ssh` enables Tailscale SSH, so the phone can connect with no key files to manage.

On the phone: install the Tailscale app, log into the same account, toggle the VPN
on. In the admin console (https://login.tailscale.com) enable **MagicDNS** so the
server is reachable as just `trader`.

From Termius, connect to host `trader` (user = your server user). If Tailscale SSH
is active, it authenticates via your tailnet identity.

### Step 2 — One-time server setup

```bash
sudo apt update && sudo apt install -y git python3.11 python3.11-venv tmux
git clone https://<GITHUB_TOKEN>@github.com/Borgimus/Apps-.git trader && cd trader
git checkout claude/options-trading-research-system-TIU0p

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
```

Create `.env` (credentials live here, NOT exported in the shell — the code loads
them via pydantic settings):

```bash
cat > .env <<'EOF'
LIVE_TRADING_ENABLED=false
BROKER=alpaca
ALPACA_API_KEY=<your paper key>
ALPACA_SECRET_KEY=<your paper secret>
ALPACA_BASE_URL=https://paper-api.alpaca.markets
DATABASE_URL=sqlite+aiosqlite:///./trading.db
LOG_LEVEL=INFO
EOF
chmod 600 .env
```

Also set git identity once: `git config user.name/user.email`, and
`timedatectl` should show NTP synchronized (the runner computes ET itself; the
clock just has to be right).

### Step 3 — Verify (evening before, or ≥15 min pre-open)

```bash
cd ~/trader && source .venv/bin/activate

# Broker connectivity + confirms paper endpoint
python scripts/test_alpaca.py

# Frozen Phase 3 baseline: hashes, clean tree, broker 0 positions / 0 orders
python scripts/capture_session_fingerprint.py --verify --check-broker

git status          # must be clean
```

All three must pass. Pre-market, scanner candidates rejecting `low_volume_chop`
is normal. If `test_alpaca.py` shows 403s, the `.env` keys are wrong or missing.

### Step 4 — Launch on session day (~09:31 ET; post-open start avoids the standby issue seen in S6)

From the phone, SSH in, then:

```bash
cd ~/trader && source .venv/bin/activate
tmux new -s s7
python -u scripts/session_runner.py --poll 30 --reconcile-interval 10 \
  2>&1 | tee -a logs/session_$(date +%F).log
```

In a second tmux window (`Ctrl-b c`) start the monitoring API:

```bash
cd ~/trader && source .venv/bin/activate
python main.py dashboard
```

Detach with `Ctrl-b d` and close the SSH app — tmux keeps both running. Within
the first minute, confirm the log shows `[cycle 1]` and no traceback:
`tail -5 logs/session_$(date +%F).log`.

**Optional full automation** (no phone needed at open) — add a cron entry.
Cron uses server-local time, so compute the offset (09:31 ET = 13:31 UTC during
DST):

```
31 13 * * 1-5 cd /home/YOU/trader && .venv/bin/python -u scripts/session_runner.py --poll 30 --reconcile-interval 10 >> logs/session_$(date +\%F).log 2>&1
```

Note: cron doesn't skip market holidays — void/skip those days manually.

### Step 5 — Monitor from the phone

- **No SSH needed**: open `http://trader:8000/api/session/pulse` in the phone
  browser (works because the phone is on the tailnet). It serves the compact
  live snapshot the runner writes every cycle (`logs/live_status.json`). Event
  stream: `logs/push_events.jsonl` (entries, exits, heartbeats every ~6 cycles).
- **Full view**: SSH in, `tmux attach -t s7`.
- **Emergency stop of new entries**: `touch ~/trader/KILL_SWITCH` via SSH, or
  `POST http://trader:8000/kill-switch/activate`. Exits and EOD liquidation keep
  running (P3 behavior) — do NOT kill the process itself while positions are open.

### Step 6 — Close-out (after auto-termination at 12:30 ET)

```bash
cd ~/trader && source .venv/bin/activate
python scripts/eod_check.py        # broker must show 0 positions, 0 open orders
git add -f logs/ evaluation/       # .gitignore blocks these; -f is required
git commit -m "Record Phase 3 Session 7 raw artifacts (2026-XX-XX)"
git push origin claude/options-trading-research-system-TIU0p
```

The runner auto-generates `evaluation/reports/<date>.json` and the session log.
The analytical bookkeeping — the session entry in `evaluation/phase3_tracking.json`,
the post-session report markdown, ledger updates — can be done afterwards from the
committed raw artifacts (by hand following the S6 entry as a template, or by a
later Claude session).

---

## Option B — Entirely on the phone (Termux) — fallback, at your own risk

Real risks: Android's **phantom process killer** silently kills Termux child
processes (Android 12+), battery optimization kills the app, and a Wi-Fi→cellular
handoff mid-session can wedge connections. Any of these voids the session or
strands positions. Mitigations below reduce but do not eliminate this.

1. Install **Termux from F-Droid** (the Play Store build is abandoned).
2. In Termux:
   ```bash
   pkg update && pkg install proot-distro termux-api
   proot-distro install debian        # Debian 12 → native Python 3.11
   proot-distro login debian
   ```
   Inside Debian, follow Option A Steps 2–4 verbatim (clone, venv,
   `pip install -r requirements.lock` — aarch64 wheels exist for the pinned
   pandas/numpy/scipy; tmux optional since Termux itself is the terminal).
3. Keep it alive:
   - In a separate Termux session: `termux-wake-lock`.
   - Android Settings → Apps → Termux → Battery → **Unrestricted**.
   - Disable the phantom process killer (needs `adb shell` from any PC, once):
     `adb shell settings put global settings_enable_monitor_phantom_procs false`
   - Phone on charger, screen can be off, stay on one network for 09:30–12:30 ET.
4. Monitoring is local: `http://127.0.0.1:8000/api/session/pulse` in the phone
   browser, or just watch the Termux session.
5. Close-out identical to Option A Step 6.

---

## Protocol guardrails (unchanged, non-negotiable)

- `LIVE_TRADING_ENABLED=false` — paper only. The base URL stays
  `paper-api.alpaca.markets`.
- No edits to strategy thresholds, risk limits, `config.yaml`,
  `config/ticker_universe.yaml`, `requirements.lock`, or broker adapters — these
  are fingerprint-frozen; any change drops the session from the Phase 3 cohort.
- Working tree must be clean at launch (that's why `.env` is gitignored).
- Broker-reported fills are authoritative for any discrepancy.
- Full window 09:30–12:30 ET required for validity.
