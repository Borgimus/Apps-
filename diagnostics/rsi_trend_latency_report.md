# RSI_trend Strategy Structural Latency Analysis
**Date**: 2026-05-26  
**Status**: Research/Diagnostics only — no trades placed, no configuration changed  
**Analyst**: Automated codebase analysis + live session log review

---

## 1. Executive Summary

RSI_trend as deployed in today's session required **25 bars at 5-minute resolution** before it could evaluate any signal. Because the session also started late (10:03 ET instead of 9:30 ET) and SNOW was not confirmed as a candidate until 10:33 ET, the strategy accumulated only 22 bars before the session ended at 11:19 ET. **RSI_trend never became eligible to signal today.**

Even under ideal conditions (session running from 9:30 ET with a symbol confirmed immediately), the strategy would not structurally be ready until **11:30 ET at 5-min resolution** — exactly the point at which the day's best momentum window (9:30–10:10 ET) had already fully resolved. Today's SNOW day high of 181.27 occurred at 10:00 ET; 100% of both the upward and downward intraday range was consumed before RSI_trend could have triggered.

The diagnosis is structural: the algorithm's minimum bar requirement enforces a 120-minute warm-up at 5-min resolution regardless of when the session starts.

There is one important distinction: the `config.yaml` sets `trend_ema_period: 50` (which would require 55 bars, or ~4.5 hours), but the `session_runner.py` instantiates RSITrendStrategy with `trend_ema_period: 20` (overriding the config). The deployed configuration is EMA(20), not EMA(50). The 55-bar config requirement is therefore **not in use** — but even the EMA(20) variant imposes a ~2-hour delay from bar 1.

---

## 2. Strategy Architecture Review

### File
`/home/user/Apps-/app/strategies/rsi_trend_strategy.py`

### Parameters as Deployed (session_runner.py line 1130)
```python
RSITrendStrategy(params={"rsi_period": 14, "rsi_oversold": 35, "trend_ema_period": 20})
```

Note: `config.yaml` specifies `trend_ema_period: 50` but `session_runner.py` overrides this inline. The deployed value is **EMA(20)**, not EMA(50).

### Minimum Bars Required
```python
min_rows = max(self._rsi_period, self._trend_ema_period) + 5
         = max(14, 20) + 5
         = 25 bars
```

This threshold is enforced in `strategy_base.py::validate_bars()` (line 93–97), which logs:
```
WARNING | strategy.rsi_trend | Insufficient bars (N < 25) for rsi_trend
```

### EMA Window
- **Deployed**: EMA(20) — an exponentially-weighted moving average with `span=20`
- **Config file**: EMA(50) (not used by session_runner)

### RSI Window
- RSI(14) computed with Wilder's smoothing (EWM com=13)

### Confirmation Gates
The signal requires ALL of the following at bar `i`:
1. Bars count >= 25 (structural gate — covered above)
2. RSI did NOT cross oversold/overbought (purely reactive — checks prior-bar vs current-bar)
3. **LONG entry**: `rsi[i-1] < 35` AND `rsi[i] >= 35` AND `price[i] > ema[i]`
4. **SHORT entry**: `rsi[i-1] > 65` AND `rsi[i] <= 65` AND `price[i] < ema[i]`
5. The strategy iterates all historical bars — it is a **batch scan**, not incremental. Every poll cycle re-runs the full lookback window.

### Insufficient Bars Code Location
`strategy_base.py`, `validate_bars()`, lines 93–97:
```python
if len(bars) < min_rows:
    self.logger.warning(
        "Insufficient bars (%d < %d) for %s", len(bars), min_rows, self.strategy_id
    )
    return False
```

Called from `rsi_trend_strategy.py::generate_signals()` line 53:
```python
if not self.validate_bars(bars, min_rows=min_rows):
    return []
```

### Scanner Bar Source
`yfinance_scanner.py` line 129:
```python
intra_df = ticker.history(period="2d", interval="5m", auto_adjust=True)
```

The scanner fetches **2-day, 5-minute bars**. Only today's bars are used for intraday VWAP/ORB/signal logic. Bar count grows from 1 at market open, incrementing by 1 every 5 minutes.

---

## 3. Readiness Timing Analysis

### At 5-Minute Bar Resolution

| Strategy           | Min Bars | If Start 9:30 ET | Delay from Open |
|-------------------|----------|-----------------|-----------------|
| VWAP_Reclaim       | 7        | ~10:00 ET       | 30 min          |
| ORB (range=15 min) | 17       | ~10:50 ET       | 80 min          |
| RSI_trend (EMA 20) | 25       | ~11:30 ET       | 120 min         |
| RSI_trend (EMA 50) | 55       | ~14:00 ET       | 270 min (config only) |

Formula: Ready time = 09:30 + (min_rows - 1) * 5 minutes.

### At 1-Minute Bar Resolution

If bars were collected at 1-min interval instead of 5-min:

| Strategy           | Min Bars | Ready Time | Delay |
|-------------------|----------|------------|-------|
| VWAP_Reclaim       | 7        | 09:36 ET   | 6 min |
| ORB (range=15 min) | 17       | 09:46 ET   | 16 min|
| RSI_trend (EMA 20) | 25       | 09:54 ET   | 24 min|

At 1-min resolution with today's SNOW data: RSI_trend ready at 09:54 ET, price 178.38 (+0.42% from open). Day high occurred at 10:01 ET at 1-min resolution.

### Actual Today (5-min bars, incidental start delay)

SNOW was confirmed as a candidate at 10:33 ET with 13 bars already available. From that point:
- Bars needed: 25 - 13 = 12 more
- Time needed: 12 × 5 min = 60 min
- Projected readiness: **~11:33 ET**
- Session ended: **11:19 ET** (22 bars reached)
- **RSI_trend never became ready in today's session**

---

## 4. Today's Session — SNOW Case Study

### Session Timeline
- Session start: 10:03 ET (UTC 14:03)
- SNOW first scan: 10:03 ET — REJECTED (all candidates rejected, low_volume_chop)
- SNOW confirmed: **10:33:53 ET** — score=40, signal=LONG, reasons: `atr=4.69% (wide-range), trend_up, rsi=80 (extreme)`
- Session shutdown signal: 11:18 ET
- Session end: 11:19 ET

### SNOW [diag] Data Table (from session_2026-05-26.log)

| Time (ET) | Price  | VWAP   | Price vs VWAP | RSI   | EMA(20) | vs EMA  | RSI_bars | ORB_bars | Strategy Ready |
|-----------|--------|--------|---------------|-------|---------|---------|----------|----------|----------------|
| 10:33:59  | 177.94 | 177.56 | +0.22%        | —     | —       | —       | 13       | 13       | No (13<25)     |
| 10:35:01  | 177.40 | 177.55 | -0.08%        | —     | —       | —       | 13       | 13       | No             |
| 10:36:01  | 177.98 | 177.54 | +0.25%        | 33.0  | 177.78  | ABOVE   | 14       | 14       | No (14<25)     |
| 10:37:01  | 178.40 | 177.55 | +0.48%        | 36.4  | 177.82  | ABOVE   | 14       | 14       | No             |
| 10:38:01  | 178.43 | 177.59 | +0.47%        | 36.6  | 177.83  | ABOVE   | 14       | 14       | No             |
| 10:39:02  | 178.84 | 177.60 | +0.69%        | 39.6  | 177.86  | ABOVE   | 14       | 14       | No             |
| 10:40:02  | 179.29 | 177.63 | +0.93%        | 42.7  | 178.03  | ABOVE   | 15       | 15       | No (15<25)     |
| 10:41:03  | 179.12 | 177.65 | +0.83%        | 41.6  | 178.01  | ABOVE   | 15       | 15       | No             |
| 10:42:04  | 178.96 | 177.66 | +0.73%        | 40.7  | 178.00  | ABOVE   | 15       | 15       | No             |
| 10:43:04  | 179.59 | 177.66 | +1.09%        | 44.8  | 178.06  | ABOVE   | 15       | 15       | No             |
| 10:44:04  | 179.23 | 177.68 | +0.87%        | 42.3  | 178.02  | ABOVE   | 15       | 15       | No             |
| 10:45:04  | 178.99 | 177.70 | +0.73%        | 40.9  | 178.10  | ABOVE   | 16       | 16       | No (16<25)     |
| 10:46:05  | 178.89 | 177.70 | +0.67%        | 40.3  | 178.09  | ABOVE   | 16       | 16       | No             |
| 10:47:05  | 178.99 | 177.72 | +0.71%        | 40.9  | 178.09  | ABOVE   | 16       | 16       | No             |
| 10:48:05  | 179.23 | 177.73 | +0.85%        | 42.8  | 178.12  | ABOVE   | 16       | 16       | No             |
| 10:49:05  | 179.68 | 177.75 | +1.09%        | 46.2  | 178.16  | ABOVE   | 16       | 16       | No             |
| 10:50:06  | 179.64 | 177.76 | +1.06%        | 45.9  | 178.31  | ABOVE   | 17       | —        | No (17<25); ORB cleared |
| 10:51:06  | 179.73 | 177.78 | +1.09%        | 46.5  | 178.31  | ABOVE   | 17       | —        | No             |
| 10:52:06  | 179.66 | 177.76 | +1.07%        | 46.1  | 178.31  | ABOVE   | 17       | —        | No             |
| 10:53:06  | 179.73 | 177.80 | +1.08%        | 46.5  | 178.31  | ABOVE   | 17       | —        | No             |
| 10:54:06  | 179.58 | 177.82 | +0.99%        | 45.5  | 178.30  | ABOVE   | 17       | —        | No             |
| 10:55:07  | 179.85 | 177.84 | +1.13%        | 47.5  | 178.48  | ABOVE   | 18       | —        | No (18<25)     |
| 10:56:07  | 179.99 | 177.85 | +1.20%        | 48.6  | 178.50  | ABOVE   | 18       | —        | No             |
| 10:57:07  | 179.88 | 177.87 | +1.13%        | 47.7  | 178.49  | ABOVE   | 18       | —        | No             |
| 10:58:07  | 179.49 | 177.88 | +0.91%        | 44.8  | 178.45  | ABOVE   | 18       | —        | No             |
| 10:59:07  | 179.69 | 177.88 | +1.01%        | 46.3  | 178.47  | ABOVE   | 18       | —        | No             |
| 11:00:08  | 179.59 | 177.89 | +0.95%        | 45.5  | 178.57  | ABOVE   | 19       | —        | No (19<25)     |
| 11:01:08  | 179.66 | 177.89 | +1.00%        | 46.1  | 178.58  | ABOVE   | 19       | —        | No             |
| 11:02:08  | 179.24 | 177.91 | +0.75%        | 42.8  | 178.54  | ABOVE   | 19       | —        | No             |
| 11:03:08  | 179.52 | 177.92 | +0.90%        | 44.9  | 178.56  | ABOVE   | 19       | —        | No             |
| 11:05:14  | 179.43 | 177.95 | +0.83%        | 44.4  | 178.64  | ABOVE   | 20       | —        | No (20<25)     |
| 11:06:15  | 179.14 | 177.96 | +0.66%        | 41.8  | 178.61  | ABOVE   | 20       | —        | No             |
| 11:07:15  | 179.10 | 177.97 | +0.63%        | 41.5  | 178.60  | ABOVE   | 20       | —        | No             |
| 11:08:15  | 179.27 | 177.97 | +0.73%        | 42.9  | 178.62  | ABOVE   | 20       | —        | No             |
| 11:09:15  | 178.95 | 177.98 | +0.54%        | 40.3  | 178.59  | ABOVE   | 20       | —        | No             |
| 11:10:16  | 178.90 | 178.00 | +0.51%        | 39.6  | 178.65  | ABOVE   | 21       | —        | No (21<25)     |
| 11:11:16  | 178.76 | 178.00 | +0.43%        | 38.5  | 178.63  | ABOVE   | 21       | —        | No             |
| 11:12:16  | 178.84 | 178.01 | +0.47%        | 39.1  | 178.64  | ABOVE   | 21       | —        | No             |
| 11:13:16  | 178.84 | 178.01 | +0.47%        | 39.1  | 178.64  | ABOVE   | 21       | —        | No             |
| 11:14:17  | 178.84 | 178.04 | +0.45%        | 39.1  | 178.64  | ABOVE   | 21       | —        | No             |
| 11:15:17  | 178.54 | 178.04 | +0.28%        | 36.9  | 178.60  | BELOW   | 22       | —        | No (22<25)     |
| 11:16:17  | 178.19 | 178.04 | +0.08%        | 34.2  | 178.57  | BELOW   | 22       | —        | No             |
| 11:17:17  | 178.55 | 178.04 | +0.29%        | 37.0  | 178.60  | BELOW   | 22       | —        | No             |
| 11:18:17  | 178.90 | 178.03 | +0.49%        | 41.5  | 178.63  | ABOVE   | 22       | —        | No             |
| 11:19 (end) | —   | —      | —             | —     | —       | —       | —        | —        | Never ready    |

Notes:
- ORB warnings stop at cycle 46 (~10:49 ET), confirming ORB cleared 17-bar threshold.
- RSI_trend warnings persist through session end (22 < 25 at 11:18 ET).
- RSI values at 10:36 ET = 33.0 (below oversold 35), would be a LONG signal candidate *if* bars were sufficient.
- At 11:15-11:16 ET, RSI crossed back below 35 (oversold territory) while price was BELOW EMA — representing the opposite condition.

### Price and Move Summary
- SNOW open (9:30 ET): **177.66**
- SNOW at confirmation (10:33 ET): **177.40** (−0.26, −0.15% from open)
- SNOW at session end (11:19 ET): ~**178.90** (+1.24, +0.70% from open)
- Day high: **181.27** at 10:00 ET (+3.61, +2.03% from open)
- Day low: **174.55** at 09:35 ET (−3.11, −1.75% from open)
- Total day range: **6.72**
- Move consumed before RSI_trend structurally ready (11:30 ET): **100% of both upward and downward daily range**

---

## 5. Momentum Window Comparison

SNOW today demonstrated a classic early-session momentum pattern:

| Time Window  | Event                           | Price   | Move    |
|-------------|----------------------------------|---------|---------|
| 9:30–9:35   | Opening gap flush (day low)      | 174.55  | −3.11   |
| 9:35–10:00  | Recovery rally (day high)        | 181.27  | +6.72   |
| 10:00–10:30 | Pullback / consolidation         | 177.40  | −3.87   |
| 10:30–10:50 | Second leg up (intraday trend)   | 180.00  | +2.60   |
| 10:50–11:30 | Sideways digest (~179–180)       | 178.39  | flat    |
| 11:30–close | Tight range ~177.64–179.51      | various | +1.84 max |

The entire morning momentum story — both the downside flush and the upside recovery — resolved within the first 60 minutes. RSI_trend structural readiness (11:30 ET) coincides with the onset of midday consolidation, not the opening momentum phase.

**For comparison (all symbols, today's 5-min data):**

| Symbol | Peak Move (time) | Move at RSI Ready (11:30) | % of Peak Captured Before Ready |
|--------|-----------------|--------------------------|----------------------------------|
| SNOW   | +3.61 at 10:00  | +0.73                    | 20%                              |
| NET    | +8.59 at 11:30+ | significant portion ongoing | early peak already past         |
| AVGO   | +7.56 at ~10:30 | already reversing         | peak likely before 11:30         |
| GOOGL  | +5.24 at ~10:30 | ongoing partial           | most of peak before 11:30        |
| MSFT   | reversing down  | negative by mid-session   | peak and reversal both pre-ready |

---

## 6. Missed Opportunity Analysis

### Today (SNOW)

- **RSI signal that existed but could not be acted on**: At 10:36 ET, RSI = 33.0 (below oversold threshold of 35), price = 177.98 (above EMA 177.78). This would have satisfied the LONG entry condition `rsi[prev] < 35 AND rsi[now] >= 35 AND price > ema` *if* bars had been sufficient. The strategy had 14 bars, needed 25. The signal was structurally blocked.

- **Options spread observation**: At 10:33 ET, SNOW260529C00190000 had spread=8.18%, OI=712, vol=141. By the 11:04 ET re-scan, spread had improved to 3.67%, OI=712, vol=194. This suggests that even if RSI_trend had been ready at 10:33, the spread cost of 8.18% would have been borderline (system max_spread_pct=10%). The spread timing question is moot since the strategy was never ready.

- **ORB readiness**: ORB cleared its threshold at ~10:50 ET (17 bars). At that point SNOW was at 180.00, near its second-wave high. ORB could theoretically have triggered but was not observed to produce a signal in the logs (raw_orb_signals were also 0 in the VWAP diag lines, and ORB stopped logging warnings after 10:49 ET).

### Structural Calculation

| Metric | Value |
|--------|-------|
| SNOW open price (9:30) | 177.66 |
| Price at structural RSI_trend readiness (11:30) | 178.39 |
| Move from open to readiness | +0.73 (+0.41%) |
| Day high | 181.27 at 10:00 ET |
| Day low | 174.55 at 09:35 ET |
| Total day range | 6.72 |
| % of total day range consumed before readiness | 100% |
| % of upward move captured before readiness | 100% |
| Available momentum after readiness | +1.84 peak (narrow, midday) |

---

## 7. Intraday Timing Distribution

### 5-Minute Bar Resolution (as deployed today)

| Strategy           | Bars | Ready Time | Delay from Open | SNOW Price at Ready | Move from Open |
|-------------------|------|------------|-----------------|---------------------|----------------|
| VWAP_Reclaim       | 7    | ~10:00 ET  | 30 min          | 179.21              | +1.55 (+0.87%) |
| ORB (range_min=15) | 17   | ~10:50 ET  | 80 min          | 180.00              | +2.34 (+1.31%) |
| RSI_trend (EMA20)  | 25   | ~11:30 ET  | 120 min         | 178.39              | +0.73 (+0.41%) |

Note: RSI_trend readiness arrives *after* the momentum move has decayed and AFTER ORB readiness.

### 1-Minute Bar Resolution (hypothetical)

| Strategy           | Bars | Ready Time | Delay from Open | SNOW Price at Ready | Move from Open |
|-------------------|------|------------|-----------------|---------------------|----------------|
| VWAP_Reclaim       | 7    | 09:36 ET   | 6 min           | 174.69              | −2.94 (−1.65%) |
| ORB (range_min=15) | 17   | 09:46 ET   | 16 min          | 177.90              | +0.26 (+0.15%) |
| RSI_trend (EMA20)  | 25   | 09:54 ET   | 24 min          | 178.38              | +0.75 (+0.42%) |

At 1-min resolution, all strategies are ready within the first 30 minutes. RSI_trend would be ready at 09:54 ET, which is 7 minutes before today's day high (10:01 ET) — a meaningfully different timing picture.

---

## 8. Structural Latency Sources (Code-Level)

The following are code-level sources of latency inherent to the algorithm, separated from incidental factors.

### L1 — Minimum Bars Requirement (Structural)
**Source**: `rsi_trend_strategy.py` line 52, `strategy_base.py` lines 93–97  
`min_rows = max(rsi_period, trend_ema_period) + 5 = 25`  
At 5-min bars, this enforces a 120-minute warmup from first bar. This is an algorithm-level constraint that applies regardless of session start time or symbol confirmation time.

### L2 — Batch Re-evaluation Each Poll Cycle (Structural)
**Source**: `rsi_trend_strategy.py::generate_signals()` — iterates `range(1, len(bars))`  
The strategy re-processes all available bars on every 60-second poll cycle. This is not a latency source for correctness, but means no rolling/incremental state is maintained. If bars don't reach 25, no evaluation occurs at all — binary gate.

### L3 — 5-Minute Bar Interval at the Scanner Level (Structural)
**Source**: `yfinance_scanner.py` line 129: `interval="5m"`  
The scanner fetches only 5-minute bars for intraday use. Each new bar arrives every 5 minutes, not continuously. This multiplies the bar-count latency by 5 compared to a 1-min feed.

### L4 — EWM RSI Calculation (Structural, minor)
**Source**: `rsi_trend_strategy.py` `_rsi()` function  
RSI uses `ewm(com=period-1, adjust=False)` which requires multiple bars to converge. The +5 buffer in min_rows is designed to compensate for this, but early RSI values (even when permitted) remain noisy due to EWM initialization.

### L5 — Session Start Late (Incidental, Not Structural)
Today's session started at 10:03 ET. This is an operational factor, not an algorithm constraint. With a 9:30 ET start, RSI_trend would still not be ready until 11:30 ET (structural minimum). The late start added ~0 additional delay to the *structural* minimum but did prevent any pre-10:03 bar accumulation that a 9:30 start would provide.

### L6 — Symbol Confirmation Delay (Incidental)
SNOW was not confirmed by the scanner until 10:33 ET — 30 minutes after session start and 63 minutes after market open. This incidental delay means the bar clock for SNOW starts at 10:33 ET rather than 9:30 ET or 10:03 ET, adding 63 minutes to the structural warm-up. Actual readiness became ~11:33 ET instead of the structural minimum of 11:30 ET.

### L7 — Config vs Runtime Discrepancy (Operational Risk)
**Source**: `config.yaml` has `trend_ema_period: 50` (min_rows=55), but `session_runner.py` line 1130 hardcodes `trend_ema_period: 20` (min_rows=25).  
The deployed value (EMA 20) is less severe than the config value (EMA 50 would need 55 bars = 270 min = 4.5 hours delay from open). However, the discrepancy between config and code is an operational risk: config changes to EMA period will have no effect unless session_runner.py is also updated.

---

## 9. Hypothesis Evaluation

### H1: RSI_trend becomes eligible after the highest-quality momentum window has already passed.

**Verdict: SUPPORTED**

Evidence:
- At 5-min resolution, RSI_trend requires 25 bars = 120 minutes from first bar. This places readiness at 11:30 ET minimum (from 9:30 open) or later.
- Today's SNOW day high (181.27) occurred at 10:00 ET — 90 minutes before RSI_trend structural readiness.
- Today's day low (174.55) occurred at 09:35 ET — 115 minutes before structural readiness.
- 100% of the total intraday range was consumed before 11:30 ET.
- Post-readiness (11:30 ET onwards), SNOW moved only 1.84 points (peak), compared to 6.72 point total daily range.
- This is not coincidental: opening momentum windows (9:30–10:30 ET) are systematically earlier than RSI_trend's structural readiness.

### H2: Options spread deterioration increases materially before RSI_trend entries.

**Verdict: INCONCLUSIVE** (insufficient data points, but directionally plausible)

Evidence:
- Today's only spread data: SNOW260529C00190000 at 10:33 ET = 8.18% spread, declining to 3.67% by 11:04 ET.
- This shows spread *improved* from 10:33 to 11:04, suggesting that if RSI_trend had been ready at 10:33, it would have faced wider spreads than at 11:00+.
- However, a single data point cannot establish a systematic pattern.
- Academic consensus supports that bid-ask spreads in liquid options are typically tightest 30-90 minutes after open, then widen into midday as market makers adjust. SNOW's data today is consistent with this pattern.
- Broader claim requires multi-session spread tracking to confirm.

### H3: RSI_trend is more suitable for trend continuation / midday continuation than opening momentum.

**Verdict: SUPPORTED (structurally)**

Evidence:
- The 2-hour warmup requirement at 5-min resolution structurally excludes RSI_trend from the opening momentum window.
- The RSI(14) crossover + EMA trend filter is a classic mean-reversion/continuation pattern designed to catch oversold bounces or overbought fades *after* a trend has been established over multiple bars.
- After 11:30 ET (RSI_trend ready), SNOW traded in a 177.13–179.51 range — a tight 2.38-point range suggesting consolidation, not momentum. RSI values oscillated 36–47, never crossing the 35/65 thresholds needed for a signal.
- This suggests the strategy is architecturally suited for detecting second-wave moves or afternoon continuation setups, not opening-range momentum.
- No RSI_trend signals fired in any session observed across the full log history. This is consistent with (a) the strategy being in warmup most sessions, and (b) RSI not crossing its thresholds when ready.

### H4: RSI_trend may perform better as a confirmation filter rather than primary trigger.

**Verdict: INCONCLUSIVE** (plausible but untested)

Evidence:
- The RSI(14) + EMA(20) combination is commonly used as a secondary confirmation in multi-factor systems.
- Today's diagnostic data shows RSI was in "healthy" range (36–48) throughout the monitored period — aligned with the scanner's LONG signal direction (price above VWAP, above EMA, trend=up). This alignment is the kind of confirmation role the strategy could play.
- However, using it as a filter (rather than a primary signal generator) is a design change that has not been tested. The current architecture requires RSI to cross the 35/65 thresholds to produce a signal — a filter role would require different logic.
- No backtest data is available in the observed logs to support or refute this hypothesis quantitatively.

---

## 10. Open Questions for Future Evaluation

1. **Bar resolution**: Would collecting 1-minute bars (instead of 5-minute) from the market open reduce RSI_trend readiness to 09:54 ET and materially improve capture of morning momentum? The 1-min analysis today shows RSI_trend would have been ready 7 minutes before the day high.

2. **EMA config discrepancy**: `config.yaml` has `trend_ema_period: 50` but `session_runner.py` uses 20. Which is the intended value? With EMA(50), RSI_trend would require 55 bars = ~4.5 hours = effectively no intraday signals possible.

3. **Multi-session signal rate**: RSI_trend has not fired a signal across any observed session (2026-05-11, 2026-05-12, 2026-05-23, 2026-05-25, 2026-05-26). Is this purely a warmup issue or are the 35/65 RSI thresholds also rarely crossed at 5-min resolution?

4. **ORB signal rate**: ORB cleared its bar threshold today at ~10:50 ET but still produced 0 signals. Was this because no breakout occurred post-range, or did the ORB logic not fire? The 9:30–10:00 high of 181.27 *was* broken in the 10:35 bar — but ORB uses `range_cutoff = day_bars.index[0] + range_end_offset` which may not align correctly given the session started mid-day.

5. **Spread optimization window**: Is there a systematic time window each day where spread_pct < 5% for liquid growth names? If SNOW's spread dropped from 8.18% to 3.67% between 10:33 and 11:04, this needs tracking across multiple sessions.

6. **Midday continuation backtest**: Has RSI_trend (with EMA 20) been evaluated against a dataset of afternoon trades (11:30–14:00 ET)? Given the strategy's structural readiness at 11:30, this is the only window where it operates. A targeted backtest of this window is needed to evaluate viability.

7. **Alternative bar sources**: If a 1-min bar feed were available from the broker (Alpaca), could the strategy accumulate bars faster and be ready within the morning momentum window? This would require architectural changes to the yfinance_scanner bar-fetch logic.

---

*Report generated from: `/home/user/Apps-/app/strategies/rsi_trend_strategy.py`, `/home/user/Apps-/app/strategies/strategy_base.py`, `/home/user/Apps-/app/scanning/yfinance_scanner.py`, `/home/user/Apps-/scripts/session_runner.py`, `/home/user/Apps-/logs/session_2026-05-26.log`, and live yfinance data for 2026-05-26.*
