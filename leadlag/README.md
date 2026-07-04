# ES/MES → SPY Lead-Lag Study

Falsification-first research into whether CME E-mini S&P 500 futures lead SPY
exploitably, with emphasis on the 09:30–10:00 ET opening session.

**Data**: ES trades from Databento (`GLBX.MDP3`, continuous front month
`ES.v.0`, CME matching-engine timestamps) + SPY trades from Alpaca SIP.
Master grid 50ms. **Clock caveat**: cross-feed skew is ~ms; conclusions below
50ms are out of scope by design.

## Pipeline

| Phase | Module | Methods |
|---|---|---|
| 1 Price discovery | `stats.py` | cross-correlation (±50ms…5s), VAR/Granger, Hasbrouck information-share bounds, Gonzalo-Granger component share, transfer entropy |
| 2 Opening conditioning | `phases.py` | lead mass by minute-after-open / vol / volume / spread terciles, heatmaps |
| 3 Event study | `phases.py` | ±5/10/20bp ES impulses (quiet pre-period), SPY impulse-response, delay, continuation probability |
| 4 Order flow | `phases.py` | ES aggressor-side imbalance → SPY forward-return rank IC |
| 5 Strategies | `strategy.py` | threshold momentum sweep over (lookback, threshold, hold) × **latency 0–1000ms**, ML scoring (linear + GBM, chronological OOS) |
| 6 Reality | `strategy.py` | full cost model, walk-forward with PF ≥ 1.2 gate, parameter-stability check |

The latency sweep is the honesty device: an edge that exists at λ=0 but dies
by λ=100ms is unreachable for a retail stack (feed hop + decision + order
gateway ≈ 50–300ms).

## Usage

```bash
python -m leadlag selftest        # no keys: every estimator must recover a planted 200ms lead
export DATABENTO_API_KEY=... ALPACA_API_KEY=... ALPACA_SECRET_KEY=...
python -m leadlag estimate-cost   # Databento cost estimate BEFORE downloading
python -m leadlag fetch           # 2021→present opening windows (resumable, cached)
python -m leadlag verify-mes      # confirm MES ≡ ES at 50ms (short sample)
python -m leadlag run             # all phases → leadlag_results/REPORT.md + charts
```

The selftest also proves the negative works: with the planted lag at one grid
step, no spurious lead appears; with execution latency beyond the lag, the
strategy edge disappears.
