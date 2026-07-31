# TC2000 + Alpaca Swing-Trading Automation (PAPER ONLY)

> ⚠️ **PAPER TRADING ONLY. NO LIVE TRADING.** The Alpaca adapter rejects any non-paper
> endpoint at startup and terminates safely. There is no runtime flag to switch to live.
> The source strategy came from a social-media video and its performance claims are treated
> as **unverified marketing** — this project reproduces and *tests* the stated rules without
> assuming the strategy is profitable.

This is a distinct system from the legacy options-research app under `app/`. It lives under
`src/` with its own tests under `tests_swing/` and its own CI (`.github/workflows/swing-ci.yml`).

## What it does

- Uses **TC2000 EasyScans** (20/60/120-bar strength) as the official candidate source, handed
  off via a documented, atomic file import — **no fabricated TC2000 API**.
- **Independently re-validates** every candidate with market data before any order.
- Executes/manages **long-only US stock** swing trades in an **Alpaca paper account** only.
- Deterministic scanning, entries, 1%-risk sizing, protective stops, 5R partial + breakeven,
  daily-close exit, reconciliation, journaling — with full auditability.
- **AI is advisory only**: explanations, summaries, anomaly review. It can never place/resize/
  cancel orders or change risk/config.

## Safety invariants (tested)

1. Non-paper Alpaca endpoint → rejected, fail-closed. (`src/broker/alpaca_paper.py`)
2. `risk_fraction ≤ 0.01`, dollar-volume floor ≥ $5M, `allow_live=false` — enforced at config load.
3. Entry requires `entry > stop`, bounded stop distance, ≥ 1 whole share, fresh data/import.
4. 5R partial fires **exactly once** (idempotent state transition; restart-safe).
5. Idempotent client order IDs prevent duplicate positions on retry.
6. Fail-closed on stale data, unreconciled broker state, stale/invalid TC2000 batch, risk caps.

## Repository layout (new system)

```
config/strategy.yaml     Versioned, deterministic strategy config (every threshold is a hypothesis)
src/indicators/          Pure-Python SMA/EMA/ADR%/ATR%/$vol/slope (exact formulas)
src/scanner/             strength · trend · contraction · breakout
src/risk/                sizing · portfolio
src/strategy/            five_r · final_exit · state_machine
src/tc2000/              importer (atomic batch, hashing, candidate sets)
src/broker/              interface · alpaca_paper (paper-endpoint enforcement)
src/execution/           orders (idempotent client order IDs)
src/{api,data,reporting,ai_review,storage,notifications}/   scaffolded for later phases
tests_swing/             89 deterministic tests (stdlib + pyyaml only)
docs/                    requirements_audit · architecture · tc2000_setup · db_schema · test_plan
                         · implementation_plan · delegation_log
```

## Run the tests

```bash
pip install pytest pyyaml
pytest -c pytest_swing.ini          # 229 passed / 1 skipped (SQLAlchemy models test skips without the dep)
```

The swing suite is dependency-light and network-free by design (broker/data are faked). An opt-in
Alpaca paper sandbox suite (later phase) runs only with `RUN_ALPACA_SANDBOX=1` and never on forked PRs.

## Operating modes (no live phase)

`BACKTEST → SHADOW → PAPER_CONFIRM → PAPER_AUTO`. PAPER_AUTO is gated by explicit engineering
acceptance criteria (see `docs/implementation_plan.md`) — which are **not** evidence of profitability.

## Status

- **Phases 0–6 complete**, 229 tests green (230 with SQLAlchemy): deterministic core; market-data,
  calendar, corporate actions; broker fault handling + reconciliation; persistence + audit
  reconstruction; dashboard + notifications; event-driven backtester + advisory-only AI review;
  runtime (JSON logging, readiness gate, fail-closed entry gate, graceful shutdown) + Docker/
  compose/systemd, backup/restore, runbook, order-incapable Windows companion; and the **live
  paper-trading loop** (mode-gated tick cycle, Alpaca paper REST + data clients, entry/manage
  decisions) — see `docs/live_trading.md`.
- **Remaining increment:** a persisted trade-state store so the loop can *manage* live positions
  across restarts (needed for full PAPER_AUTO); management logic is complete and unit-tested.
- **Deferred to on-account steps** (cannot run offline): live Alpaca trade-update streaming, the
  opt-in paper sandbox lifecycle test, and the PAPER_AUTO acceptance gates (20 shadow sessions,
  10 manual paper trades, operator sign-off) in `docs/implementation_plan.md`.

## Documentation

| Doc | Contents |
|-----|----------|
| `docs/requirements_audit.md` | Transcript facts vs provisional interpretations vs unresolved choices |
| `docs/architecture.md` | Component map, data flow, **state machine**, fail-closed controls |
| `docs/tc2000_setup.md` | EasyScan PCFs, export procedure, filenames, import validation checklist |
| `docs/db_schema.md` | Auditable relational schema + reconstruction guarantee |
| `docs/test_plan.md` | Coverage matrix and how to run |
| `docs/implementation_plan.md` | Phased plan + PAPER_AUTO acceptance criteria |
| `docs/live_trading.md` | Live paper-trading loop: tick cycle, mode gating, wiring, how to run |
| `docs/delegation_log.md` | Multi-model delegation records and supervisor decisions |
