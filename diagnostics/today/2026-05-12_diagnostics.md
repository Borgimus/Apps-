# Post-Session Diagnostics — 2026-05-12
**Generated:** 2026-05-12 11:30:00 ET  
**System:** Paper Options Trading — Diagnostic Mode  
**Data note:** All yfinance market data labeled RESEARCH DATA ONLY. No broker calls made. No trades placed.

---

## 1. Executive Summary

Today's session produced one executed trade (MSFT PUT, CLI fallback) resulting in a **-$109.50 loss** (-17.8%). All 13 symbols in the universe were rejected by the scanner due to **universe-wide low-volume chop** (rvol range: 0.044–0.166; threshold: 0.50). The CLI fallback mechanism activated and selected MSFT (highest score=50), but allowed a trade entry in an environment where exit spreads subsequently reached **16.42%** — 64% above the 10% risk manager limit.

Three strategy engines (ORB, VWAP Reclaim, RSI Trend) generated **60 signals** across 91 one-minute bars per symbol (09:30–11:00 ET). All signals were blocked by scanner gates or session buffers on the non-fallback path. The VWAP strategy was the most active (46/60 signals, 77%), consistent with its sensitivity to micro price oscillations around VWAP in choppy tape.

**Key finding:** The CLI fallback does not have a minimum rvol floor. When the entire universe is in chop, the fallback should enter STANDBY rather than select the least-bad candidate. Exit spread monitoring at trade exit is also absent from the current code path.

---

## 2. CLI Fallback Audit

### Code Path Analysis

When all scanner candidates are rejected, the paper trader activates a CLI fallback that:
1. Selects the symbol with the highest scanner score (MSFT, score=50)
2. Bypasses: `rvol_threshold`, `min_score_threshold`, `alpaca_confirmation`
3. Retains: `risk_manager`, `liquidity_filter`, `no_trade_buffer`, `cooldown_after_loss`, `max_active_positions`

The following gates **remain active** on the fallback path:
- Risk manager (max_risk_per_trade=1%, max_trades_per_day=3, max_daily_loss=2%)
- Liquidity filter (min_OI=100, min_volume=50, max_spread_pct=10%)
- Session buffer (no-trade first/last 15 min)
- Cooldown after loss (15 min)
- Max 1 active position

The following gates **are bypassed**:
- rvol_threshold (min 0.5; MSFT rvol=0.063 at scan time)
- min_score_threshold (min 40; MSFT=50 passes, but bypass mechanism still skips the gate check)
- Alpaca confirmation signal

### Risk Rating: MEDIUM

**Finding:** The fallback correctly retained liquidity and risk gates, but the `max_spread_pct` check runs only at entry (09:46:44, spread was within limits). The exit spread at 10:14:52 was 16.42% ($4.64 bid / $5.47 ask) — 64% above the 10% limit — and was not re-evaluated. In a universe-wide chop session, even MSFT's rvol of 0.063 indicates the market is not providing the volume needed for efficient option pricing.

**Recommendation:** Add a secondary gate to the CLI fallback:
```
if max(rvol_universe) < 0.20:
    log.warning("STANDBY: universe-wide chop, max rvol=%.3f < 0.20 floor")
    return  # no fallback trade
```
Additionally, add an exit-time spread re-check: if spread_pct > max_spread_pct at exit attempt, log a warning and consider marketable limit escalation.

---

## 3. Scanner Rejection Table

All 13 symbols rejected. Rejection reason: `low_volume_chop (rvol < 0.5 threshold)`.

| Symbol | Score | Signal   | rvol  | RSI   | Trend     | VWAP Position | Rejection Reason                     |
|--------|-------|----------|-------|-------|-----------|---------------|--------------------------------------|
| MSFT   | 50    | SHORT    | 0.063 | 42.61 | sideways  | below_vwap    | low_volume_chop (rvol=0.063 < 0.5)  |
| NVDA   | 48    | LONG     | 0.136 | 75.32 | up        | above_vwap    | low_volume_chop (rvol=0.136 < 0.5)  |
| AMD    | 40    | LONG     | 0.158 | 80.18 | up        | above_vwap    | low_volume_chop (rvol=0.158 < 0.5)  |
| AAPL   | 38    | LONG     | 0.109 | 79.43 | up        | at_vwap       | low_volume_chop (rvol=0.109 < 0.5)  |
| AMZN   | 33    | NEUTRAL  | 0.047 | 51.88 | up        | below_vwap    | low_volume_chop (rvol=0.047 < 0.5)  |
| GOOGL  | 33    | NEUTRAL  | 0.100 | 62.70 | up        | below_vwap    | low_volume_chop (rvol=0.100 < 0.5)  |
| SPY    | 31    | LONG     | 0.047 | 73.20 | up        | at_vwap       | low_volume_chop (rvol=0.047 < 0.5)  |
| QQQ    | 31    | LONG     | 0.166 | 79.29 | up        | at_vwap       | low_volume_chop (rvol=0.166 < 0.5)  |
| TSLA   | 30    | LONG     | 0.072 | 81.20 | up        | at_vwap       | low_volume_chop (rvol=0.072 < 0.5)  |
| META   | 30    | NEUTRAL  | 0.066 | 27.42 | sideways  | at_vwap       | low_volume_chop (rvol=0.066 < 0.5)  |
| IWM    | 26    | NEUTRAL  | 0.088 | 57.96 | up        | below_vwap    | low_volume_chop (rvol=0.088 < 0.5)  |
| DIA    | 26    | NEUTRAL  | 0.144 | 54.93 | up        | below_vwap    | low_volume_chop (rvol=0.144 < 0.5)  |
| NFLX   | 23    | NEUTRAL  | 0.044 | 29.46 | down      | above_vwap    | low_volume_chop (rvol=0.044 < 0.5)  |

**Observations:**
- 10 of 13 symbols had LONG scanner signal or NEUTRAL with up-trend — macro bias was bullish, consistent with MSFT underlying rising +$1.47 during the PUT hold.
- Highest rvol was QQQ at 0.166, still only 33% of the threshold. This is an extreme low-volume opening.
- MSFT was the only sideways-trend symbol with a SHORT signal — a contrarian position in a broadly bullish tape.

---

## 4. Strategy Signal Audit

**Total signals generated: 60** across 13 symbols, 09:30–11:00 ET.

### Summary by Strategy

| Strategy | Signals | Symbols Hit | Most Active Symbol |
|----------|---------|-------------|-------------------|
| ORB      | 6       | SPY, IWM, DIA, AAPL, TSLA, NFLX | AAPL, IWM, SPY |
| VWAP     | 41      | All except IWM, TSLA (no VWAP) | AAPL (9), GOOGL (8) |
| RSI      | 13      | SPY, QQQ, AAPL, TSLA, AMD, META, GOOGL | QQQ (2) |

### ORB Signals (6 total)

| Symbol | Direction | Time  | Price   | Confidence | OR Range | Notes                           | Gate Block |
|--------|-----------|-------|---------|-----------|----------|---------------------------------|------------|
| SPY    | SHORT     | 10:00 | 734.82  | 0.900     | 1.71     | Breakdown below 735.51          | low_vol_chop |
| IWM    | SHORT     | 10:00 | 281.08  | 0.900     | 2.00     | Breakdown below 282.06          | low_vol_chop, low_score |
| DIA    | SHORT     | 09:46 | 493.51  | 0.606     | 3.59     | Breakdown below 493.53          | low_vol_chop, low_score |
| AAPL   | LONG      | 10:16 | 294.33  | 0.900     | 1.24     | Breakout above 293.86           | low_vol_chop |
| TSLA   | SHORT     | 10:32 | 433.73  | 0.900     | 9.39     | Breakdown below 438.41          | low_vol_chop, low_score |
| NFLX   | LONG      | 09:59 | 86.82   | 0.636     | 0.98     | Breakout above 86.79 (narrow)   | low_vol_chop, low_score |

**ORB Notes:** SPY and IWM both broke down at exactly 10:00 ET — a confluence suggesting sector-wide selling pressure at the 10am hour. The AAPL ORB long at 10:16 was the only bullish ORB. NFLX range of 0.98 is near the 0.5 min_range_pts floor; signal quality is marginal.

### VWAP Reclaim/Rejection Signals (41 total — selected highlights)

| Symbol | Direction | Time  | Price   | VWAP Level | Notes                                 | Gate Block |
|--------|-----------|-------|---------|-----------|---------------------------------------|------------|
| SPY    | LONG      | 09:42 | 736.43  | 736.40    | Reclaim — within no-trade buffer      | no_trade_buf |
| SPY    | SHORT     | 09:46 | 736.39  | 736.42    | Rejection — 4 min after buffer ends   | low_vol_chop |
| SPY    | LONG      | 10:14 | 736.11  | 735.97    | Reclaim — coincides with trailing stop | low_vol_chop |
| QQQ    | LONG      | 10:14 | 707.63  | 707.24    | Reclaim — simultaneous with SPY reclaim | low_vol_chop |
| MSFT   | LONG      | 10:14 | 409.23  | 409.10    | Reclaim — bearish for PUT holders     | low_vol_chop |
| NVDA   | LONG      | 09:31 | 217.86  | 217.75    | Early reclaim — no_trade_buffer       | no_trade_buf |
| NVDA   | SHORT     | 09:58 | 220.63  | 221.04    | Rejection after strong morning run    | low_vol_chop |
| AMD    | SHORT     | 10:16 | 452.11  | 452.61    | Rejection — AMD weakening at VWAP     | low_vol_chop |
| AAPL   | LONG/SHORT| 09:31–10:54 | Various | Various | 9 signals — noisy VWAP environment | low_vol_chop |
| GOOGL  | Mixed     | 09:52–10:55 | Various | Various | 8 signals — tight VWAP band chop     | low_vol_chop |

**VWAP Notes:** The VWAP strategy generated 41 signals (68% of total). In a low-rvol environment, price oscillates around VWAP without directional conviction, and the `proximity_pct=0.002` filter is insufficient to separate meaningful reclaims from noise. AAPL's 9 VWAP signals and GOOGL's 8 signals on a choppy day illustrate the signal inflation risk.

### RSI Trend Signals (13 total)

| Symbol | Direction | Time  | Price   | RSI   | EMA    | Confidence | Notes                          | Gate Block |
|--------|-----------|-------|---------|-------|--------|-----------|--------------------------------|------------|
| SPY    | LONG      | 09:53 | 736.33  | 39.2  | 736.29 | 0.638     | RSI bounce from oversold       | low_vol_chop |
| QQQ    | LONG      | 09:34 | 707.86  | 48.8  | 707.09 | 0.643     | RSI=48.8 — marginal oversold  | no_trade_buf |
| QQQ    | LONG      | 09:36 | 707.14  | 39.3  | 707.08 | 0.647     | RSI bounce — warmup only 6 bars | no_trade_buf |
| AAPL   | LONG      | 09:55 | 293.58  | 35.5  | 293.33 | 0.605     | RSI oversold bounce            | low_vol_chop |
| TSLA   | LONG      | 09:37 | 442.63  | 48.9  | 441.36 | 0.625     | RSI=48.9 — not truly oversold  | no_trade_buf |
| AMD    | SHORT     | 09:56 | 452.16  | 61.8  | 453.07 | 0.614     | RSI overbought fade (bearish bias) | low_vol_chop |
| META   | LONG      | 09:39 | 596.38  | 51.1  | 594.27 | 0.604     | RSI barely above midline       | no_trade_buf |
| GOOGL  | LONG      | 09:53 | 388.20  | 35.2  | 387.26 | 0.671     | Strong oversold reading        | low_vol_chop |

**RSI Notes:** The RSI(14) on 1m bars requires ~14 bars of warmup. Signals before 09:44 (QQQ at 09:34, 09:36; TSLA at 09:37; META at 09:39) are based on fewer than 14 bars and should be considered unreliable. QQQ's RSI=48.8 "bounce" signal does not meet the rsi_oversold=35 threshold by any standard; the EWM initialization is causing artificial readings.

---

## 5. Liquidity Audit

**Data source:** RESEARCH DATA ONLY — yfinance EOD snapshot, not open-of-session prices.  
**Symbols:** MSFT, NVDA, AMD, AAPL, SPY, QQQ, TSLA (signal symbols only).  
**Expiry:** 2026-05-13 (or nearest available).

### MSFT Options (May-13 expiry) — *Trade symbol*

| Strike | Type | Label | Bid   | Ask   | Spread% | Volume | OI    | Delta Est | Liquidity |
|--------|------|-------|-------|-------|---------|--------|-------|-----------|-----------|
| 407.50 | CALL | ITM   | 3.50  | 3.65  | 4.2%    | 4,231  | 390   | +0.626    | GOOD      |
| 410.00 | CALL | ATM   | 2.36  | 2.40  | 1.7%    | 11,083 | 783   | +0.473    | GOOD      |
| 412.50 | CALL | OTM   | 1.42  | 1.53  | 7.5%    | 8,117  | 1,179 | +0.321    | FAIR      |
| 412.50 | **PUT**  | **ITM**   | **5.05**  | **5.50**  | **8.5%**    | 512    | 404   | -0.321    | **FAIR** ⚠ |
| 410.00 | PUT  | ATM   | 3.55  | 3.85  | 8.1%    | 1,820  | 1,110 | -0.473    | FAIR      |
| 407.50 | PUT  | OTM   | 2.37  | 2.47  | 4.1%    | 4,895  | 1,779 | -0.626    | GOOD      |

**Critical finding:** The MSFT PUT used in today's trade had an exit spread of 16.42% at 10:14. Current EOD spreads (8.1–8.5%) are already at the boundary of the 10% risk-manager limit. During the session, MSFT PUT liquidity was clearly degraded vs. the call side, and the spread widened substantially as MSFT reversed upward.

### NVDA Options (May-13) — *Highest-quality liquidity in universe*

| Strike | Type | Label | Bid   | Ask   | Spread% | Volume  | OI     | Delta Est | Liquidity |
|--------|------|-------|-------|-------|---------|---------|--------|-----------|-----------|
| 215.00 | CALL | ITM   | 3.70  | 3.80  | 2.7%    | 14,534  | 8,579  | +0.785    | GOOD      |
| 217.50 | CALL | ATM   | 2.27  | 2.30  | 1.3%    | 39,679  | 6,733  | +0.498    | GOOD      |
| 220.00 | CALL | OTM   | 1.29  | 1.31  | 1.5%    | 147,059 | 23,291 | +0.210    | GOOD      |
| 220.00 | PUT  | ITM   | 3.95  | 4.05  | 2.5%    | 54,994  | 4,065  | -0.210    | GOOD      |
| 217.50 | PUT  | ATM   | 2.44  | 2.46  | 0.8%    | 50,359  | 4,879  | -0.498    | GOOD      |
| 215.00 | PUT  | OTM   | 1.44  | 1.45  | 0.7%    | 78,969  | 6,484  | GOOD      | GOOD      |

NVDA has the deepest liquidity in the universe — sub-1% spreads on ATM/OTM puts, volumes exceeding 50,000 contracts. If rvol were sufficient, NVDA would be the preferred trading vehicle.

### AMD Options (May-15 expiry — no May-13 available)

| Strike | Type | Label | Bid   | Ask   | Spread% | Volume | OI    | Delta Est | Liquidity |
|--------|------|-------|-------|-------|---------|--------|-------|-----------|-----------|
| 430.00 | CALL | ITM   | 14.90 | 15.65 | 4.9%    | 198    | 6,237 | +0.686    | GOOD      |
| 432.50 | CALL | ATM   | 13.70 | 14.30 | 4.3%    | 58     | 393   | +0.542    | GOOD      |
| 435.00 | CALL | OTM   | 12.50 | 12.85 | 2.8%    | 321    | 1,858 | +0.397    | GOOD      |
| 435.00 | PUT  | ITM   | 12.50 | 12.95 | 3.5%    | 2,267  | 2,177 | -0.397    | GOOD      |
| 432.50 | PUT  | ATM   | 11.45 | 11.80 | 3.0%    | 806    | 751   | -0.542    | GOOD      |
| 430.00 | PUT  | OTM   | 10.30 | 10.65 | 3.3%    | 4,296  | 2,458 | -0.686    | GOOD      |

AMD May-15 options have high premiums (high-priced stock) but reasonable spreads. No May-13 expiry available — AMD runs on weekly Thursdays only.

### SPY Options (May-13) — *Best overall liquidity*

| Strike | Type | Label | Bid  | Ask  | Spread% | Volume | OI    | Delta Est | Liquidity |
|--------|------|-------|------|------|---------|--------|-------|-----------|-----------|
| 732.00 | CALL | ITM   | 3.06 | 3.10 | 1.3%    | 3,254  | 1,048 | +0.529    | GOOD      |
| 733.00 | CALL | ATM   | 2.43 | 2.44 | 0.4%    | 12,285 | 621   | +0.495    | GOOD      |
| 734.00 | CALL | OTM   | 1.90 | 1.91 | 0.5%    | 17,041 | 625   | +0.460    | GOOD      |
| 734.00 | PUT  | ITM   | 2.72 | 2.75 | 1.1%    | 36,277 | 1,871 | -0.460    | GOOD      |
| 733.00 | PUT  | ATM   | 2.34 | 2.35 | 0.4%    | 33,787 | 1,785 | -0.495    | GOOD      |
| 732.00 | PUT  | OTM   | 1.90 | 1.92 | 1.0%    | 30,026 | 1,430 | -0.529    | GOOD      |

SPY has the tightest spreads (0.4% ATM) and excellent volume. Even on a low-rvol day, SPY options maintain institutional-quality liquidity.

### QQQ and TSLA Options (May-13) — Summary

| Symbol | Best Spread | Worst Spread | Max Volume  | Liquidity Grade |
|--------|-------------|--------------|-------------|-----------------|
| QQQ    | 0.3% (OTM)  | 0.9% (ATM C) | 37,308      | EXCELLENT       |
| TSLA   | 0.7% (ITM)  | 1.3% (ATM P) | 44,054      | EXCELLENT       |

---

## 6. Exit Logic Comparison

**Trade:** MSFT PUT, fill 09:46:44 ET @ $6.15 option / $407.77 underlying.  
**Peak:** 09:48, option=$6.74 (+$59 MFE). Actual exit: 10:14:52, trailing stop 25%, exit=$5.055, PnL=-$109.50.

| # | Exit Method               | Trail/Threshold | Exit Time  | Exit Price | PnL ($) | PnL (%) | Hold (min) | Trigger            |
|---|--------------------------|-----------------|------------|------------|---------|---------|------------|--------------------|
| 1 | **Actual: 25% trail**    | peak×0.75=$5.055 | 10:14:52  | $5.055     | **-$109.50** | -17.8% | 28.1 | trailing_stop (actual) |
| 2 | Tighter 15% trail        | peak×0.85=$5.729 | ~10:00    | $5.73      | -$42.00 | -6.8%  | 13.3  | trailing_stop_15pct    |
| 3 | Tighter 20% trail        | peak×0.80=$5.392 | ~10:10    | $5.39      | -$76.00 | -12.4% | 23.3  | trailing_stop_20pct    |
| 4 | **MFE capture +$40**     | unrealized≥$40   | 09:47     | $6.55      | **+$40.00** | +6.5% | 0.3  | mfe_capture_threshold  |
| 5 | Macro: SPY+QQQ VWAP reclaim | both reclaim | 10:14:00  | $5.055     | -$109.50 | -17.8% | 27.3 | dual_etf_vwap_reclaim  |
| 6 | Max-hold 15 min          | time limit       | 10:01:44  | $5.95      | -$20.00 | -3.3%  | 15.0  | max_hold_15min         |
| 7 | **Max-hold 10 min**      | time limit       | 09:56:44  | $6.05      | **-$10.00** | -1.6% | 10.0 | max_hold_10min         |
| 8 | MSFT VWAP reclaim        | underlying VWAP  | 10:16:00  | $5.055     | -$109.50 | -17.8% | 29.3 | underlying_vwap_reclaim |

### Option Price Timeline (underlying / option)

```
09:46:44  $407.77 / $6.15  [FILL]
09:47     $406.82 / $6.65
09:48     $406.67 / $6.74  [PEAK — MFE=$59]
09:49     $407.49 / $6.40
09:50     $407.89 / $6.20
09:55     $407.91 / $6.05  [15% trail breach would occur between 09:55-10:00]
10:00     $407.86 / $5.95  [10-min hold exit]
10:05     $408.42 / $5.75
10:10     $408.37 / $5.78  [20% trail breach near here]
10:14     $409.24 / $5.055 [ACTUAL EXIT — trailing stop + SPY/QQQ VWAP reclaim]
10:15     $409.67 / —
```

SPY VWAP context: below VWAP 09:46–10:13, reclaimed 10:14  
QQQ VWAP context: below VWAP 09:55–10:13, reclaimed 10:14

### Analysis

The underlying (MSFT) moved **against** the PUT from entry: +$1.47 (+0.36%) over the 28-minute hold. The option peaked in the first 2 minutes as MSFT dipped to $406.67, then was eroded by a combination of **adverse delta** (MSFT rising) and **time decay** (0 DTE option near expiry). The 16.42% exit spread at 10:14 implies fill was near the bid ($4.64), not mid ($5.055).

Time-based exits (Scenarios 6 and 7) dramatically outperform the 25% trailing stop on this trade. Scenario 4 (MFE capture) is theoretically optimal but requires a profit-target rule not currently implemented.

---

## 7. Observations

1. **Universe-wide chop is rare and dangerous:** All 13 symbols had rvol below 0.20 at session start. The max rvol (QQQ=0.166) was only 33% of the scanner threshold. This is a "standby" session, not a "reduced-quality" session.

2. **60 strategy signals, 0 would have survived scanner gates:** Every signal was blocked by `low_volume_chop`. The strategies themselves are generating signals that are qualitatively consistent with actual market moves (e.g., SPY/IWM ORB short at 10:00 was directionally correct), but the rvol gate is correctly suppressing them.

3. **VWAP strategy signal inflation:** 41 of 60 signals (68%) came from the VWAP strategy. In low-rvol choppy tape, the `proximity_pct=0.002` filter is too permissive — price oscillates through VWAP without directional follow-through. AAPL produced 9 VWAP signals and GOOGL produced 8 in 90 minutes.

4. **MSFT PUT thesis was wrong at entry:** The scanner tagged MSFT as SHORT and sideways, but the macro environment (10 of 13 symbols trending UP, most at or above VWAP) argued for a bullish bias. The CLI fallback selected the contrarian signal without checking macro alignment.

5. **Exit spread widened 64% above limit:** The 16.42% exit spread on the MSFT PUT exceeded the risk manager's `max_spread_pct=0.10` by 64%. The spread is not re-checked at exit. This is a code path gap.

6. **SPY and QQQ VWAP reclaim at 10:14 was predictable:** Both generated VWAP reclaim signals at exactly 10:14 ET. A macro-exit rule triggered by simultaneous dual-ETF VWAP reclaim would have produced the same PnL on this trade but would be beneficial in scenarios where the PUT is still above the trailing stop level.

7. **RSI warmup flaw on 1m bars:** RSI(14) requires 14 bars. Signals before 09:44 are unreliable. QQQ fired at 09:34 with RSI=48.8 — above the 35 oversold threshold; the EWM alpha is producing inflated readings during warmup.

8. **ORB at 10:00 was correct in direction:** SPY, IWM, and DIA all broke ORB range to the downside at 10:00. But ORB signals require `volume > avg_vol` — in a low-rvol environment, most breakouts were volume-confirmed relative to a low baseline, not relative to normal session volume.

9. **MSFT underlying rose +$1.47 during PUT hold:** The position was not just wrong directionally — it was wrong in an environment where the macro bias (10/13 symbols up, SPY at VWAP) argued for upside continuation. The CLI fallback selects the best-score candidate but does not verify macro alignment.

10. **AMD earliest available expiry is May-15 (not May-13):** AMD runs on weekly Thursday-only expirations. Any AMD options trade requires overnight or 3-day hold risk, which is outside the paper system's 0-DTE/1-DTE preference.

---

## 8. Hypotheses

**H1 — Universe rvol as spread predictor:**  
Universe-wide rvol < 0.20 is a reliable predictor of elevated option spreads (>10%) and should gate all trading. Testable: correlate `max(rvol_universe_at_open)` with `avg_exit_spread_pct` across the trade ledger. Expected Pearson r < -0.6.

**H2 — VWAP proximity_pct too tight:**  
The VWAP strategy's `proximity_pct=0.002` generates excessive false signals in low-rvol environments. Raising to 0.005 would reduce VWAP signal count by an estimated 60-70% on days like today (from 41 to ~12-15). Not recommended to change without backtesting on 30+ sessions.

**H3 — Time exits dominate trailing stops on 0DTE puts in chop:**  
In low-rvol sessions, time decay is the dominant force on short-dated puts. A 10-min or 15-min time exit outperforms a 25% trailing stop because the trailing stop is anchored to a 2-minute peak that is rarely revisited. This hypothesis requires 20+ trades to validate statistically.

**H4 — Exit spread re-check is a missing gate:**  
Adding a spread_pct re-check at exit time would surface degraded liquidity before order submission. If `exit_spread_pct > 1.5 × max_spread_pct`, the system should log a WARNING and consider adjusting the limit price aggressively (marketable limit). This is a code improvement, not a parameter change.

**H5 — RSI 1m bars need warmup suppression:**  
RSI(14) on 1m bars should suppress signals before bar index 14 (i.e., before 09:44 ET on session open). A trivial guard `if bars.index.get_loc(idx[i]) < self._rsi_period: continue` would eliminate 5 of the 13 RSI signals today.

**H6 — Dual-ETF VWAP reclaim as formal exit rule:**  
When both SPY and QQQ simultaneously reclaim VWAP, all short-delta positions (puts) should be closed immediately. This would have produced the same outcome today (10:14 exit) but would outperform trailing stops in cases where the macro reversal occurs before the trailing stop is hit. Implement as a conditional check in the exit loop alongside trailing stop.

---

## 9. Tomorrow's Recommended Settings (Watch List Only)

**No parameter changes recommended.** This is a single-session observation list. The following are conditions to MONITOR, not thresholds to change.

- **rvol check at 09:45:** If `max(rvol_universe) < 0.20`, enter STANDBY — no CLI fallback trades.
- **Macro alignment before MSFT short:** Verify SPY and QQQ are both below VWAP before taking a bearish individual-name position.
- **MSFT PUT spread at entry:** Confirm MSFT PUT spread < 8% (current EOD is 8.1–8.5%; at open it may be wider).
- **NVDA and AMD rvol:** These are the highest-quality candidates when active. If `rvol(NVDA) > 0.30` by 09:45, NVDA is the preferred vehicle over MSFT for options with NVDA's tighter spreads.
- **SPY/QQQ VWAP stability:** If SPY crosses VWAP more than 3 times in the first 30 minutes, skip directional intraday trades — it signals the same choppy environment as today.
- **First 30-min volume arrival:** Normal sessions see rvol accelerate from 0.5→1.5 in the first 30 min. If rvol is still below 0.3 at 10:00, the session is structurally slow and options liquidity will be degraded all day.

---

## 10. Overfitting Warning

This diagnostic covers a **single session (2026-05-12) with one executed trade (MSFT PUT)**. All exit scenario comparisons and strategy signal analyses are backward-looking with full hindsight.

The "best" exit scenario (Scenario 4: MFE capture at +$40) requires knowing the exact peak price in advance. It is not a realizable trading rule without a separate profit-target mechanism. Even with such a mechanism, the threshold ($40) is calibrated to this specific trade's peak, not a generalizable rule.

The universe-wide low-rvol observation may be a one-day anomaly caused by macro calm (e.g., no catalysts, narrow pre-market range) and may not repeat.

**DO NOT adjust strategy parameters, trailing stop percentages, scanner thresholds, or min_score values based on this single session.** A minimum of 20-30 trade samples under similar conditions is required before any parameter optimization is statistically meaningful. Premature optimization based on a single loss will introduce selection bias and reduce out-of-sample performance.

---

*End of Report — 2026-05-12 Post-Session Diagnostics*  
*Generated by: post_session_diagnostics.py | Mode: READ-ONLY | No broker calls | No trades placed*
