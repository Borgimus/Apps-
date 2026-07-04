# Validation Findings — SPY/DIA U-Reversal Hypothesis

**Date:** 2026-07-04 · **Data:** Alpaca SIP consolidated tape, 253 sessions
(2025-07-01 → 2026-07-02), 1-second bars reconstructed from trades,
09:28–09:56 ET · **Machine-readable results:** `ureversal_results/*.json`
(regenerate with `python -m ureversal research`)

## Verdict: **FAIL** — the hypothesis as specified is contradicted by the data

The strategy must not be traded live in its long form. The evidence is not
"absence of edge" but an *inverted* premise and a *negative* conditional
return. Details and proposed modifications below.

---

## 1. DIA does not lead SPY — SPY leads DIA

Unconditional cross-correlation of 1-second returns over 143 sessions in the
opening window (`corr(r_DIA[t−k], r_SPY[t])`):

| lag | k=−1 (SPY leads) | k=0 | k=+1 (DIA leads) | Σ k∈[−8,−1] | Σ k∈[+1,+8] |
|---|---|---|---|---|---|
| corr | **+0.068** | +0.317 | +0.013 | **+0.084** | +0.013 |

The only meaningful off-zero mass is at *SPY leading DIA by one second* —
5× larger than everything on the DIA-leads side combined. This is standard
price discovery: SPY trades ~50× DIA's volume (measured on this data: ~88.6k
vs ~10.8k prints in the window); information hits SPY (and ES) first, DIA
echoes ~1s later. The premise "DIA independently reverses first" mistakes the
echo for the source.

Event-conditional (within detected episodes): lead score −0.011,
95% CI [−0.115, +0.097]; reversal-timing median 1.0s, Wilcoxon p = 0.17 —
no lead there either.

Cross-check with QQQ (60 sessions): ±1s cross-correlations are symmetric
(+0.015 each way). Among liquid cap-weighted ETFs, nothing tradable on Alpaca
observably leads SPY at 1-second resolution.

## 2. The conditional return on the trigger is negative, at every horizon and every parameter setting

Long SPY at the trigger, net of half-spread + slippage + fees, 28 events:

| horizon | mean net bps | 95% CI | win rate |
|---|---|---|---|
| 30s | −2.57 | [−4.65, −0.51] | 32% |
| 60s | −3.04 | [−5.77, −0.30] | 29% |
| 120s | −3.42 | [−7.20, +0.28] | 36% |
| 300s | −4.76 | [−10.24, +0.85] | 39% |

At 30s and 60s the CI *excludes zero on the negative side*. A 54-combination
sensitivity sweep over (ρ_min, θ_down, δ_min, z_min) on 120 sessions found no
configuration with positive mean forward return (range −1.1 to −6.7 bps).
Walk-forward optimization (expanding window, monthly folds, 40-candidate
random search) pooled OOS: 14 trades, win rate 21%, profit factor 0.35,
expectancy −1.1 bps, with chosen parameters drifting across folds.

Interpretation: "flat after a correlated decline, with the thin tracker
uptick" is predominantly a *continuation* state in the opening window, not a
bottom. The DIA uptick that fires the trigger is bid-ask bounce/noise on an
instrument that prints in only ~66% of seconds.

The one structurally interesting positive: running the same detector on
*shuffled* SPY/DIA pairings produces **zero** events — the pattern's
occurrence genuinely requires the true intermarket link. The geometry is
real; its predictive direction is wrong.

## 3. Pattern frequency

28 events / 253 sessions (0.11/day; 10% of days). All events fell on mid- and
high-volatility days (23/28 in the top vol tercile); the U-shape simply
doesn't form on quiet opens. Events cluster 09:33–09:50 with no strong
minute-of-window concentration.

## 4. The fade variant (short the trigger) — a hint, not an edge

Shorting SPY at the same trigger, identical cost treatment and null models:

| horizon | mean net bps | 95% CI | win rate |
|---|---|---|---|
| 30s | −0.03 | [−2.09, +2.05] | 50% |
| 60s | +0.44 | [−2.31, +3.17] | 54% |
| 300s | +2.18 | [−3.43, +7.67] | 50% |

Positive point estimates that beat the random-entry null's 95th percentile at
3 of 4 horizons — but every CI includes zero at n=28, and a sign flip chosen
*after* seeing the long results is data mining by construction. **Not
validated.** It earns exactly one thing: a pre-registered re-test on
independent data (see §5.3).

## 5. Proposed modifications (preserving the intermarket-lead concept)

1. **Move the leader upstream of price discovery: ES/MES futures → SPY.**
   The one place a genuine lead into SPY plausibly exists is index futures
   (they lead cash ETFs at sub-second horizons in the microstructure
   literature). Alpaca has no futures data or execution; this requires a
   futures-capable feed/broker. The `FuturesVenue` hook and the
   target/leader-generic signal engine mean only the data adapter is new
   work. **This is the highest-value next experiment.**
2. **Do not invert to "trade DIA off SPY".** The measured echo is corr 0.068
   at 1s: expected capture ≈ 0.068 × σ₁ₛ(DIA) ≈ 0.05 bp per event, versus
   ~1–2 bp round-trip cost on DIA's thinner book. Dead on arithmetic.
3. **Pre-registered fade re-test.** Fetch 2021–2025 (Alpaca history reaches
   2016), run `run_study(direction="short")` once, untouched parameters. If
   the 60–300s expectancy is positive with CI excluding zero on ~100+
   events, the fade graduates to paper trading; otherwise it dies too.
4. **Slower divergence clocks.** At 1s, Epps noise dominates ETF pairs. The
   intermarket concept survives at minute-scale between instruments with
   genuine composition differences (SPY vs RSP equal-weight, SPY vs sector
   ETFs on days with concentrated sector moves). The engine supports this
   via `corr_ret_s` and the window parameters without code changes.
5. **More history for frequency.** 0.11 events/day is too rare to power
   event-level statistics in one year. Any revised trigger should be tuned
   for ≥0.5/day and validated on ≥3 years.

## 6. Methodology notes that materially changed results

- **Epps effect**: median SPY/DIA 1-second-return correlation is 0.46 on real
  SIP data (vs ~0.95 at minute scale). Correlation gates now use overlapping
  3-second returns (`corr_ret_s: 3`). Any similar strategy tested with naive
  1s correlations will silently never fire.
- **Real slope scale**: opening-window 20s SPY slopes sit within
  ±0.18 bps/s (p5–p95). Thresholds were recalibrated accordingly.
- **Feed**: IEX (free real-time tier) carries ~147 DIA prints per opening
  window (~1 per 11s) — unusable for 1s signals on the thin leg. All results
  here are SIP. Historical SIP is free; real-time SIP (needed for any live
  variant) requires Alpaca Algo Trader Plus.
