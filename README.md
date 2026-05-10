# Options Trading Research System

A modular Python system for researching, backtesting, and paper-trading day-trading options strategies.

> ⚠️ **DISCLAIMER**: This software is for educational and research purposes only. Options trading involves significant risk of loss. Paper trading results do not guarantee live performance. Always verify strategies extensively before risking real capital.

---

## Features

| Module | Description |
|--------|-------------|
| `app/data/yfinance_data.py` | Historical OHLCV + research-grade options data (unofficial) |
| `app/brokers/` | Broker adapter pattern — Alpaca, Tradier, IBKR, local paper |
| `app/strategies/` | 4 plug-in strategies + IV crush and liquidity filters |
| `app/risk/` | Pre-trade risk manager with hard-coded guardrails |
| `app/backtesting/` | Vectorised backtester with P&L reports |
| `app/api/` | FastAPI dashboard + WebSocket signal stream |
| `paper_trader.py` | Full intraday trading loop (paper mode by default) |
| `main.py` | CLI entry point |

---

## Architecture

```
yfinance (research only)
        │
        ▼
  Strategy Signals ──► IV Crush Filter ──► Liquidity Filter
        │
        ▼
  Risk Manager (pre-trade checks)
        │
        ▼
  Broker Adapter (Alpaca / Tradier / IBKR / Paper)
        │
        ├──► Paper broker (local simulation)
        └──► Live broker (LIVE_TRADING_ENABLED=true required)
        │
        ▼
  SQLite/Postgres ◄──► FastAPI Dashboard
```

---

## Setup

### 1. Install dependencies

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your broker credentials
```

Key settings in `.env`:

```bash
# Safety: leave this as false until you have paper-tested your strategies
LIVE_TRADING_ENABLED=false

# Choose your broker: alpaca | tradier | ibkr | paper
BROKER=alpaca

# Alpaca paper credentials
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

---

## How to Run

### Unit tests

```bash
pytest tests/ -v
```

All 59 tests should pass.  Coverage includes:

- ORB strategy with 5-minute intraday bars (13 tests in `test_intraday_orb.py`)
- Risk sizing, session buffers, kill switch, daily loss guardrails
- Broker interface (PaperBroker), paper order placement end-to-end
- Strategy signal generation for ORB, RSI, VWAP, MA Compression

### Integration test (real Alpaca paper account)

Requires Alpaca API credentials in `.env`.

```bash
python scripts/integration_test.py
```

This fetches live SPY bars, runs all strategies, risk-checks a live option
contract selected from the Alpaca chain, places a paper limit order, then
immediately cancels it.

### Dashboard (standalone)

Starts the FastAPI dashboard without the trading loop.  Broker-dependent
endpoints (`/account`, `/positions`) return 503; all others work fully.

```bash
uvicorn app.api.dashboard_api:app --reload
# Open: http://127.0.0.1:8000/health
# Docs: http://127.0.0.1:8000/docs
```

To start the dashboard with a live broker connection (and the trading loop
running in the background), use `python paper_trader.py` instead.

### Manual paper-trading loop (dry run — no orders placed)

Fetches today's 5-minute SPY bars, runs ORB/VWAP/RSI strategies, selects
live Alpaca contracts, runs full risk checks, and logs every decision.
**No orders are submitted.**

```bash
python scripts/paper_loop.py --dry-run
```

Optional flags:

```bash
# Cancel unfilled orders older than 20 minutes, then dry-run:
python scripts/paper_loop.py --dry-run --cancel-stale 20

# Show internal library logs (useful for debugging):
python scripts/paper_loop.py --dry-run --log-level INFO

# Different symbol:
python scripts/paper_loop.py --dry-run --symbol QQQ
```

### Manual paper-trading loop (places Alpaca paper limit orders)

Same pipeline as dry-run, but actually submits limit orders to Alpaca's
paper account.  **Limit orders only — market orders are always rejected.**

```bash
python scripts/paper_loop.py
```

Orders are placed during the valid session window (09:45–15:45 ET by default)
and respect all risk guardrails (max 3 trades/day, 2% daily loss cap, etc.).

To cancel stale unfilled orders from previous runs:

```bash
python scripts/paper_loop.py --cancel-stale 15   # cancel orders > 15 min old
```

### Kill switch

To halt all order submissions immediately:

```bash
touch ./KILL_SWITCH          # activate
rm ./KILL_SWITCH             # deactivate

# Or via the dashboard API:
curl -X POST http://127.0.0.1:8000/kill-switch/activate
curl -X DELETE http://127.0.0.1:8000/kill-switch
```

### Backtests

```bash
python main.py backtest --symbol SPY QQQ --start 2023-01-01 --end 2024-12-31
```

Reports are saved to `./backtest_results/`.

> ⚠️ Backtest results use **synthetic options pricing** (Black-Scholes) because yfinance does not provide historical options data. Results are clearly marked as `[APPROXIMATE]`.

### 5. Dashboard API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness check |
| `GET /status` | Trading session status |
| `GET /account` | Broker account summary |
| `GET /positions` | Open positions |
| `GET /orders?status=filled` | Recent orders |
| `GET /signals` | Recent strategy signals |
| `GET /risk` | Risk counters and recent events |
| `POST /kill-switch/activate` | Halt all new orders immediately |
| `DELETE /kill-switch` | Re-enable trading |
| `POST /backtest/run` | Trigger a backtest via API |
| `WS /ws/signals` | Real-time signal stream |

---

## Broker Setup

### Alpaca (recommended for API-first development)

1. Create an account at [alpaca.markets](https://alpaca.markets)
2. Generate paper trading API keys
3. Set `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, and `ALPACA_BASE_URL` in `.env`
4. Options order flow requires the `alpaca-py` SDK: `pip install alpaca-py`

### Tradier (excellent for options data)

1. Sign up at [tradier.com](https://tradier.com) and request API access
2. Use the sandbox for testing: `TRADIER_BASE_URL=https://sandbox.tradier.com/v1`
3. Set `TRADIER_ACCESS_TOKEN` in `.env`

### Interactive Brokers

1. Install TWS or IB Gateway
2. Enable API access in TWS settings (port 7497 for paper, 7496 for live)
3. Install ib_insync: `pip install ib_insync`
4. Set `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID` in `.env`

### Local Paper Broker (no credentials needed)

Set `BROKER=paper` in `.env`. Uses yfinance for quote simulation. Useful for offline strategy development.

---

## Strategies

| ID | Name | Signals |
|----|------|---------|
| `orb` | Opening Range Breakout | LONG/SHORT on SPY/QQQ breakout |
| `vwap_reclaim` | VWAP Reclaim/Rejection | LONG on reclaim, SHORT on rejection |
| `rsi_trend` | RSI + Trend Filter | Oversold bounce / overbought fade |
| `ma_compression` | MA Compression Breakout | Breakout after EMA(8)/EMA(21) squeeze |

Plus two filters applied to all signals:
- **IV Crush Filter** — blocks trades near earnings events
- **Liquidity Filter** — selects the most liquid contract matching the signal

---

## Risk Controls

All of these are enforced **before every order** regardless of strategy:

| Control | Default | Config key |
|---------|---------|------------|
| Max risk per trade | 1% of equity | `RISK_MAX_RISK_PER_TRADE` |
| Max trades per day | 3 | `RISK_MAX_TRADES_PER_DAY` |
| Max daily loss | 2% | `RISK_MAX_DAILY_LOSS` |
| No trade first 15 min | Hard-coded | `no_trade_open_buffer_minutes` |
| No trade last 15 min | Hard-coded | `no_trade_close_buffer_minutes` |
| No market orders | Hard-coded | — |
| Min open interest | 100 | `RISK_MIN_OPEN_INTEREST` |
| Min volume | 50 | `RISK_MIN_VOLUME` |
| Max bid/ask spread | 10% | `RISK_MAX_SPREAD_PCT` |
| Earnings blackout | 1 day | `RISK_EARNINGS_BLACKOUT_DAYS` |

### Kill Switch

Create the file `./KILL_SWITCH` (or call `POST /kill-switch/activate`) to immediately halt all new order submissions. Delete the file or call `DELETE /kill-switch` to resume.

---

## Enabling Live Trading

> ⚠️ **WARNING**: Live trading places real money at risk.

Live trading requires an **explicit opt-in** — it cannot be enabled by editing `config.yaml` alone. You must set the environment variable:

```bash
export LIVE_TRADING_ENABLED=true
```

The system will print a 5-second warning countdown before starting. Ensure you have:
- Paper-tested your strategies thoroughly
- Set your broker to the live endpoint (not paper/sandbox)
- Understood the risk controls and verified they are configured correctly

---

## Running Tests

```bash
pytest tests/ -v --cov=app --cov-report=term-missing
```

Test coverage includes:
- Risk sizing (position sizing, max risk)
- Spread, OI, and volume filters
- Max daily loss and max trades per day
- Session buffer checks
- Strategy signal generation
- Broker interface (PaperBroker)
- Paper order placement end-to-end

---

## Unattended Session Runner

`scripts/session_runner.py` is the hardened, crash-safe replacement for `paper_trader.py`.
It is designed to run unattended and restart automatically after a crash.

```bash
# Single paper session (places real Alpaca paper orders)
python scripts/session_runner.py

# Dry-run (no orders placed, full pipeline exercised)
python scripts/session_runner.py --dry-run

# Multiple symbols, custom poll interval
python scripts/session_runner.py --symbol SPY QQQ --poll 60

# Cancel unfilled orders on shutdown, reconcile every 15 minutes
python scripts/session_runner.py --cancel-pending --reconcile-interval 15
```

On startup the runner:
1. Reloads any pending orders from the database (crash recovery)
2. Reconciles local positions against the broker
3. Enters the polling loop (fill tracking → position monitor → signal scan)

On shutdown (SIGTERM/SIGINT or market close):
1. Final fill poll so no fills are missed
2. Optional pending-order cancellation (`--cancel-pending`)
3. Position liquidation
4. Session health report written to `logs/session_YYYY-MM-DD.json`

---

## Docker Deployment

```bash
cp .env.example .env
# Edit .env with your broker credentials and alert settings

docker compose up -d
```

Two services start:
- **dashboard** — FastAPI on port 8000; restarts on crash; healthcheck on `/health`
- **session-runner** — Runs `session_runner.py`; restarts on crash; waits for dashboard to be healthy first

Logs and the SQLite database are mounted from the host so they persist across container restarts:

```
./logs/   → /app/logs   (trading.log, errors.log, session_*.json, etc.)
./data/   → /app/data
```

To stop cleanly:

```bash
docker compose down
```

To tail live logs:

```bash
docker compose logs -f session-runner
```

---

## Monitoring Dashboard

Once the dashboard service is running, open **http://localhost:8000** in your browser.

The dashboard auto-refreshes every 30 seconds and shows:
- Daily P&L, win rate, open positions count
- Risk status (drawdown, trades remaining, kill switch state)
- Open positions table with unrealised P&L
- Pending orders table with age
- Recent fills and rejections
- Scrollable session log
- Kill switch toggle buttons

---

## Logging

All events are written to multiple rotating files under `./logs/`:

| File | Contents |
|------|----------|
| `trading.log` | All events (plain text, 10 MB × 5) |
| `trading.jsonl` | All events as NDJSON — machine-readable (20 MB × 10) |
| `errors.log` | ERROR and above only (5 MB × 10) |
| `broker.log` | `app.brokers.*` events only (5 MB × 5) |
| `api.log` | `app.api.*` events only (5 MB × 5) |
| `session_YYYY-MM-DD.log` | Today's session in plain text (daily rotation, 30 days) |
| `session_YYYY-MM-DD.json` | End-of-day health report (JSON) |

Query with `jq`:

```bash
# All fills today
jq 'select(.msg | contains("FILL"))' logs/trading.jsonl

# All errors
jq 'select(.level == "ERROR")' logs/trading.jsonl
```

---

## Alerts

Configure Slack and/or email by setting environment variables in `.env`:

```bash
# Slack
ALERT_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Email (Gmail example)
ALERT_EMAIL_FROM=bot@gmail.com
ALERT_EMAIL_TO=you@gmail.com
ALERT_SMTP_HOST=smtp.gmail.com
ALERT_SMTP_PORT=587
ALERT_SMTP_USER=bot@gmail.com
ALERT_SMTP_PASSWORD=app_password_here

# Only send warning+ events (default: info = everything)
ALERT_MIN_LEVEL=warning
```

Alerts fire for these 15 events:

| Event | Default level |
|-------|--------------|
| Session started/stopped | info |
| Order submitted/filled/partial | info |
| Order cancelled/rejected/stale-cancelled | warning |
| Stop loss / Take profit | warning / info |
| Daily loss threshold reached | critical |
| Kill switch activated | critical |
| API/broker error | warning |
| EOD liquidation | info |
| Session summary | info |

If no channels are configured the alert service is a no-op — no errors are raised.

---

## Safe Stop

To stop the session runner without losing any in-flight data:

```bash
# Graceful (recommended): SIGTERM triggers final fill poll + health report
kill -TERM $(pgrep -f session_runner)

# Or create the kill switch file to halt new orders first, then stop:
touch ./KILL_SWITCH
# ... wait for current positions to be managed ...
kill -TERM $(pgrep -f session_runner)
```

With Docker:

```bash
docker compose stop session-runner   # sends SIGTERM, waits for graceful exit
```

---

## Paper Mode Enforcement

The session runner enforces paper trading at startup:

```python
if settings.live_trading_enabled:
    logger.critical("LIVE_TRADING_ENABLED=true — aborting.")
    sys.exit(1)
```

`LIVE_TRADING_ENABLED` must never be set to `true` when using `session_runner.py`.

---

## Project Structure

```
├── app/
│   ├── config/          # Settings loader (pydantic-settings)
│   ├── data/            # yfinance data source (research only)
│   ├── brokers/         # Broker adapters (Alpaca, Tradier, IBKR, Paper)
│   ├── strategies/      # Strategy plug-ins + filters
│   ├── risk/            # Pre-trade risk manager
│   ├── backtesting/     # Backtest engine + report generator
│   ├── api/             # FastAPI dashboard + SQLAlchemy models
│   ├── trading/         # FillTracker, PositionManager, TradeJournal,
│   │                    # PendingOrderStore, Reconciler, SessionRecovery,
│   │                    # HealthReporter
│   └── utils/           # Logging setup, alert service
├── scripts/
│   ├── session_runner.py  # Hardened unattended trading loop
│   └── integration_test.py
├── tests/               # pytest unit tests
├── paper_trader.py      # Legacy intraday loop
├── main.py              # CLI entry point
├── Dockerfile
├── docker-compose.yml
├── config.yaml          # Default configuration
├── .env.example         # Environment variable template
└── requirements.txt
```

---

## Data Disclaimer

This project uses **yfinance**, which scrapes unofficial Yahoo Finance endpoints. This data:

- May be delayed, incorrect, or unavailable without notice
- Is intended for **research and educational use only**
- **Must not** be used as the execution-grade data source for placing orders

For actual order flow, the system always uses broker-provided quotes, option chains, Greeks, and bid/ask spreads via the broker API adapters.

---

## License

MIT License. Use at your own risk.
