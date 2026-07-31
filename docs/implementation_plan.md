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

## Phase 2 — Market data & broker integration (fakes → sandbox) ✅ (fakes)
- `data/market_data.py` (bars/quotes, feed metadata IEX/SIP, staleness, min-bars, gaps, spread). ✅
- `data/corporate_actions.py` (splits, symbol change, delisting, halt, stale). ✅
- `data/calendar.py` exchange calendar (weekends, floating/observed holidays, early closes, DST via
  zoneinfo, next-regular-open) + clock-drift check. ✅
- `broker/retry.py` fault classification (timeout/429/5xx/disconnect), capped backoff, Retry-After
  honoring, fail-closed exhaustion; wired into the paper adapter's `submit_order` reusing the same
  idempotent client_order_id. ✅
- `execution/reconciliation.py` broker-wins reconciliation: position/qty mismatch, unknown/missing
  position, unknown open order, and **missing-stop** detection → blocks new risk. ✅
- **Gate:** reconciliation & retry tests green (37 new tests, 126 total). Live trade-update
  streaming and the opt-in Alpaca paper sandbox lifecycle test remain for on-account verification.

## Phase 3 — Persistence, dashboard, notifications ✅
- Canonical DDL (`src/storage/schema.sql`) applied via stdlib `sqlite3` (dev/tests) + typed
  `Repository`; SQLAlchemy ORM models + Alembic migration (`migrations/`) as the Postgres
  deployment artifacts (validated in the `test-orm` CI job). ✅
- Idempotency enforced at storage: unique `client_order_id` and unique transition
  `idempotency_key` (duplicate transition insert is a safe no-op). ✅
- Audit-reconstruction test: indicators recomputed from the stored snapshot equal the persisted
  values byte-for-byte. ✅
- Authenticated dashboard: `build_dashboard_state` returns all required sections (health, paper
  verification, session, batch freshness, candidates, setups, positions/stops/R, equity/exposure/
  committed-risk/P&L/drawdown, reconciliation, reports) + stdlib `http.server` with constant-time
  bearer auth and public health/readiness endpoints. ✅
- Provider-neutral notifications: full event catalog, secret redaction, in-memory/console/webhook
  providers, fan-out dispatcher that isolates a failing provider. ✅
- **Gate:** audit reconstruction green; dashboard reconciliation blocks new risk on missing stop;
  no secrets in payloads/logs (redaction + secret-scan tests). 149 passed / 1 skipped lean
  (151 with SQLAlchemy present).

## Phase 4 — Research/backtest & AI review ✅
- Event-driven, point-in-time engine (`src/backtest/engine.py`): closed-bar-only setups,
  next-bar breakout on open/high, initial stop from the arming bar's low (lookahead-free proxy
  for "session low up to trigger"), pessimistic within-bar stop-before-target ordering, 5R-once
  partial + breakeven, confirm-close-then-next-open exit, dataset fingerprint for reproducibility. ✅
- Fill/cost model (`fills.py`): entry slippage, unfilled stop-limit (high-below-level / gap-above-
  limit), overnight gap-through stops, partial fills, commissions + SEC/TAF regulatory fees. ✅
- Metrics (`metrics.py`): signals/trades, win rate, expectancy R & $, profit factor, median/avg
  winner & loser, max drawdown, holding time, gap losses, turnover, costs. ✅
- Walk-forward (`walk_forward.py`): non-overlapping dev/validation/final-OOS split, rolling
  train/test windows, and grid **sensitivity that reports the distribution without picking a
  max-return winner**. ✅
- Breakdowns (`reports.py`): by stop-width bucket and by arbitrary decision-record tag
  (regime/liquidity/agreement/component). ✅
- AI review (`src/ai_review/`): advisory-only reviewer (explain/summarize/cluster/anomaly-issue/
  eod-report) with model/prompt/token/cost metadata; boundary guard rejects any reviewer exposing
  order/risk/config methods; `authoritative_decision` copies the deterministic outcome verbatim and
  attaches the review as audit only; shadow rules never auto-promote. ✅
- **Gate:** engine reproducible from dataset fingerprints; AI boundary tests green. 180 passed /
  1 skipped lean (181 with SQLAlchemy present).

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
