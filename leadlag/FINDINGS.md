# ES → SPY Lead-Lag Study — Findings

**Date:** 2026-07-04 · **Sample:** 627 sessions, 2024-01-02 → 2026-07-02,
09:30–10:00 ET · **Data:** ES trades (Databento GLBX.MDP3, continuous
front month, CME matching-engine timestamps) × SPY trades (Alpaca SIP),
aligned on a 50ms grid · **Reproduce:** `python -m leadlag run`
(full outputs: `leadlag_results/results.json`, charts in `results/`)

## Verdict: **ES LEADS — NOT EXPLOITABLE**

Answering the directive's four success criteria:

| # | Question | Answer |
|---|---|---|
| 1 | Does ES truly lead SPY? | **Yes, unambiguously.** |
| 2 | Is the lead statistically significant? | **Yes, overwhelmingly** (consistent across 627/627 sessions on transfer entropy). |
| 3 | Is the lead economically meaningful? | **No.** It is fully consumed within ±100ms. |
| 4 | Does any strategy survive realistic execution? | **No.** Every walk-forward configuration is net-negative; PF 0.58–0.63 vs the 1.2 gate. |

## 1. ES leads — the statistical case (Phase 1)

- **Cross-correlation** is asymmetric in ES's favor and dies fast:
  corr(r_ES[t−k], r_SPY[t]) = **0.035 at k=+50ms** vs 0.019 at k=−50ms;
  0.011 vs 0.008 at ±100ms; ≈0 beyond ±250ms. Contemporaneous (k=0)
  dominates everything at 0.123 — most adjustment happens inside the same
  50ms bin.
- **Information shares** (627 sessions): Gonzalo-Granger component share
  CS(ES) = **0.75**; Hasbrouck bounds **[0.59, 0.84]**. Error-correction
  speeds: α_SPY = +0.36 vs α_ES = −0.09 — SPY does ~4× the adjusting
  toward ES; ES mostly ignores the basis.
- **Granger** (VAR at 100ms): both directions "significant" (within-bin
  feedback), but summed cross-coefficients are 2.36 (ES→SPY) vs 0.65.
- **Transfer entropy**: net TE(ES→SPY) positive in **100% of sessions**.

## 2. Opening-session structure (Phase 2)

- The lead is **stable across the first 30 minutes** (lead mass 0.020–0.035),
  slightly strongest minutes 15–20.
- **The lead *shrinks* in high volatility** (0.014 in the top vol tercile vs
  0.023–0.025 low/mid) — the directive's hypothesis that "the lead expands
  during rapid repricing" is **contradicted**: arbitrage capacity scales up
  with volatility faster than information flow does.

## 3. The killer evidence — impulse response (Phase 3)

For ES impulses of 3/5/10bp within 500ms (quiet pre-period, 1,934/485/60
events):

| ES impulse | SPY already moved at impulse end | final response | ratio | half-response delay | P(continuation) |
|---|---|---|---|---|---|
| 3 bp | +3.10 bp | +3.85 bp | 1.02 | **0 ms** | 49% |
| 5 bp | +5.16 bp | +5.89 bp | 0.94 | **0 ms** | 48% |
| 10 bp | +9.50 bp | +10.89 bp | 0.96 | **0 ms** | 48% |

By the time an ES impulse is *observable as complete*, SPY has already made
~100% of its response, and what follows is a coin flip. There is nothing
left to collect at any latency ≥ 50ms. The arbitrage happens *during* the
impulse, not after it.

## 4. Order flow (Phase 4)

ES aggressor-side imbalance (1s window) predicts SPY's next 500ms with rank
IC = **0.034, t = 38** — one of the most statistically significant and
economically worthless numbers in this study: an IC of 0.03 on a ~1bp
500ms move captures ~0.03bp per signal against ~1.6bp round-trip costs.
The IC decays to 0.010 at 5s and goes *negative* (mean-reversion) for 5s
imbalance at 5s horizon. The information is real; the meter is running too
fast to bill anyone for it.

## 5–6. Strategies, walk-forward, ML (Phases 5–6)

- 162-combination threshold-momentum sweep: the best cell with ≥100 trades
  shows +1.6bps at **t = 0.6** — statistically indistinguishable from zero;
  every t-stat in the sweep's positive tail is < 1. (The λ=500ms cell
  outscoring λ=0 cells is itself the tell: noise, not signal.)
- Walk-forward (6-month train, 1-month test, 2024→2026):
  λ=100ms → −1.04 bps/trade (PF 0.58); λ=250ms → −0.87 (PF 0.58);
  λ=500ms → −0.94 (PF 0.63). **All fail the PF ≥ 1.2 gate.**
- ML (GBM, 12 microstructure features, chronological OOS): IC = **0.0016**;
  trading its top/bottom deciles nets −0.71 bps/trade at 14% win rate.
  Feature importances (ES 5s velocity, 1s flow) confirm the model found the
  same information the linear tests found — already priced.

## Why the hypothesis fails

Index arbitrage between ES and SPY is the most heavily competed relative-
value trade in existence. The literature (Budish, Cramton & Shim 2015)
documented the race window compressing from ~150ms in 2005 to ~5–10ms by
2015; this study confirms that in 2024–2026 the exploitable residual at
≥50ms resolution rounds to zero. A retail-accessible stack (Databento live
~1–5ms feed + decision + Alpaca REST order gateway) delivers ~50–300ms
decision-to-fill — one to two orders of magnitude slower than the window.
Against that, SPY costs ~1.6bp round trip while the post-impulse residual
is ≤ 0.75bp *gross* (Phase 3's final-minus-end gap) with 48% continuation.

**Production readiness assessment: not production-viable.** No live scanner
or execution framework was armed for this signal — building execution infra
for a strategy that fails its own validation gates would be theater. The
`ureversal/` live architecture (websocket scanner → risk layer → venue) is
the reference design should any slower-clock variant validate.

## Most promising adjacent hypotheses (in order)

1. **Same information, slower clock.** The Phase 4 flow IC (t=38) and ES
   overnight/pre-open drift are real information. At 1–5 *minute* horizons
   the competition is risk-capacity-bound rather than latency-bound, and
   1.6bp costs amortize over larger moves. Concrete test: ES overnight
   return + opening 1s-flow imbalance as features for SPY behavior in the
   first 30 minutes (gap-fade/follow classification). The framework here
   supports this by changing `grid.base_dt_ms` and the strategy clocks.
2. **Lead into less-arbed targets.** SPY is the most defended instrument on
   earth; IWM, sector ETFs, or high-beta single names are not. The same
   pipeline (swap the Alpaca symbol) would measure whether ES leads them by
   *seconds* rather than milliseconds.
3. **Basis-reversion at wider deviations.** α_SPY = 0.36 error-correction
   is strong; a study of extreme ES–SPY basis deviations (> 3σ) as SPY
   mean-reversion signals at seconds-scale would use the cointegration the
   right way around (fade SPY's overshoot, not chase ES's lead).
4. **Do not pursue** vol-conditioned latency trading: Phase 2 shows the
   lead *shrinks* when volatility rises.
