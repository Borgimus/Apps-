# Historical Signal Analysis

## Claims Registry

> **Data source:** 2026-05-11 – 2026-05-29 — pre-phase3, Bug A/B active, FillTracker defects active  
> **All claims in this document carry `[CONTAMINATED SOURCE]`**  
> See `research/epistemic_standards.md` for category definitions.

| # | Claim summary | Tag | Contaminated |
|---|---------------|-----|--------------|
| 1 | ORB: 0 trades executed; 103 evaluations all blocked/skipped because risk limit was exhausted by VWAP trades | `OBSERVED` / `INFERRED` | yes |
| 2 | RSI_trend: all 639 evaluations skipped (rsi_trend_diagnostic_only mode; permanently non-executable) | `OBSERVED` | yes |
| 3 | Quality ≥2 signals reached execution; quality=1 signals never evaluated (risk limit already exhausted) | `INFERRED` | yes |
| 4 | Exit spreads consistently 1.3–3.5× wider than entry spreads across all adverse-exit trades | `DERIVED` | yes |
| 5 | The trade counter bug (exits consuming entry slots) caused all ORB blocks — not signal quality | `INFERRED` | yes |
| 6 | Effective entries per session = ⌊max_trades_per_day / 2⌋ under the buggy counter semantics | `DERIVED` | yes |
| 7 | All executed trades had sub-normal volume (rvol < 1.0); reduces generalizability of staleness findings | `OBSERVED` / `INFERRED` | yes |



*Generated 2026-06-01T15:10:38Z UTC  |  Research-only — no parameter changes recommended*

---

## Scope and Critical Limitations

| Item | Detail |
|---|---|
| Sessions analysed | 2026-05-11, 2026-05-12, 2026-05-23, 2026-05-25, 2026-05-26, 2026-05-29 |
| Sessions with bridge diagnostics | 2026-05-29 |
| Total bridge rows (signal-level evaluations) | 852 |
| Real strategy executions (all sessions) | 3 |

> **Warning:** Bridge diagnostics (quality scores, per-gate pass/fail, scanner scores per signal) only
> exist for 2026-05-29 — the first session with PAPER_EVAL_PERMISSIVE_ENTRY_MODE active.
> Earlier sessions produced trade_journal records but no per-signal diagnostic data.
> ORB `orb_fwd_*` forward-performance columns are NULL for all 2026-05-29 rows because
> `underlying_price_at_signal` was added after the session ran.
> **Total real strategy executions: N=3.** All statistical claims in this report are descriptive only.

---

## Session History

| Date | Bridge Active | Trades | PnL | Notes |
|---|:---:|:---:|---:|---|
| 2026-05-11 | NO | 1 | $+2.00 | fill_lifecycle_test only; no real strategy activity |
| 2026-05-12 | NO | 1 | $-109.50 | RSI_trend 1 trade (MSFT short); bridge not yet active |
| 2026-05-23 | NO | 0 | $+0.00 | Scanner standby; no executions |
| 2026-05-25 | NO | 0 | $+0.00 | Scanner standby; no executions |
| 2026-05-26 | NO | 0 | $+0.00 | Scanner standby; SNOW briefly passed, no execution |
| 2026-05-29 | YES | 2 | $-40.50 | First PAPER_EVAL_PERMISSIVE_ENTRY_MODE session; 2 VWAP trades |

**Total across all sessions:** 4 fills (3 real strategy trades + 1 fill_lifecycle_test)
**Total realized PnL:** $-147.00 (real strategies: $-149.00)

---

## Signal Statistics

*Source: signal_bridge table, 2026-05-29 only (only session with diagnostics active)*

| Strategy | Total Evaluations | Traded | Blocked | Skipped | Trade Rate |
|---|---:|---:|---:|---:|---:|
| orb | 103 | 0 | 77 | 26 | 0.0% |
| rsi_trend | 639 | 0 | 0 | 639 | 0.0% |
| vwap_reclaim | 110 | 2 | 78 | 30 | 1.8% |

**Notes:**
- **RSI_trend:** All 639 evaluations skipped as `rsi_trend_diagnostic_only`. This strategy is permanently non-executable in current mode.
- **ORB:** 0 trades executed. 77 blocked by risk limit, 26 skipped by cooldown. Risk limit exhausted by VWAP trades before any ORB execution window.
- **VWAP:** 2 trades from 110 evaluations. 78 blocked by risk limit after trades hit.

---

## Quality Score Analysis

### Distribution

| Strategy | Quality 0 | Quality 1 | Quality 2 | Quality 3 | Quality 4 |
|---|---:|---:|---:|---:|---:|
| orb | — | — | — | 103 | — |
| rsi_trend | 639 | — | — | — | — |
| vwap_reclaim | — | 59 | 50 (1T) | 1 (1T) | — |

*(T) = evaluations that resulted in a trade*

### Quality Score vs Outcome — VWAP (only strategy with executed trades and quality variation)

| Quality | Total Evals | Traded | Blocked | Skipped | Actual Outcomes |
|---|---:|---:|---:|---:|---|
| 1/4 | 59 | 0 | 29 | 30 | DIA (score=53): 0 traded — all blocked/skipped after risk limit |
| 2/4 | 50 | 1 | 49 | 0 | MSFT (score=60): 1 trade → -$9.00 (trailing_stop, hold 62s) |
| 3/4 | 1 | 1 | 0 | 0 | SNOW (score=60): 1 trade → -$31.50 (trailing_stop, hold 91s) |

> **Finding:** Both executed trades had quality ≥ 2 vs 0 trades at quality 1. However:
> (1) quality=1 signals were never given a chance — they arrived after the risk limit was exhausted.
> (2) Both quality=2 and quality=3 executions resulted in losses.
> (3) N=2 is insufficient to draw any conclusion about quality score predictive value.

### ORB Quality Score vs Forward Performance

> **Not available.** All 103 ORB signals have quality=3. `underlying_price_at_signal` was NULL
> for all rows (column added after the session ran). `orb_fwd_*` columns are entirely NULL.
> First usable ORB forward performance data will come from the next session (2026-06-02+).

---

## Scanner Score Analysis

### Score Distribution in Bridge (2026-05-29)

| Strategy | Score 0-39 | Score 40-59 | Score 60-79 | Score 80+ |
|---|---:|---:|---:|---:|
| orb | 0 | 54 | 49 | 0 |
| rsi_trend | 0 | 537 | 102 | 0 |
| vwap_reclaim | 0 | 59 | 51 | 0 |

*No signals in 0-39 or 80+ buckets. All activity concentrated in 40-79 range.*

### Scanner Score vs Forward Returns

| Strategy | Bucket | N Signals | N with Fwd Data | Avg +5m | Avg +15m | Avg +30m |
|---|---|---:|---:|---:|---:|---:|
| orb | 40-59 | 54 | 0 | n/a | n/a | n/a |
| orb | 60-79 | 49 | 0 | n/a | n/a | n/a |
| rsi_trend | 40-59 | 537 | 0 | n/a | n/a | n/a |
| rsi_trend | 60-79 | 102 | 0 | n/a | n/a | n/a |
| vwap_reclaim | 40-59 | 59 | 0 | n/a | n/a | n/a |
| vwap_reclaim | 60-79 | 51 | 0 | n/a | n/a | n/a |

> **Finding:** No forward performance data exists for any strategy in any score bucket.
> ORB `orb_fwd_*` columns require `underlying_price_at_signal` which was NULL for all rows.
> VWAP and RSI_trend have no forward performance columns at all.
> Scanner score vs forward return correlation cannot be computed from current data.

### Scanner Score of Executed VWAP Trades

| Trade | Symbol | Scanner Score | Quality | Outcome |
|---|---|---:|---:|---|
| 1 (10:34 AM) | SNOW | 60 | 3/4 | -$31.50 (trailing_stop, 91s hold) |
| 2 (11:04 AM) | MSFT | 60 | 2/4 | -$9.00 (trailing_stop, 62s hold) |
| Blocked DIA | DIA | 53 | 1/4 | Risk limit exhausted — never traded |

> Score 60 vs 53 in a sample of N=2 executions provides no basis for predicting outcomes.

---

## Execution Analysis

### All Executed Trades (real strategies only)

| Date | Strategy | Symbol | Dir | Fill | Exit | PnL | Fill Latency | Entry Spread | Exit Spread | Hold | Exit Reason |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-05-12 | rsi_trend | MSFT | short | 6.15 | 5.055 | $-109.50 | 63s | 9.3% | 16.4% | 1688s | trailing_stop |
| 2026-05-29 | vwap_reclaim | SNOW | long | 1.04 | 0.725 | $-31.50 | 95s | 8.3% | 29.0% | 91s | trailing_stop |
| 2026-05-29 | vwap_reclaim | MSFT | long | 0.26 | 0.17 | $-9.00 | 34s | 8.0% | 11.8% | 62s | trailing_stop |

### Per-Strategy Execution Summary

| Strategy | N | Wins | Losses | Win Rate | Total PnL | Avg PnL | Avg Fill Latency | Avg Entry Spread | Avg Exit Spread | Avg Hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb | 0 | — | — | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| rsi_trend | 1 | 0 | 1 | 0% | $-109.50 | $-109.50 | 63s | 9.2% | 16.4% | 1688s |
| vwap_reclaim | 2 | 0 | 2 | 0% | $-40.50 | $-20.25 | 64s | 8.2% | 20.4% | 77s |

### MAE / MFE

> **Not available from stored data.** The trade_journal stores entry fill price and exit price
> but does not record intraday high/low watermarks. MAE/MFE cannot be derived from current schema.
> Observations from exit reasons: all 3 real strategy trades exited via `trailing_stop`,
> indicating the stop was hit before the target, implying MFE < entry price for all.

### Spread Observations

| Trade | Entry Spread | Exit Spread | Spread Widening |
|---|---:|---:|---:|
| MSFT (rsi_trend) | 9.3% | 16.4% | +7.2pp |
| SNOW (vwap_reclaim) | 8.3% | 29.0% | +20.7pp |
| MSFT (vwap_reclaim) | 8.0% | 11.8% | +3.8pp |

> **Finding:** Exit spreads are substantially wider than entry spreads across all trades.
> SNOW exit spread (29.0%) was 3.5x entry spread (8.3%). MSFT (vwap) exit spread (11.8%) vs 8.0% entry.
> RSI_trend MSFT exit (16.4%) vs entry (9.3%). This is consistent with options widening during adverse moves.

---

## Gate Analysis

### Block Reasons by Strategy

| Strategy | Block Reason | Count | % of Blocks |
|---|---|---:|---:|
| orb | `risk: Max trades per day reached: 4/3` | 75 | 97% |
| orb | `risk: Max trades per day reached: 3/3` | 2 | 3% |
| rsi_trend | *(no blocks)* | — | — |
| vwap_reclaim | `risk: Max trades per day reached: 4/3` | 74 | 95% |
| vwap_reclaim | `risk: Max trades per day reached: 3/3` | 3 | 4% |
| vwap_reclaim | `risk: Max trades per day reached: 4/3; Trade cost $3066.00 exceeds max risk $998` | 1 | 1% |

### Skip Reasons by Strategy

| Strategy | Skip Reason | Count | % of Skipped |
|---|---|---:|---:|
| orb | `cooldown_after_loss` | 26 | 100% |
| rsi_trend | `rsi_trend_diagnostic_only` | 639 | 100% |
| vwap_reclaim | `cooldown_after_loss` | 30 | 100% |

### Missed Opportunity Analysis

*Signals that passed liquidity and spread gates but were blocked by risk limit only:*

| Strategy | Total Blocked | Blocked by Risk Only | Risk-Block Rate |
|---|---:|---:|---:|
| orb | 77 | 76 | 99% |
| rsi_trend | 0 | 0 | n/a |
| vwap_reclaim | 78 | 78 | 100% |

**Sequence of events (2026-05-29) explaining all blocks:**

1. **10:34 AM** — SNOW VWAP traded (risk counter: 1 entry, 0 exits)
2. **~10:35–11:03 AM** — SNOW exit via trailing_stop (risk counter via record_trade calls)
3. **11:04 AM** — MSFT VWAP traded; simultaneously DIA VWAP and DIA ORB signals blocked ("3/3 reached")
4. **11:04 AM onward** — All remaining signals (DIA, ORB, additional VWAP) blocked as "4/3 reached"
5. **12:00–12:30 PM** — MSTR ORB signals appear and are immediately blocked

> **Key structural finding:** The `risk.record_trade()` method counts both entries AND exits.
> With max_trades=3: SNOW entry(1) + SNOW exit(2) + MSFT entry(3) = limit reached.
> MSFT exit pushes counter to 4/3, which is why 75 blocks show "4/3" not "3/3".
> This means only ~1.5 round-trip trades are possible per session with max_trades_per_day=3.
> Every ORB signal (103 total) arrived AFTER the risk limit was already saturated.

### ORB Slot Reservation (new feature — not active on 2026-05-29)

> The ORB slot reservation feature (commits 79d65c2) was implemented AFTER the 2026-05-29 session.
> It was not active during data collection. Future sessions will test whether reserving a slot
> before 11:30 ET allows at least one ORB execution.

---

## Predictive Value Assessment

### Quality Score

| Strategy | Verdict | Evidence |
|---|---|---|
| orb | **INDETERMINATE** | All 103 ORB bridge rows have quality_score=3. No variation exists — cannot test whether score predicts outcomes. Additionally, 0 ORB trades were executed, so no actual outcome data. |
| rsi_trend | **NOT APPLICABLE** | RSI_trend is permanently in diagnostic-only mode. All 639 rows have quality_score=0 and final_decision=skipped. No execution data exists. |
| vwap_reclaim | **INDETERMINATE** | Quality distribution: 1→59 rows (0 traded), 2→50 rows (1 traded), 3→1 row (1 traded). Both traded signals had quality >= 2. However N=2 executions is insufficient to assess predictive value. Both resulted in losses. |

### Scanner Score

| Strategy | Verdict | Evidence |
|---|---|---|
| orb | **INDETERMINATE** | Scores range 40-79 (no 0-39 or 80+ data). No forward performance data (orb_fwd_* NULL for all rows). No executions to compare outcomes against. |
| rsi_trend | **NOT APPLICABLE** | No executions. All signals skipped as diagnostic_only. |
| vwap_reclaim | **INDETERMINATE** | Both executed trades had scanner_score=60. Blocked DIA signals had scanner_score=53. Higher score (60 vs 53) did not predict better outcome — both executed trades lost. N=2 is not sufficient to assess predictive value. |

---

## What Additional Data Would Enable Predictive Analysis

| Requirement | Status | Path to Resolution |
|---|---|---|
| ORB executions (minimum ~10) | 0 executed | ORB slot reservation active from 2026-06-02+ |
| `underlying_price_at_signal` populated | NULL for all rows | Auto-populated from next session |
| `orb_fwd_*` columns populated | NULL for all rows | Will fill post-session via compute_orb_forward_performance() |
| VWAP quality score variation with outcomes | 1 trade per level, both losses | Needs 10+ trades per quality level |
| Scanner score range 0-39 and 80+ | Not observed | Market/scanner dependent |
| MAE/MFE data | Not in schema | Requires intraday bar replay or explicit watermark tracking |
| RSI_trend executions | 1 trade (2026-05-12, pre-bridge) | RSI_trend diagnostic-only — no future data without mode change |

---

## Summary

Across 6 sessions and 852 bridge evaluations:

- **3 real strategy trades** were executed (RSI_trend: 1, VWAP: 2, ORB: 0). All resulted in losses.
- **Total PnL:** -$149.00 across real strategies (-$40.50 in 2026-05-29 VWAP, -$109.50 in 2026-05-12 RSI_trend).
- **Primary block driver:** Risk limit exhaustion. 100% of blocked signals were blocked by max_trades_per_day.
  The counter semantics (entries + exits counted equally) limit usable capacity to ~1.5 round-trips per day.
- **Quality score:** Cannot assess predictive value. ORB has zero variation (all=3). VWAP has N=2 executions.
- **Scanner score:** Cannot assess predictive value. All scores in 40-79 range. No forward performance data.
- **ORB:** Never executed in any session. 103 bridge evaluations all blocked or skipped.
  First execution opportunity requires ORB slot reservation to be active (implemented, not yet run).
- **RSI_trend:** Diagnostic-only. One historical execution (2026-05-12, pre-bridge) lost $109.50.
- **Spread widening:** Consistent across all trades — exit spreads 1.3–3.5x wider than entry spreads.
  All exits triggered by trailing_stop (adverse moves), not take-profit.

**Minimum sessions required before predictive analysis is possible:**
Approximately 5–10 more sessions with ORB slot reservation active, assuming 1–2 ORB executions per session,
to accumulate sufficient quality/score variation for meaningful cross-group comparison.
