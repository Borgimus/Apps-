# SPY/DIA Divergence U-Reversal Strategy

Self-contained research + trading system for the opening-window (09:33–09:53 ET)
SPY long strategy triggered by DIA leading SPY out of a mutual decline.
Full mathematical definitions: [STRATEGY_SPEC.md](STRATEGY_SPEC.md).

> ⛔ **VALIDATION RESULT: FAIL (2026-07-04).** One year of SIP tick data shows
> SPY leads DIA (not the reverse) and the trigger's conditional return is
> significantly *negative* — see [FINDINGS.md](FINDINGS.md) for the evidence
> and proposed modifications. Do not trade this live in its long form. The
> system remains fully functional for re-testing revised hypotheses.

## Quick start

```bash
pip install -r requirements.txt            # repo root requirements include this package's deps
export ALPACA_API_KEY=... ALPACA_SECRET_KEY=...   # paper keys are fine for data + paper trading

python -m ureversal selftest               # no keys needed: synthetic end-to-end check
python -m ureversal fetch    --start 2025-01-02 --end 2025-06-30    # cache 1s data
python -m ureversal research --start 2025-01-02 --end 2025-06-30    # THE validation study
python -m ureversal backtest --start 2025-01-02 --end 2025-06-30 --plot eq.png
python -m ureversal optimize --start 2025-01-02 --end 2025-06-30 --budget 60
python -m ureversal replay   --date 2025-06-02 --plot day.png       # live-path replay
python -m ureversal scan                   # real-time signals, no orders
python -m ureversal trade                  # paper by default
python -m ureversal dashboard              # http://127.0.0.1:8001
```

Data feed: set `data.feed` in `ureversal.yaml` (`sip` needs a paid Alpaca data
plan; `iex` works on free keys but is sparse — reports are labeled with the
feed used).

## Architecture

| Module | Role |
|---|---|
| `ureversal.yaml` | every parameter + optimization grids (single source of truth) |
| `data.py` | Alpaca trades → 1-second bars, parquet cache |
| `signals.py` | §2–3 signal math + state machine — **one implementation** consumed by backtest (vectorized scan) and live (incremental engine); parity is tested |
| `backtest.py` | conservative fill/cost model, §4 exit families (`ExitEvaluator` shared with live) |
| `optimize.py` | walk-forward optimization, parameter-stability reporting |
| `research.py` | §8 validation study: frequency, lead-lag, null models, regimes → PASS/FAIL |
| `replay.py` | historical session through the live code path + parity check |
| `risk.py` | §7 hard limits: 1 position, 3 trades/day, daily loss, loss-streak breaker, PDT, kill switch |
| `execution.py` | marketable-limit orders, 2s cancel rule; options/futures venue hooks (unarmed) |
| `live.py` | websocket → 1s bars → same engine; `scan` and `trade` modes |
| `dashboard.py` | FastAPI UI over the trader's journal + kill switch |
| `viz.py` | session/event charts, equity curves |

## Safety model

Paper is the default everywhere. Real orders require **both**
`live.mode: live` in `ureversal.yaml` **and** `LIVE_TRADING_ENABLED=true` in
the environment. The kill switch (`./KILL_SWITCH` file, or POST
`/kill-switch/activate` on the dashboard) halts entries and flattens any open
position within one second. All positions are force-flattened at 09:54:30 ET
regardless of exit logic.
