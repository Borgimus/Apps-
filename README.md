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

### 3. Run backtests

```bash
python main.py backtest --symbol SPY QQQ --start 2023-01-01 --end 2024-12-31
```

Reports are saved to `./backtest_results/`.

> ⚠️ Backtest results use **synthetic options pricing** (Black-Scholes) because yfinance does not provide historical options data. Results are clearly marked as `[APPROXIMATE]`.

### 4. Start paper trading

```bash
python main.py trade
```

Opens the dashboard at `http://127.0.0.1:8000`.

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
│   └── utils/           # Logging setup
├── tests/               # pytest unit tests
├── paper_trader.py      # Intraday trading loop orchestrator
├── main.py              # CLI entry point
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

---

## Other apps in this repository

### [multi-agent-workspace/](multi-agent-workspace/)

A model-agnostic multi-agent AI project workspace: create projects, assign AI agents (Anthropic, OpenAI-compatible, local, or mock models) with roles, prompts, tools and permissions, and watch them plan, delegate, implement, review each other's work and produce shared artifacts — with live activity streaming, a prompt inspector, human approval gates, and a complete audit history. See its [README](multi-agent-workspace/README.md) for setup.
