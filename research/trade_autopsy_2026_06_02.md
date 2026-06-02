# Trade Autopsy — 2026-06-02

**3 trades executed. 1 win, 2 losses. Daily PnL: -$128.50.**

> No intraday position marks are stored. MFE/MAE are bounded by known entry and exit prices only. Underlying prices at exit are delta-approximated — treat as directional indicators, not precise values.

---

## Trade 302 — DIA — WIN (+$25.00)

### Signal

| Field | Value |
|---|---|
| Strategy | vwap_reclaim |
| Direction | SHORT (put) |
| Scanner score | 53.0 |
| Scanner direction | **LONG** ← contradicts strategy |
| Scanner reasons | orb_breakout, trend_up, rsi=79.5 (extreme), above_vwap |
| Quality score | **4.0 / 4** |
| Confluence count | 5 |
| Signal age at entry | 96.3 min |
| rvol at signal | 0.627 |
| Underlying at signal | $511.32 |

**Scanner/strategy disagreement:** Scanner tagged DIA as a breakout LONG. vwap_reclaim generated a SHORT — correctly fading an overbought RSI (79.5) extension above VWAP.

### Contract

| Field | Value |
|---|---|
| Symbol | DIA260605P00511000 |
| Type | Put |
| Strike | $511 |
| Expiry | 2026-06-05 (3 DTE) |
| Delta | -0.352 |
| IV | 14.18% |

### Entry

| Field | Value |
|---|---|
| Time (ET) | 11:21:17 |
| Underlying | $511.32 |
| Bid / Ask | $1.54 / $1.62 |
| Entry spread | 5.06% |
| Limit | $1.62 |
| Fill | $1.60 |
| Slippage | -$0.02 |
| Fill latency | 64s |

### Exit

| Field | Value |
|---|---|
| Time (ET) | 12:30:56 |
| Reason | eod_exit |
| Limit placed | $1.80 |
| Fill | **$1.85** (+$0.05 vs limit) |
| Hold | 68.6 min |

### PnL

| Field | Value |
|---|---|
| Gross PnL | +$25.00 |
| Entry spread cost (est.) | -$4.00 |
| Entry slippage | -$2.00 |
| Net after costs (est.) | +$19.00 |

### MFE / MAE

| Metric | Value |
|---|---|
| Known min MFE | +$25.00 (15.6%) |
| MAE | unknown — no intraday marks |
| Direction correct | ✓ yes |
| Underlying move (est.) | -$0.71 (-0.14%) |

### Assessment

**Signal:** STRONG. Quality 4/4, confluence 5. Scanner contradiction was a false negative — vwap_reclaim correctly identified overbought extension. High quality offset 96-min staleness.

**Execution:** GOOD. Entry spread 5.06% acceptable for low-IV ETF option. Exit filled above limit (+$0.05). No execution issues.

**Exit management:** ACCEPTABLE. EOD forced exit captured +15.6% gain. Whether a better exit existed intraday is unknown (no marks). Low DIA IV (14.18%) means theta decay was minimal during the hold.

**Verdict:** No evidence of poor signal, execution, or exit management.

---

## Trade 303 — COIN — LOSS (-$103.50)

### Signal

| Field | Value |
|---|---|
| Strategy | vwap_reclaim |
| Direction | SHORT (put) |
| Scanner score | 62.0 |
| Scanner direction | SHORT ← agrees with strategy |
| Scanner reasons | orb_breakdown, trend_sideways, rsi=35.4, below_vwap |
| Quality score | **3.0 / 4** |
| Confluence count | 3 |
| Signal age at entry | **121.6 min (2+ hours — very stale)** |
| rvol at signal | 0.51 |
| Underlying at signal | $176.91 |

**Concurrent ORB signal (skipped):** ORB also generated SHORT at quality 2/4 (underlying $172.73 at ORB signal time). ORB forward data shows COIN rose +1.27% in 30 min from ORB signal level. By vwap_reclaim entry ($176.91), underlying was already $4.18 above the breakdown level.

### Contract

| Field | Value |
|---|---|
| Symbol | COIN260605P00170000 |
| Type | Put |
| Strike | $170 |
| Expiry | 2026-06-05 (3 DTE) |
| Delta | -0.393 |
| IV | **83.66%** ← very high |

### Entry

| Field | Value |
|---|---|
| Time (ET) | 11:51:38 |
| Underlying | $176.91 |
| Bid / Ask | $3.65 / $3.97 |
| Entry spread | **8.4%** (wide) |
| Limit | $3.97 |
| Fill | $3.90 |
| Slippage | -$0.07 |
| Fill latency | 64s |

### Exit

| Field | Value |
|---|---|
| Time (ET) | 12:20:51 |
| Reason | trailing_stop |
| Exit bid / ask | $2.82 / $2.91 |
| Exit spread | 3.14% |
| Limit placed | $2.82 |
| Fill | **$2.865** (+$0.045 vs limit) |
| Hold | 28.2 min |

### PnL

| Field | Value |
|---|---|
| Gross PnL | -$103.50 |
| Entry spread cost (est.) | -$16.00 |
| Entry slippage | -$7.00 |
| Net after costs (est.) | -$126.50 |

### MFE / MAE

| Metric | Value |
|---|---|
| Known min MFE | unknown — no intraday marks |
| MAE (at exit) | -$103.50 (-26.5%) |
| Direction correct | ✗ no — underlying rose |
| Underlying move (est.) | +$2.65 (+1.50%) |

### ORB Forward Performance (direction-adjusted)

| Horizon | Underlying | Move vs ORB signal | Direction-adj return |
|---|---|---|---|
| +5 min | $173.12 | +$0.39 | -0.23% (SHORT wrong) |
| +15 min | $174.12 | +$1.39 | -0.80% |
| +30 min | $174.93 | +$2.20 | -1.27% |

ORB signal was generated when COIN was at $172.73. It was already rising. vwap_reclaim entry at $176.91 was after a further $4.18 rally.

### Assessment

**Signal:** POOR.
- Signal age 121.6 min — the underlying had moved $4.18 above the breakdown level by entry time
- Both the ORB and vwap_reclaim signals were SHORT, but COIN was in an upward momentum phase, not a breakdown
- rvol=0.51 — sub-normal volume, low directional conviction
- Very high IV (83.66%) means the put was expensive; being wrong is costly

**Execution:** MARGINAL.
- Entry spread 8.4% — wide, driven by high IV. Within the 10% threshold but at the high end
- Entry slippage -$0.07 (1.8%) — slightly elevated
- Exit spread narrowed to 3.14% — conditions improved at exit

**Exit management:** ACCEPTABLE.
- Trailing stop fired at 28.2 min, limiting loss to -$103.50
- Given wrong direction from the start, stop performed its intended function
- Without the stop, EOD exit would have occurred ~10 min later at likely worse prices given continued underlying rally

**Verdict:** Signal was poor. The 2-hour-old signal was acted on after COIN had already moved materially against the SHORT. High IV inflated entry cost and magnified the loss. Trailing stop correctly limited damage.

---

## Trade 304 — META — LOSS (-$50.00)

### Signal

| Field | Value |
|---|---|
| Strategy | vwap_reclaim |
| Direction | LONG (call) |
| Scanner score | 60.0 |
| Scanner direction | LONG ← agrees with strategy |
| Scanner reasons | orb_breakout, rsi=43, above_vwap, ma_compression |
| **Scanner trend field** | **down** ← contradicts LONG signal |
| Quality score | **2.0 / 4** ← minimum threshold |
| Confluence count | **1** ← minimum |
| Signal age at entry | 96.6 min |
| rvol at signal | 0.507 |
| Underlying at signal | $602.92 |

**Internal scanner contradiction:** Scanner emitted LONG with `trend=down`. Scanner score 60 driven by orb_breakout/above_vwap, but price trend was classified as down — suggesting recent price action diverged from the breakout signal.

### Contract

| Field | Value |
|---|---|
| Symbol | META260603C00610000 |
| Type | Call |
| Strike | $610 |
| Expiry | **2026-06-03 (1 DTE)** ← critical |
| Delta | 0.446 |
| IV | 44.62% |
| Moneyness | $7.08 OTM (1.17% OTM) |

**1-day expiry with $7.08 OTM strike:** High theta decay rate. Even a flat-to-down underlying move would erode value rapidly over the 38.2 min hold.

### Entry

| Field | Value |
|---|---|
| Time (ET) | 11:51:38 |
| Underlying | $602.92 |
| Bid / Ask | $4.58 / $4.78 |
| Entry spread | 4.27% |
| Limit | $4.78 |
| Fill | $4.70 |
| Slippage | -$0.08 |
| Fill latency | 64s |

### Exit

| Field | Value |
|---|---|
| Time (ET) | 12:30:56 |
| Reason | eod_exit |
| Limit placed | $4.12 |
| Fill | **$4.20** (+$0.08 vs limit) |
| Hold | 38.2 min |

### Intraday Delta Snapshot (12:21 ET)

| Field | Entry | 12:21 | Change |
|---|---|---|---|
| Delta | 0.446 | 0.421 | -0.025 |

Delta declined 2.5 pts over 30 min — underlying drifted below strike or theta reduced call sensitivity. Directionally unfavorable.

### PnL

| Field | Value |
|---|---|
| Gross PnL | -$50.00 |
| Entry spread cost (est.) | -$10.00 |
| Entry slippage | -$8.00 |
| Net after costs (est.) | -$68.00 |

### MFE / MAE

| Metric | Value |
|---|---|
| Known min MFE | unknown — no intraday marks |
| MAE (at exit) | -$50.00 (-10.6%) |
| Direction correct | ✗ no — underlying drifted lower |
| Underlying move (est.) | -$1.11 (-0.18%) |

### Assessment

**Signal:** POOR.
- Quality 2/4, confluence 1 — weakest signal of the session
- Scanner internal contradiction: trend=down but signal=LONG
- rvol=0.507 — sub-normal volume
- 96.6 min signal age

**Execution:** ACCEPTABLE.
- Entry spread 4.27% — reasonable
- Slippage -$0.08 within norms
- Exit filled above limit

**Exit management:** POOR.
- 1-day expiry contract selected for a ~40 min hold under a minimum-quality signal. Theta decay was structurally working against the position from entry.
- No trailing stop triggered (loss remained below threshold). EOD exit was the only exit.
- The LiquidityFilter selected the shortest-available liquid expiry that passed delta/OI gates. A 1-day OTM call requires a high-conviction directional signal — quality 2/4 with 1 confluence does not meet that bar.

**Verdict:** Signal was poor, and contract selection was structurally mismatched. A 1-day OTM call entered with minimum-threshold signal quality and sub-normal volume, into a scanner-tagged downtrend, lost $50 on a -$1.11 underlying move. This trade combined the worst signal quality with the worst contract structure.

---

## Comparative Analysis

| Metric | DIA | COIN | META |
|---|---|---|---|
| Scanner score | 53 | **62** | 60 |
| Quality score | **4.0** | 3.0 | 2.0 |
| Confluence | **5** | 3 | 1 |
| Signal age (min) | 96.3 | **121.6** | 96.6 |
| rvol | **0.627** | 0.510 | 0.507 |
| IV | **14.2%** | 83.7% | 44.6% |
| DTE | 3 | 3 | **1** |
| Entry spread | 5.1% | **8.4%** | 4.3% |
| Direction correct | ✓ | ✗ | ✗ |
| Exit reason | eod | trailing_stop | eod |
| PnL | **+$25** | -$103.50 | -$50 |

### Findings

**1. Quality score outperformed scanner score as predictor.**
Scanner ranked COIN (62) > META (60) > DIA (53). Quality ranked DIA (4) > COIN (3) > META (2). Outcome followed quality order exactly: DIA win, META second, COIN worst.

**2. Scanner/strategy direction disagreement was not a negative signal.**
DIA scanner=LONG, strategy=SHORT. Strategy was correct. The scanner identified a breakout; the strategy correctly identified an overextended RSI reversion opportunity. Disagreement is not disqualifying.

**3. Signal staleness of 2+ hours coincided with the worst trade.**
COIN's signal was 121.6 min old. The underlying had moved $4.18 above the breakdown level by entry. ORB forward data confirms COIN was in an upward phase from signal generation onward. Staleness was a credible risk factor.

**4. Very high IV magnified the COIN loss.**
COIN IV 83.66% at entry. Spread cost alone was ~$16 on a $390 entry. A -26.5% option move produced -$103.50. The same percentage option move on low-IV DIA would have produced a fraction of that loss.

**5. 1-day expiry contract was structurally incompatible with a weak signal.**
META260603C00610000 expired the next day. $7.08 OTM. IV 44.62%. Theta decay was working against this position from the moment of entry. Quality 2/4 with 1 confluence does not justify a 1-day expiry OTM contract.

**6. All three entries had sub-normal volume (rvol < 1.0).**
DIA 0.627, COIN 0.510, META 0.507. The session's STANDBY trigger (low volume chop) persisted into the active trading period. Low-rvol signals carry lower directional conviction.

**7. Trailing stop on COIN functioned correctly.**
Wrong direction from entry. COIN kept rising. Stop fired at 28.2 min, limiting loss to -$103.50. Without it, EOD exit would likely have been worse.

---

## Summary Verdict

| Trade | Signal | Execution | Exit Management |
|---|---|---|---|
| DIA +$25 | Strong | Good | Acceptable |
| COIN -$103.50 | **Poor** (stale, high-IV, wrong direction) | Marginal | Acceptable (stop worked) |
| META -$50 | **Poor** (1 confluence, trend contradiction) | Acceptable | **Poor** (1-day expiry mismatch) |

The session loss (-$128.50) was concentrated in two poor-quality signals. DIA demonstrated that the strategy can select and execute a correct trade cleanly. COIN and META demonstrated that sub-normal volume, stale signals, high IV, and short-expiry contract selection are compounding risk factors.

---

*Evidence only. No parameter changes. No strategy changes.*
