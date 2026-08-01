# Test Plan

Deterministic core is pure-Python (stdlib) so tests run without numpy/pandas/network and are
byte-reproducible. Broker and market-data are exercised through **fakes**. A separate opt-in sandbox
suite hits Alpaca paper and never runs on PRs from forks.

## Coverage matrix (spec §Testing requirements)

| Area | Test(s) | Phase 1 |
|------|---------|:------:|
| Indicator calculations (SMA/EMA/ADR%/ATR%/$vol/slope) | `test_indicators.py` | ✅ |
| Strength ranking without future data | `test_strength.py` | ✅ |
| ADR% and dollar-volume definitions | `test_indicators.py` | ✅ |
| Contraction & pivot detection | `test_contraction.py` | ✅ |
| Breakout crossing & chase limits | `test_breakout.py` | ✅ |
| Position sizing & whole-share rounding | `test_sizing.py` | ✅ |
| Stop distance & risk caps | `test_sizing.py` | ✅ |
| Partial-fill behavior | `test_five_r.py` | ✅ |
| 5R partial exactly once | `test_five_r.py` | ✅ |
| Stop replacement to breakeven | `test_five_r.py` | ✅ |
| Daily-close exit timing | `test_final_exit.py` | ✅ |
| Gap-through-stop behavior | `test_final_exit.py` | ✅ |
| Duplicate event delivery (idempotency) | `test_state_machine.py` | ✅ |
| Crash/restart during order states | `test_state_machine.py` | ✅ |
| Alpaca timeouts/429/5xx/disconnects | `test_retry.py`, `test_broker_paper.py` (fake) | ✅ |
| Reconciliation mismatches | `test_reconciliation.py` | ✅ |
| Stale & incomplete TC2000 imports | `test_importer.py` | ✅ |
| Market holidays & early closes | `test_calendar.py` | ✅ |
| Market-data staleness / missing bars | `test_market_data.py` | ✅ |
| Corporate actions (split/halt/delist/stale) | `test_corporate_actions.py` | ✅ |
| Clock-drift tolerance | `test_calendar.py` | ✅ |
| Persistence + idempotency constraints | `test_storage.py` | ✅ |
| Audit reconstruction from stored inputs | `test_audit_reconstruction.py` | ✅ |
| Dashboard state + bearer auth + missing-stop block | `test_dashboard.py`, `test_server.py` | ✅ |
| Notifications: catalog, redaction, fan-out | `test_notifications.py` | ✅ |
| SQLAlchemy deployment models (dep-guarded) | `test_models_sqlalchemy.py` | ✅ |
| Fill/cost model (slippage/gap/partial/fees) | `test_fills.py` | ✅ |
| Backtest no-lookahead / gap / 5R-once / repro | `test_backtest_engine.py` | ✅ |
| Backtest metrics + stop-width breakdown | `test_backtest_metrics.py` | ✅ |
| Walk-forward split + sensitivity | `test_walk_forward.py` | ✅ |
| AI advisory boundary + metadata | `test_ai_review.py` | ✅ |
| JSON logging + secret redaction | `test_runtime_logging.py` | ✅ |
| Startup-reconciliation readiness + shutdown | `test_runtime_lifecycle.py` | ✅ |
| Fail-closed entry gate + emergency stop | `test_runtime_controls.py` | ✅ |
| Windows companion (no order authority) | `test_companion.py` | ✅ |
| Live entry evaluation (accept + rejections) | `test_live_entry.py` | ✅ |
| Live position management (5R/breakeven/final/missing-stop) | `test_live_manage.py` | ✅ |
| Live orchestrator mode gating + fail-closed | `test_live_orchestrator.py` | ✅ |
| Alpaca REST + data clients (request building) | `test_alpaca_clients.py` | ✅ |
| Live service glue (market view/reconcile/loop/setups) | `test_live_service.py` | ✅ |
| Durable trade-state store (reconstruct/5R-once/restart) | `test_trade_state.py` | ✅ |
| Paper-endpoint enforcement | `test_broker_paper.py` | ✅ |
| Secret-leakage checks | `test_secret_leak.py` | ✅ |

## Test types

- **Unit** — every indicator/formula against hand-computed vectors.
- **Property** — sizing never risks > `risk_fraction·equity`; shares never negative; partial never
  sells 0 or exceeds open qty; breakout crossing monotonic in price.
- **State-machine** — guards reject illegal transitions; idempotency keys make duplicate events no-ops;
  restart replays to the same state.
- **Integration (fakes)** — broker fake returns timeouts/429/5xx; adapter fails closed.
- **Sandbox (opt-in)** — real Alpaca paper; gated by env, skipped on forked PRs.

## Running

The swing suite is **isolated** from the legacy options-system suite (`tests/`), which pulls
heavy deps via its own conftest. Run the swing suite with its dedicated config:

```
pytest -c pytest_swing.ini                 # deterministic core (stdlib + pyyaml only)
RUN_ALPACA_SANDBOX=1 pytest tests_swing/sandbox -q   # opt-in, real paper account (later phase)
```

Current status: **238 passed / 1 skipped** lean (Phases 1–6); **239 passed** with SQLAlchemy present.

## Acceptance gates per phase → see docs/implementation_plan.md.
