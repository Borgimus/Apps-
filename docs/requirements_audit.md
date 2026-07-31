# Requirements Audit — TC2000 + Alpaca Swing-Trading Automation

This document separates **transcript facts** (stated in the source video), **provisional
interpretations** (our defensible defaults for underspecified rules), and **unresolved
choices** (decisions deferred to configuration/experiment). The source video is treated as
**unverified marketing**. Nothing here asserts the strategy is profitable.

Status legend: ✅ implemented in this phase · 🟡 scaffolded/config-only · ⬜ later phase.

---

## 1. Transcript facts (stated rules)

| # | Fact | Where enforced | Status |
|---|------|----------------|--------|
| F1 | US-listed stocks, **long only**, v1. | strategy/model | ✅ (config `mode`, long-only sizing) |
| F2 | Exclude stocks priced **at or below $1** (strict `>`). | `universe.min_price` | ✅ |
| F3 | Favor smaller/faster growth stocks; require adequate liquidity. | strength + liquidity filters | ✅ |
| F4 | Hold multiple days while exit rules allow. | state machine | ✅ (states) |
| F5 | Daily = main setup; hourly = closer inspection. | indicators both TF | ✅ (daily governs) |
| F6 | Indicators: SMA 10/20/50/200, Volume, 22-EMA of volume. | `indicators.*` | ✅ |
| F7 | Three strength scans: ~20/60/120 daily bars. | `strength_scans` | ✅ |
| F8 | Each scan = top **2%** of universe over its lookback. | `top_percentile` | ✅ |
| F9 | Filters: price > $1, ≥5% daily movement, avg $ vol ≥ $30M (floor ≥ $5M). | universe filters | ✅ |
| F10 | MAs rising at ~45°; price above SMA50; SMA200 flat/curling up. | trend qualification | ✅ (as normalized slope) |
| F11 | Wait for orderly pullback to SMA10/20; higher lows, lower highs, tightening range, declining volume, quiet pre-breakout candle. | contraction detection | ✅ |
| F12 | Enter on breakout above the contraction top during regular hours. | breakout/entry | ✅ (deterministic) |
| F13 | Account risk **never exceeds 1% per trade**. | `risk.risk_fraction` | ✅ |
| F14 | Initial stop = **low of day** at breakout. | `initial_stop_reference` | ✅ (documented interpretation) |
| F15 | At entry+**5R**, sell **20–30%**, then move stop to breakeven. | partial_exit | ✅ |
| F16 | Final exit when a completed **daily candle closes below SMA10**. | final_exit | ✅ (confirm-close policy) |
| F17 | Add to a position after a second contraction (no complete rules given). | pyramiding | 🟡 disabled v1, shadow-logged |

---

## 2. Provisional interpretations (defensible defaults, configurable)

| # | Ambiguity | Our provisional default | Rationale |
|---|-----------|-------------------------|-----------|
| P1 | "moves faster than 5%/day" = ADR%? ATR%? 1-day change? | **20-bar ADR%** = `mean((H−L)/prev_close·100, 20)`. | Captures orderly volatility; a 1-day >5% gain would exclude quiet pullbacks. ATR% kept as a shadow experiment. |
| P2 | Must a stock appear in all three scans? | **`intersection_3_of_3`** (strict). | Conservative default; `agreement_2_of_3` and `union_ranked` computed but shadow-only. |
| P3 | "45-degree" MA angle. | **Normalized slope %** over 5-bar lookback, `slope_pct = (ma_t − ma_{t−k})/ma_{t−k}·100 > 0`. | Screen angle is scale-dependent; slope is scale-free and reproducible. |
| P4 | Pullback proximity to MA. | close/low within **3.0%** OR **1.0·ATR20** of SMA10/20. | Either metric qualifies; both configurable. |
| P5 | Consolidation length. | **3–15 daily bars**. | Matches "3 to 15 bars" reading. |
| P6 | Pivot definition. | fractal pivot with **2 bars** on each side; require ≥2 higher lows and ≥2 lower highs when bars allow. | Standard swing-pivot definition. |
| P7 | "Declining volume" during pullback. | pre-breakout volume **< 22-day volume EMA** AND flat-to-declining slope across consolidation. | Two independent, logged components. |
| P8 | Narrow pre-breakout candle. | true range **< 0.6·ATR20** (optional, scored). | Provisional; scored not required. |
| P9 | Breakout level. | **highest validated contraction range high** (v1). | Descending-trendline recognition deferred until explicitly tested. |
| P10 | "Elevated breakout volume." | **time-of-day-normalized** relative volume ≥ 1.5. | Raw morning full-day volume comparison is explicitly invalid. |
| P11 | 5R partial fraction. | **25%** (mid of 20–30%). | Provisional midpoint. |
| P12 | Final-exit execution. | confirm completed daily close < SMA10, then order **next regular-session open**. | Official close unknown intraday; near-close signal is shadow-only. |
| P13 | Stop-distance bounds. | min **0.5%**, max **15.0%**. | Reject too-tight and too-wide stops. |
| P14 | Slippage/entry order. | **stop-limit** with 0.5% slippage ceiling. | Never a naked market order in fast small-caps. |

---

## 3. Unresolved choices (deferred to config/experiment)

| # | Open question | Handling |
|---|---------------|----------|
| U1 | Exact slope lookback/threshold that best matches the "45°" intent. | Parameter grid in backtest; stable-region selection, not max-return. |
| U2 | ADR% vs ATR% for the 5% filter. | Shadow comparison experiment; report both. |
| U3 | Candidate mode that best balances signal count vs quality. | Shadow-evaluate 3-of-3 / 2-of-3 / union. |
| U4 | Dividend handling in indicators/P&L. | Documented in `docs/market_data.md`; Alpaca paper does not simulate dividends → reports flag it. |
| U5 | IEX vs SIP feed for the paper account. | Recorded per snapshot; TC2000 vs Alpaca never compared as identical feeds. |
| U6 | Time-of-day volume normalization curve source. | Built from historical intraday cumulative-volume profile; until available, breakout volume check fails closed. |

---

## 4. Hard safety invariants (non-negotiable, tested)

1. Broker adapter **rejects any non-paper Alpaca endpoint** and terminates safely. No casual live flag.
2. AI output **never** places/resizes/cancels orders or changes risk/config.
3. A filled position **must** have an acknowledged protective stop or an actively managed exit state; otherwise `RISK_BLOCKED`.
4. Entry rejected unless `entry_price > stop_price`, stop distance within bounds, size ≥ 1 whole share, and all freshness/liquidity/reconciliation/risk checks pass.
5. 5R partial executes **exactly once** (persisted one-time transition; restart-safe).
6. Fail-closed on stale data, unreconciled broker state, stale/invalid TC2000 batch, DB failure, insufficient buying power, closed/unsupported session, clock drift, or duplicate order intent.
7. Idempotent client order IDs prevent duplicate positions on retry.
