# Signal Staleness Analysis

**Sessions:** 2026-05-29, 2026-06-02  
**Signal bridge rows:** 857 total; 639 excluded (rsi_trend diagnostic); **218 analyzed**  
**Executed trades with age data:** 5

---

## Data Limitations

The following constraints bound all conclusions in this document:

1. **n=5 executed trades.** No statistical inference is valid. Observations are recorded as-is.
2. **Non-random trade selection.** Executed signals are those that cleared all risk and position gates in sequence. Unexecuted signals were blocked primarily by `max_trades_per_day` (155/155 blocks), not by quality or liquidity, creating severe selection bias.
3. **2026-05-29 block contamination.** All 155 "Max trades per day reached" blocks on 2026-05-29 reflect the pre-fix entry counter bug: exits consumed entry slots. These blocks do not represent capacity-constrained decisions. Commit `2d15a18` fixed the bug before the 2026-06-02 session.
4. **No intraday position marks.** MFE and MAE are bounded only for trailing-stop exits (stop trigger = known MAE). EOD exits provide no intraday bound.
5. **2 sessions, same market regime.** Both sessions had rvol < 1.0 throughout and were in STANDBY from open.

---

## Signal Population

| Category | Count |
|---|---|
| Total bridge rows | 857 |
| Excluded (rsi_trend diagnostic, quality=0) | 639 |
| **Analyzed (quality > 0)** | **218** |
| Traded | 5 |
| Blocked (all: max_trades_per_day) | 155 |
| Skipped | 58 |

### Strategy breakdown (analyzed signals)

| Strategy | Signals | Traded | Blocked | Skipped |
|---|---|---|---|---|
| orb | 104 | 0 | 77 | 27 |
| vwap_reclaim | 114 | 5 | 78 | 31 |

---

## Age Distribution — All Signals (quality > 0)

| Bucket | Count | % of total | Avg quality | Avg scanner | Avg age (min) |
|---|---|---|---|---|---|
| 0–15 min | 2 | 0.9% | 3.0 | 53.0 | 14.6 |
| 15–30 min | 25 | 11.5% | 3.0 | 53.3 | 23.6 |
| 30–60 min | 33 | 15.1% | 2.82 | 53.5 | 39.3 |
| **60–120 min** | **128** | **58.7%** | 1.95 | 57.9 | 80.6 |
| 120+ min | 30 | 13.8% | 2.03 | 62.0 | 127.4 |

**Structural finding:** 72.5% of actionable signals were older than 60 minutes. Fresh signals (0–15 min) represented < 1% of the population. Signal aging is a structural property of the system (signals generated once per bar, carried forward through the poll cycle).

---

## Quality Score by Age Bucket

| Bucket | Q=1 | Q=2 | Q=3 | Q=4 | Total |
|---|---|---|---|---|---|
| 0–15 min | 0 | 0 | 2 | 0 | 2 |
| 15–30 min | 0 | 0 | 25 | 0 | 25 |
| 30–60 min | 2 | 2 | 29 | 0 | 33 |
| 60–120 min | 57 | 22 | 48 | 1 | 128 |
| 120+ min | 0 | 29 | 1 | 0 | 30 |

Quality is not monotone with age. Quality-3 signals cluster in the 15–60 min range. Quality-1 signals appear only in the 60–120 min range. The single quality-4 signal (DIA) was 96 min old.

---

## Age-Bucketed Trade Analysis

### 0–15 min (n=0 trades)

No executed trades. Both signals in this bucket were blocked by risk capacity.

---

### 15–30 min (n=1 trade)

| Metric | SNOW |
|---|---|
| Session | 2026-05-29 |
| Signal age | 19.0 min |
| Quality score | 3.0 |
| Scanner score | 60.0 |
| rvol | 0.657 |
| Strategy | vwap_reclaim LONG |
| Fill price | $1.04 |
| Exit price | $0.725 |
| Exit reason | trailing_stop |
| Hold | 1.5 min |
| PnL | **-$31.50** |
| MAE | -30.3% (at stop trigger) |
| MFE | unknown |
| IV | 0.0 (data gap) |
| DTE | 0 (same-day expiry) |

Notes: Same-day expiry. IV not captured. Trailing stop fired at 1.5 min — essentially immediate adverse move from entry. Fresh signal, same-day expiry, 8.3% entry spread.

---

### 30–60 min (n=1 trade)

| Metric | MSFT |
|---|---|
| Session | 2026-05-29 |
| Signal age | 39.3 min |
| Quality score | 2.0 |
| Scanner score | 60.0 |
| rvol | 0.552 |
| Strategy | vwap_reclaim LONG |
| Fill price | $0.26 |
| Exit price | $0.17 |
| Exit reason | trailing_stop |
| Hold | 1.0 min |
| PnL | **-$9.00** |
| MAE | -34.6% (at stop trigger) |
| MFE | unknown |
| IV | 0.0 (data gap) |
| DTE | 0 (same-day expiry) |

Notes: Same-day expiry. Very small absolute dollar amount ($9). Trailing stop fired at 1.0 min. Both 2026-05-29 trades used same-day expiry options — a separate risk factor from signal age.

---

### 60–120 min (n=2 trades)

| Metric | DIA | META |
|---|---|---|
| Session | 2026-06-02 | 2026-06-02 |
| Signal age | 96.3 min | 96.6 min |
| Quality score | **4.0** | 2.0 |
| Scanner score | 53.0 | 60.0 |
| rvol | 0.627 | 0.507 |
| Strategy | vwap_reclaim SHORT | vwap_reclaim LONG |
| Fill price | $1.60 | $4.70 |
| Exit price | $1.85 | $4.20 |
| Exit reason | eod_exit | eod_exit |
| Hold | 68.6 min | 38.2 min |
| PnL | **+$25.00** | **-$50.00** |
| MFE | ≥+15.6% | unknown |
| MAE | unknown | ≤-10.6% |
| IV | 14.18% | 44.62% |
| DTE | 3 | **1** |

Notes: DIA and META had nearly identical signal ages but opposite outcomes. DIA was the only quality-4 signal in the dataset. META had a 1-day expiry with quality=2/4 and confluence=1. Scanner trend field for META was "down" despite a LONG signal — internal contradiction.

---

### 120+ min (n=1 trade)

| Metric | COIN |
|---|---|
| Session | 2026-06-02 |
| Signal age | 121.6 min |
| Quality score | 3.0 |
| Scanner score | 62.0 |
| rvol | 0.51 |
| Strategy | vwap_reclaim SHORT |
| Fill price | $3.90 |
| Exit price | $2.865 |
| Exit reason | trailing_stop |
| Hold | 28.2 min |
| PnL | **-$103.50** |
| MAE | -26.5% (at stop trigger) |
| MFE | unknown |
| IV | **83.66%** |
| DTE | 3 |

**ORB forward performance (direction-adjusted):**

| Horizon | Underlying | Return vs SHORT |
|---|---|---|
| +5 min from ORB signal | $173.12 | -0.23% |
| +15 min | $174.12 | -0.80% |
| +30 min | $174.93 | -1.27% |

ORB signal was generated at COIN=$172.73. By vwap_reclaim entry, COIN was at $176.91 — $4.18 higher. Forward data confirms underlying was in upward momentum from signal generation time. Direction was wrong before entry.

Notes: Highest IV entry (83.66%). Widest absolute loss. Signal was 2 hours old; underlying had moved materially from breakdown level.

---

## Executed Trade Summary

| Symbol | Age (min) | Quality | Scanner | rvol | IV | DTE | PnL | MAE | Exit |
|---|---|---|---|---|---|---|---|---|---|
| SNOW | 19.0 | 3.0 | 60 | 0.66 | 0.0* | 0 | -$31.50 | -30.3% | trailing_stop |
| MSFT | 39.3 | 2.0 | 60 | 0.55 | 0.0* | 0 | -$9.00 | -34.6% | trailing_stop |
| DIA | 96.3 | **4.0** | 53 | 0.63 | 14.2% | 3 | **+$25.00** | unknown | eod_exit |
| META | 96.6 | 2.0 | 60 | 0.51 | 44.6% | **1** | -$50.00 | ≤-10.6% | eod_exit |
| COIN | 121.6 | 3.0 | 62 | 0.51 | **83.7%** | 3 | -$103.50 | -26.5% | trailing_stop |

*iv=0 in 2026-05-29 journal records — data gap, not zero IV.

---

## Hypothesis Evaluation

### H1: Signal age negatively correlates with performance

**Result: INCONCLUSIVE**

No valid correlation can be computed from 5 non-randomly selected observations.

Observational: the oldest signal (COIN, 122 min) produced the worst absolute loss. However, the single win (DIA, 96 min) was nearly as old. The freshest traded signal (SNOW, 19 min) was a loss. Age alone does not discriminate wins from losses in this set.

Minimum sample required for meaningful correlation: ~30 executed trades.

---

### H2: Signals older than 60 minutes underperform fresh signals

**Result: NOT SUPPORTED by this data**

| Group | Trades | Wins | Win Rate | Avg PnL |
|---|---|---|---|---|
| < 60 min (SNOW, MSFT) | 2 | 0 | 0% | -$20.25 |
| ≥ 60 min (DIA, META, COIN) | 3 | 1 | 33% | -$42.83 |

Fresh signals had 0% win rate. Stale signals had a 33% win rate and one winner (+$25). Both groups lost money on average. H2 is not supported; in this data the directional relationship is reversed.

Confounds that prevent generalization:
- The two fresh trades (2026-05-29) used same-day expiry options (DTE=0), adding a separate structural disadvantage
- The sole win in the ≥60-min group was the only quality-4 signal in the entire dataset

---

### H3: Quality score remains predictive after controlling for age

**Result: CONSISTENT WITH DATA — not confirmable from n=5**

| Quality | Trades | Wins | Avg PnL | Age range (min) |
|---|---|---|---|---|
| 2.0 | 2 | 0 | -$29.50 | 39.3 – 96.6 |
| 3.0 | 2 | 0 | -$67.50 | 19.0 – 121.6 |
| 4.0 | 1 | **1** | +$25.00 | 96.3 |

Quality-4 = only win. Quality-2 and 3 = all losses. This is consistent with quality being predictive, but the quality-4 signal is n=1 and also happens to be the only trade where the strategy correctly faded a scanner-contradiction signal. It is not possible to isolate quality from other factors.

Quality-3 average PnL (-$67.50) is worse than quality-2 (-$29.50), which appears contradictory. This is driven by COIN's high IV (83.66%) inflating the dollar loss rather than by quality signal failure — a confound.

---

### H4: Scanner score remains predictive after controlling for age

**Result: NOT SUPPORTED — scanner score inversely correlated with outcome in this sample**

| Scanner | Trades | Wins | Avg PnL |
|---|---|---|---|
| 53 (DIA) | 1 | 1 | +$25.00 |
| 60 (SNOW, MSFT, META) | 3 | 0 | -$30.17 |
| 62 (COIN) | 1 | 0 | -$103.50 |

The lowest scanner score (53) was the only win. The highest (62) was the worst loss. Directionally, scanner score is inversely correlated with outcome in this data.

**Explanatory note:** DIA's scanner score of 53 was partly suppressed because the scanner tagged it as a breakout LONG while the vwap_reclaim strategy generated a SHORT. A scanner/strategy disagreement may reduce the composite scanner score. The winning trade was a reversion signal, not a breakout. Scanner scores are optimized for breakout strength; they may underrate reversion setups.

---

## Cross-Cutting Observations

**1. Same-day expiry is a separate risk factor from signal age.**  
Both 2026-05-29 trades (SNOW, MSFT) used same-day expiry options and both lost within 1.5 min of entry. Signal age was 19 and 39 min respectively — not the oldest signals. The expiry structure may have been more determinative than the signal age.

**2. IV at entry was the strongest dollar-loss predictor.**  
COIN IV 83.7% → $103.50 loss on a -26.5% option move. DIA IV 14.2% → +$25.00 win on a +15.6% move. High IV inflates both premium and loss magnitude when direction is wrong, independent of signal age.

**3. Fresh signals are extremely rare in this system.**  
Only 2/218 analyzed signals (0.9%) were under 15 minutes old. The system's signal generation model (once per bar) and 60-second poll cycle structurally produce aged signals. A "fresh signal" regime (0–15 min) is nearly unobservable in current session data.

**4. All 5 executed trades had sub-normal volume (rvol < 1.0).**  
Both sessions were in STANDBY from open due to low-volume chop. Every executed trade occurred under below-average volume conditions. This reduces the generalizability of any staleness finding to normal-volume conditions.

**5. ORB forward performance data covers only 1 signal.**  
One COIN ORB signal has populated `orb_fwd_pct` fields (age 86.6 min, quality 2, direction wrong). No age-stratified ORB forward analysis is possible.

---

## What Additional Data Would Be Required

To test H1–H4 with adequate statistical power:

| Requirement | Current | Target |
|---|---|---|
| Executed trades | 5 | ≥30 |
| Sessions | 2 | ≥10 |
| Age buckets with ≥5 trades | 0 | All 5 |
| Intraday position marks (MFE/MAE) | 0 | Per-cycle |
| rvol > 1.0 entries | 0 | Mix needed |
| Quality-4 observations | 1 | ≥5 |

---

*Evidence only. No recommendations.*
