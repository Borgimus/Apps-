# Architecture & State Machine

PAPER-ONLY swing-trading automation. GitHub is source-control/CI only — **not** the intraday
runtime. The bot runs on a persistent Linux host; an optional Windows companion only ships
TC2000 export files and can never place orders.

## Component map (`src/`)

```
api/            Authenticated dashboard + health/readiness endpoints (later phase)
broker/
  interface.py      Abstract broker (submit/cancel/replace, positions, account, streams)
  alpaca_paper.py   Paper-ONLY Alpaca adapter; rejects any non-paper endpoint (fail-closed)
data/
  market_data.py    Bars/quotes, feed metadata (IEX/SIP), staleness checks
  corporate_actions.py  Splits/symbol-change/delist/halt detection
tc2000/
  importer.py       Atomic 3-file batch import, validation, hashing, candidate sets
  validators.py     File/date/symbol/duplicate/empty validation
scanner/
  strength.py       Top-2% lookback ranking (no future data), agreement modes
  trend.py          Normalized-slope trend qualification
  contraction.py    Pivots, contraction, proximity, volume, narrow candle
  breakout.py       Breakout level, crossing, gap/chase limits
strategy/
  model.py          Candidate / Setup / Signal domain objects
  state_machine.py  Guarded, idempotent, timestamped, reasoned transitions
indicators/         Pure-Python SMA/EMA/ADR%/ATR%/$-volume/slope (exact formulas)
risk/
  sizing.py         1%-risk sizing, caps, whole-share rounding
  portfolio.py      Committed-risk accounting, exposure/position caps
execution/
  orders.py         Idempotent client order IDs, marketable/stop-limit builders
  reconciliation.py Broker REST snapshot + trade-update stream reconciliation
reporting/          EOD/weekly reports (later phase)
ai_review/          Explanations/summaries only; never authoritative (later phase)
storage/            Relational persistence (Postgres prod, SQLite dev/test)
notifications/      Provider-neutral notification interface
config/             Versioned YAML loader + validation
```

## Data-flow (one trading decision)

```
TC2000 EasyScans (operator) ──uploads .txt/.csv──▶ tc2000/importer (atomic batch, hashed)
        │                                                   │
        │                          candidate sets (3of3 / 2of3 / union, feed=TC2000)
        ▼                                                   ▼
Independent market data (Alpaca/other) ──▶ scanner.strength ▶ scanner.trend ▶ scanner.contraction
        │  (every scan/decision records source, timestamp, feed, values)          │
        ▼                                                                          ▼
                                                              scanner.breakout (level, volume)
                                                                                   │
                                                          risk.sizing + risk.portfolio (1%, caps)
                                                                                   │
                                                     execution.orders (idempotent, stop-limit)
                                                                                   │
                                              broker.alpaca_paper (PAPER endpoint enforced)
                                                                                   │
                                        execution.reconciliation (REST + trade-update stream)
```

Every arrow persists an auditable record. **Broker state wins** for actual positions/orders;
any discrepancy stays visible and blocks new risk until resolved.

## Strategy state machine

States (per candidate/trade):

```
IMPORTED ─▶ QUALIFIED ─▶ SETUP_WATCH ─▶ ENTRY_PENDING ─▶ OPEN_INITIAL_RISK
                                              │                    │
                                              │              (touch entry+5R)
                                              │                    ▼
                                              │             PARTIAL_PENDING ─▶ OPEN_BREAKEVEN
                                              │                    │                  │
                                              │                    └──────────────────┤
                                              │                                       ▼
                                              │                            (daily close < SMA10)
                                              │                                       ▼
                                              │                             FINAL_EXIT_PENDING ─▶ CLOSED
                                              ▼
                                        INVALIDATED  (setup broke before/while pending)

Cross-cutting blocking states (enter from anywhere, fail-closed):
   RECON_BLOCKED   broker/db reconciliation mismatch
   RISK_BLOCKED    position without a valid protective stop, or risk-limit breach
```

### Transition guards (representative)

| From → To | Guard (all must pass) | Idempotency key |
|-----------|-----------------------|-----------------|
| IMPORTED → QUALIFIED | fresh batch; strength agreement mode satisfied; trend qualifies; price>$1; liquidity OK | `candidate_id@batch_hash` |
| QUALIFIED → SETUP_WATCH | contraction detected; required components pass | `candidate_id@setup_version` |
| SETUP_WATCH → ENTRY_PENDING | breakout level valid; regular session; import fresh; data fresh; no gap beyond max | `signal_id` |
| ENTRY_PENDING → OPEN_INITIAL_RISK | fill confirmed; `entry>stop`; stop bounds OK; size≥1; protective stop acknowledged | `client_order_id` |
| ENTRY_PENDING → INVALIDATED | TTL expired / chase exceeded / setup invalidated / market closed | `signal_id` |
| OPEN_INITIAL_RISK → PARTIAL_PENDING | price touched `entry + 5R`; partial not yet done | `trade_id:partial` (once) |
| PARTIAL_PENDING → OPEN_BREAKEVEN | partial fill confirmed; stop moved to VWAP entry & acknowledged | `trade_id:breakeven` |
| OPEN_* → FINAL_EXIT_PENDING | completed daily candle closed < SMA10 | `trade_id:final@session_date` |
| FINAL_EXIT_PENDING → CLOSED | remaining qty exit filled; position reconciled to 0 | `trade_id:closed` |
| any → RISK_BLOCKED | position lacks valid stop OR risk cap breached | reason-stamped |
| any → RECON_BLOCKED | REST vs stream vs DB mismatch | reason-stamped |

Every transition record stores: `from`, `to`, `guard_results`, `idempotency_key`, `timestamp`,
`reason`. Replaying stored inputs + config reproduces every transition.

## Fail-closed controls (reject new entries)

Non-paper endpoint · stale/unavailable data · unreconciled broker state · stale/incomplete/invalid
TC2000 batch · DB persistence failure · position missing its stop · daily/portfolio risk reached ·
insufficient buying power · closed/unsupported session · clock drift > tolerance · duplicate order
intent. Plus an operator **emergency stop** that blocks new entries while continuing to manage and
close existing positions (never abandons positions or stops).

## Modes / promotion gates

`BACKTEST → SHADOW → PAPER_CONFIRM → PAPER_AUTO`. There is **no live phase**. PAPER_AUTO is gated by
the acceptance criteria in `docs/implementation_plan.md`. Shadow rules never auto-promote to execution.
