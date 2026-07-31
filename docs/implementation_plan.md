# Phased Implementation Plan & Acceptance Gates

Work proceeds one phase at a time. Each phase shows changed files, explains material decisions, runs
tests with exact results, lists remaining risks, and commits with a focused message. **No live phase
exists.** PAPER_AUTO is the terminal operating mode and is gated by explicit criteria.

## Phase 0 — Design (this batch) ✅
- Requirements audit; architecture + state machine; TC2000 handoff; DB schema; test plan; this plan.
- Versioned `config/strategy.yaml`.
- **Gate:** docs reviewed; provisional values enumerated and configurable.

## Phase 1 — Deterministic testable core (this batch) ✅
- Pure-Python indicators (SMA/EMA/ADR%/ATR%/$vol/slope).
- Scanners: strength ranking (no future data) + agreement modes; trend slopes; contraction/pivots;
  breakout level/crossing/gap/chase.
- Risk: 1% sizing, caps, whole-share rounding, stop-distance bounds.
- 5R partial (exactly once) + breakeven stop move.
- Final-exit timing (confirm daily close < SMA10) + gap-through-stop accounting.
- State machine: guarded, idempotent, timestamped, reasoned transitions.
- TC2000 importer: atomic batch, validation, hashing, candidate sets.
- Broker interface + paper-endpoint enforcement (rejects non-paper).
- Config loader with validation (risk_fraction ≤ 0.01, $vol floor ≥ $5M, allow_live=false).
- Tests for all of the above; secret-leakage guard.
- **Gate:** `pytest -c pytest_swing.ini` green; safety invariants covered by tests.

## Phase 2 — Market data & broker integration (fakes → sandbox)
- `data/market_data.py` (bars/quotes, feed metadata, staleness), corporate actions detection.
- Exchange calendar (holidays/early closes/DST), clock-drift check.
- Alpaca paper adapter wired to broker fake; timeouts/429/5xx/disconnect handling; reconciliation
  (REST + trade-update stream); idempotent order lifecycle.
- **Gate:** reconciliation & retry tests green; opt-in sandbox lifecycle test passes manually.

## Phase 3 — Persistence, dashboard, notifications
- SQLAlchemy models + Alembic migrations (Postgres/SQLite); audit reconstruction test.
- Authenticated dashboard (health, paper verification, session, batch freshness, candidates, setups,
  positions/stops/R, equity/exposure/risk/drawdown, reconciliation, reports).
- Provider-neutral notifications for all required events.
- **Gate:** reports reconcile to broker snapshots; no secrets in logs.

## Phase 4 — Research/backtest & AI review
- Event-driven, point-in-time backtester (slippage/spread/partials/unfilled stop-limits/gaps/
  corporate actions/fees). Walk-forward; regime/liquidity/agreement/stop-width/component reports;
  sensitivity to every provisional definition.
- AI review module (explanations/summaries/anomaly issue drafts) with model/prompt/cost metadata;
  cannot alter risk/config/orders.
- **Gate:** backtest reproducible from dataset fingerprints; AI boundary tests green.

## Phase 5 — Deployment & runbooks
- Docker dev env; persistent Linux deploy (compose/systemd); health/readiness; JSON logs+rotation;
  DB backup/restore; graceful shutdown preserving order management; startup reconciliation before ready.
- Runbook: broker outage, data outage, missing stop, duplicate order, stale scan, corrupted DB.
- Optional Windows companion (install/auth/retry/uninstall; cannot place orders).
- **Gate:** startup reconciliation blocks readiness on mismatch; graceful shutdown verified.

## PAPER_AUTO acceptance criteria (all required, engineering-only — not proof of edge)
1. TC2000 setup & import guide verified by the operator.
2. All automated tests pass.
3. No unresolved high-severity security/reconciliation defects.
4. ≥ 20 market sessions of shadow operation: no duplicate orders, no missing stops, no unexplained drift.
5. ≥ 10 manually confirmed Alpaca paper trades through the full lifecycle.
6. Restart recovery tested with open orders and open positions.
7. Every paper position has a verified protective stop or an actively managed exit state.
8. Daily reports reconcile to Alpaca positions/orders/fills/equity.
9. Operator explicitly approves the versioned strategy config.

A separate statistical sample requirement (not these gates) governs any judgment of profitability.
