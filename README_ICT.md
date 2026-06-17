# ICT Liquidity Sweep & FVG Reversal Strategy

A full-stack algorithmic trading application implementing the ICT (Inner Circle Trader) methodology: session range calculation, liquidity sweep detection, Fair Value Gap (FVG) entry signals, programmable market structure analysis, and comprehensive backtesting.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11 · FastAPI · async SQLAlchemy · Alembic |
| Database | SQLite (dev) / PostgreSQL 15 (prod) |
| Frontend | React 18 · Vite · TypeScript · TailwindCSS · TanStack Query v5 · lightweight-charts v4 |
| Infra | Docker Compose · nginx |

---

## How to Run and Access the App

### 1. Start with Docker

From the repo root:

```bash
docker compose up --build
```

The first run downloads base images and installs dependencies — allow 2–3 minutes. Subsequent starts take a few seconds.

To run in the background:

```bash
docker compose up --build -d
```

To stop everything:

```bash
docker compose down
```

### 2. Access on the same machine

| What | URL |
|---|---|
| Frontend (trading dashboard) | http://localhost:3000 |
| Backend REST API | http://localhost:8000 |
| Interactive API docs (Swagger) | http://localhost:8000/docs |
| Alternative API docs (ReDoc) | http://localhost:8000/redoc |

### 3. Access from another device on the same Wi-Fi

Docker binds the ports to `0.0.0.0` by default, so any device on the same local network can reach the app using the **host machine's local IP address** instead of `localhost`.

**Find your host machine's local IP:**

```bash
# macOS / Linux
ipconfig getifaddr en0        # macOS (Wi-Fi)
ip route get 1 | awk '{print $7; exit}'  # Linux

# Windows (PowerShell)
ipconfig | findstr "IPv4"
```

The local IP is typically in the form `192.168.x.x` or `10.0.x.x`.

**Then on any phone, tablet, or other laptop on the same Wi-Fi, open:**

| What | URL |
|---|---|
| Frontend | `http://<host-ip>:3000` |
| API | `http://<host-ip>:8000` |
| API docs | `http://<host-ip>:8000/docs` |

For example, if your host machine's IP is `192.168.1.42`:
- Frontend → `http://192.168.1.42:3000`
- API docs → `http://192.168.1.42:8000/docs`

> If the remote device can't connect, check that your host machine's firewall allows inbound connections on ports 3000 and 8000.

---

## Quick Start (local dev without Docker)

### Local development

**Backend**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

**Frontend**
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

---

## Strategy Logic

All definitions are objective and fully parameterisable — no subjective chart reading required.

### Session windows

| Session | Default (UTC) | Config key |
|---|---|---|
| Asian | 00:00 – 06:00 | `asian_session` |
| London | 03:00 – 08:00 | `london_session` |
| New York | 14:00 – 21:00 | `trading_start_hour` / `stop_trading_hour` |

The `SessionCalculator` scans each bar's UTC timestamp and computes:
- `asian_high` / `asian_low` — highest high / lowest low within the Asian window
- `london_high` / `london_low` — same for London

### Liquidity sweep definition

A sweep is detected when:

1. **Extension** — a bar's `high` (for upside sweeps) or `low` (downside) exceeds the session level by at least `min_sweep_ticks × tick_size`.
2. **Rejection** — within `rejection_candles` subsequent bars, `close` returns inside the range by at least `rejection_close_pct` of the extension.

```
Upside sweep threshold  = level + extension × (1 − rejection_close_pct)
Downside sweep threshold = level − extension × (1 − rejection_close_pct)
```

A `SweepEvent` is emitted for every sweep regardless of rejection status. `rejection_confirmed=True` marks valid entry signals.

### Fair Value Gap (FVG) — ICT 3-candle definition

```
Bullish FVG:  candle[2].low  > candle[0].high   (gap to the upside)
Bearish FVG:  candle[2].high < candle[0].low    (gap to the downside)
```

FVGs below `min_fvg_size` (in price points) are discarded. `fvg_fill_pct_entry` controls how far into the gap price must penetrate before a signal fires.

### Market structure

| Event | Definition |
|---|---|
| `SWING_HIGH` | Strict local max over `swing_lookback` bars on both sides |
| `SWING_LOW` | Strict local min over `swing_lookback` bars on both sides |
| `HIGHER_HIGH` / `LOWER_HIGH` | Current swing high vs. previous swing high |
| `HIGHER_LOW` / `LOWER_LOW` | Current swing low vs. previous swing low |
| `BOS_BULLISH` | Close breaks above the most-recent confirmed swing high |
| `BOS_BEARISH` | Close breaks below the most-recent confirmed swing low |
| `CHOCH_BULLISH` | Close breaks above last swing high while in a downtrend |
| `CHOCH_BEARISH` | Close breaks below last swing low while in an uptrend |

### Liquidity targets

Targets are collected from four sources (strength 1–3):

| Source | Strength |
|---|---|
| Previous-day high / low | 3 |
| Session highs / lows | 2 |
| Equal highs / lows (within `equal_threshold_price`) | 2 |
| Swing points | 1 |

`nearest_target(levels, price, direction)` returns the closest qualifying level in the trade direction.

### Exit modes

| Mode | Behaviour |
|---|---|
| `fixed_rr` | Take profit at `fixed_rr_ratio × risk` (default 2R) |
| `liquidity_target` | TP at the nearest liquidity level beyond entry |
| `major_structure` | TP at the next BOS/CHoCH structural level |
| `hybrid` | First partial TP at 1R (50%), then liquidity target for remainder |

---

## Configuration Reference

All parameters live in `ICTConfig` (pydantic model, `app/strategies/ict/config.py`).

| Parameter | Default | Description |
|---|---|---|
| `tick_size` | `0.25` | Minimum price increment for the instrument |
| `min_sweep_ticks` | `2.0` | Minimum extension beyond level in ticks |
| `rejection_candles` | `3` | Bars allowed for close-back-inside |
| `rejection_close_pct` | `0.5` | Fraction of extension required for rejection |
| `min_fvg_size` | `0.0` | Minimum FVG gap in price points |
| `fvg_fill_pct_entry` | `0.0` | How deep into FVG before entry (0 = top of gap) |
| `fvg_lookback_bars` | `20` | Bars back to scan for valid FVGs |
| `swing_lookback` | `5` | Bars on each side for swing high/low detection |
| `sl_buffer_ticks` | `2.0` | Extra buffer beyond sweep extreme for stop-loss |
| `exit_mode` | `"fixed_rr"` | Exit strategy: `fixed_rr`, `liquidity_target`, `major_structure`, `hybrid` |
| `fixed_rr_ratio` | `2.0` | R-multiple for fixed-RR take profit |
| `account_size` | `100,000` | Starting account equity |
| `risk_per_trade_pct` | `0.01` | Fraction of account risked per trade |
| `max_daily_loss_pct` | `0.02` | Max daily drawdown before halting |
| `max_trades_per_day` | `3` | Trade limit per session day |
| `trading_start_hour` | `14` | NY session open (UTC) |
| `stop_trading_hour` | `21` | NY session close (UTC) |

---

## API Reference

Base URL: `http://localhost:8000/api/ict`

### Signals

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/signals` | List recent ICT signals |
| `GET` | `/signals/{id}` | Single signal detail |
| `POST` | `/signals/{id}/update-status` | Mark signal filled/cancelled |

### Backtest

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/backtest` | Queue a backtest; returns `{task_id}` |
| `GET` | `/backtest/{task_id}` | Poll status / retrieve results |

**Backtest request body**
```json
{
  "symbol": "NQ=F",
  "start_date": "2023-01-01",
  "end_date": "2023-12-31",
  "config": {
    "min_sweep_ticks": 2,
    "rejection_close_pct": 0.5,
    "exit_mode": "fixed_rr",
    "fixed_rr_ratio": 2.0,
    "risk_per_trade_pct": 0.01
  }
}
```

**Backtest result fields** (flat response)

```
total_trades, winning_trades, losing_trades
win_rate, long_win_rate, short_win_rate
avg_rr, profit_factor, expectancy
total_pnl, total_return, monthly_return
max_drawdown, sharpe_ratio, trade_duration_avg
monthly_pnl: { "YYYY-MM": dollars }
trades: [ { entry_time, exit_time, pnl, rr_achieved, ... } ]
```

### Scanner

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/scanner` | Scan all configured symbols for live signals |

### WebSocket

```
WS ws://localhost:8000/ws/ict/signals
```

Messages: `signal`, `signal_update`, `heartbeat`, `error`

---

## Database Migrations (Alembic)

```bash
# Apply all migrations
alembic upgrade head

# Create a new auto-generated migration
alembic revision --autogenerate -m "your description"

# Downgrade one step
alembic downgrade -1
```

The `alembic/env.py` transparently converts async driver URLs (`sqlite+aiosqlite://`, `postgresql+asyncpg://`) to their sync equivalents so Alembic's own runner works without changes.

**Environment variable**

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/trading alembic upgrade head
```

Defaults to `sqlite:///./trading.db` when unset.

---

## Backtesting

The backtester (`app/backtesting/ict_backtester.py`) runs the complete strategy over historical 1-minute bars.

```python
from app.backtesting.ict_backtester import ICTBacktester
import yfinance as yf, pandas as pd

bars = yf.download("NQ=F", start="2023-01-01", end="2023-12-31", interval="1m")
bt = ICTBacktester(starting_equity=100_000)
result = bt.run(bars, symbol="NQ=F")
print(result.to_dict())
```

**Reported metrics**

| Metric | Description |
|---|---|
| `win_rate` | Fraction of trades closed in profit |
| `long_win_rate` / `short_win_rate` | Win rates split by direction |
| `avg_rr` | Average risk-to-reward achieved |
| `profit_factor` | Gross profit ÷ gross loss |
| `expectancy` | Expected P&L per trade in dollars |
| `max_drawdown` | Peak-to-trough equity decline (fraction) |
| `sharpe_ratio` | Annualised Sharpe (risk-free = 0) |
| `trade_duration_avg` | Average trade duration in minutes |
| `monthly_pnl` | Dict of `{YYYY-MM: net_pnl}` |

**Trade replay** (for UI animation)

```python
bt = ICTBacktester(starting_equity=100_000)
for snapshot in bt.trade_replay(bars, symbol="NQ=F"):
    # snapshot emitted once per bar with current equity, open positions, etc.
    ...
```

---

## Scanner & Alerts

```python
from app.scanner.scanner import ICTScanner
from app.data import YFinanceDataSource

scanner = ICTScanner(
    symbols=["NQ=F", "ES=F", "GC=F"],
    data_fetcher=YFinanceDataSource(),
)
results = await scanner.scan_all()
```

Alert channels are configured via `AlertSettings`:

```python
from app.alerts.alert_manager import AlertManager, AlertSettings, DiscordConfig

manager = AlertManager(AlertSettings(
    in_app=True,
    discord=DiscordConfig(webhook_url="https://discord.com/api/webhooks/..."),
))
await manager.send_signal_alert(sweep_event, fvg, direction="long")
```

Supported channels: in-app (WebSocket), email (SMTP), Discord (webhook), Telegram (bot).

---

## Architecture

```
Apps-/
├── app/
│   ├── api/                  FastAPI routers, ORM models, schemas
│   ├── strategies/ict/       Core strategy modules
│   │   ├── config.py         ICTConfig pydantic model
│   │   ├── session_calculator.py
│   │   ├── liquidity_sweep.py
│   │   ├── fvg_detector.py
│   │   ├── market_structure.py
│   │   ├── liquidity_targets.py
│   │   ├── position_sizer.py
│   │   └── ict_strategy.py   Orchestrator (session → sweep → FVG → signal)
│   ├── backtesting/
│   │   └── ict_backtester.py
│   ├── scanner/
│   │   └── scanner.py        Multi-symbol async scanner
│   ├── alerts/
│   │   └── alert_manager.py  Multi-channel alert dispatcher
│   └── data/
│       └── data_fetcher.py   yfinance + async data layer
├── alembic/                  Database migrations
├── frontend/src/
│   ├── pages/BacktestPage.tsx
│   ├── components/backtest/  Metrics panel, equity curve, monthly returns, trade table
│   ├── api/ict.ts            API client
│   └── types/ict.ts          TypeScript types
├── tests/
│   └── test_ict_strategy.py  48 unit tests
└── docker-compose.yml
```

---

## Tests

```bash
# ICT strategy tests (48 tests)
python -m pytest tests/test_ict_strategy.py -v

# Full suite
python -m pytest tests/ -q
```

Key test coverage:
- `SessionCalculator` — range computation from 1-min bars
- `LiquiditySweepDetector` — controlled minimal DataFrames for deterministic sweep/rejection tests
- `FVGDetector` — bullish/bearish detection, fill percentage, size filter
- `MarketStructureEngine` — swing detection, HH/HL/LH/LL labelling, BOS/CHoCH
- `LiquidityTargetFinder` — session levels, nearest target lookup
- `PositionSizer` — risk-based sizing, edge cases
- `ICTStrategy` — end-to-end signal generation
- `ICTBacktester` — result shape, per-bar replay
