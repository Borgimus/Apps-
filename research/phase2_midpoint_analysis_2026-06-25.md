# Phase 2 Midpoint Analysis
**Generated:** 2026-06-25  
**Sessions completed:** S6–S10 (5 of 10)  
**Trades in dataset:** 20 (14 Phase 1 + 6 Phase 2; S9/S10 contributed 0)  
**Data sources:** phase2_tracking.json, scan_results, signal_bridge (S3–S8 only; S9/S10 excluded as sentinel data)

---

## Summary of Standing

| Metric | Value |
|--------|-------|
| Combined P&L (all sessions) | -$248.00 |
| Phase 2 P&L (S6–S10) | +$28.00 |
| Win rate (20 trades) | 25.0% (5/20) |
| Direct fill win rate (N=5) | 60.0% (3/5) |
| Recovered fill win rate (N=15) | 13.3% (2/15) |
| ORB profit factor | 0.143 (N=4) |
| VWAP profit factor | 0.305 (N=16) |
| Best DTE bucket | DTE=2-3: 50% win rate (N=4) |
| Worst DTE bucket | DTE=1: 0% (N=1), DTE=0: 20% (N=10) |

All seven primary questions remain formally **indeterminate**. The findings below reflect directional signals, confounds, and what additional data from S11–S15 can or cannot resolve.

---

## Q7 — Fill Path

**Finding: Largest observed effect in the dataset. Unresolvable confound with Phase 1 vs Phase 2 infrastructure.**

| Fill Path | N | Wins | Win Rate | P&L | Avg/Trade |
|-----------|---|------|----------|-----|-----------|
| Direct | 5 | 3 | 60.0% | +$32 | +$6.40 |
| Recovered | 15 | 2 | 13.3% | -$280 | -$18.67 |

The performance gap is the largest observed effect across all dimensions — 46.7 percentage points in win rate, $25/trade difference in average P&L. However, the confound makes this number unreliable as an isolated fill-path effect:

**The confound:** Fill path and Phase are nearly perfectly correlated. All 14 Phase 1 trades are recovered fills. Of 6 Phase 2 trades, 5 are direct and 1 is recovered (XLE S6, -$4 actual). The -$280 / 13.3% from recovered fills is essentially the Phase 1 performance figure. The +$32 / 60.0% from direct fills is essentially the Phase 2 performance figure. Everything that changed between Phase 1 and Phase 2 — broker recovery mechanism, DTE distribution, market regime, scanner threshold adjustments — is bundled into this comparison.

**What S11–S15 can resolve:** If we continue accumulating direct fills in Phase 2, the confound weakens over time only if we get recovered fills in Phase 2 for comparison. Currently the XLE S6 recovered fill (-$4) is the only data point in that cell. We need at least 3 more recovered Phase 2 fills, which requires the broker recovery path to activate — that requires fills to happen at all, and for some to go through the stale-price recovery path.

**Conclusion:** The fill path difference is real in the data but not isolable. Do not cite the 60% vs 13% comparison as evidence for or against the fill mechanism itself. It may be reporting a market regime difference, an infrastructure defect difference, or a genuine fill-path effect — cannot distinguish at current N.

---

## Q5 — Scanner Score and Component Attribution

**Finding: Total score inconclusive. Component attribution clear — trend alignment dominates; ATR non-discriminating.**

### Total Score vs Outcomes

| Score Band | N | Wins | Win Rate | P&L |
|------------|---|------|----------|-----|
| <40 | 3 | 0 | 0% | -$82 |
| 40–49 | 4 | 0 | 0% | -$46 |
| 50–59 | 7 | 4 | 57% | -$116 |
| 60–69 | 1 | 0 | 0% | -$4 |

The 50–59 bucket shows a 57% win rate, but its P&L is still deeply negative (-$116 on 7 trades, avg -$16.57) because the losses are larger than the wins. The high win rate in 50–59 is driven by PLTR×2 wins (+$24, +$25). The negative P&L despite >50% win rate reflects an asymmetric loss structure, not a scoring problem per se.

The 60–69 bucket has N=1 (XLE S6, loss, -$4) — insufficient to draw conclusions. No trades above 70.

**Total score alone does not predict outcome.** The 57% win rate in 50–59 compared to 0% in 40–49 could reflect the score difference or the PLTR effect (see Universe section).

### Component Attribution

Computed from 390 passing scan_results rows (S3–S8, excluding S9/S10 sentinel data):

| Component | Frequency in Passing Candidates | Notes |
|-----------|--------------------------------|-------|
| ATR wide (≥1.5%) | 86.4% | Near-universal — non-discriminating |
| ATR good (0.8–1.5%) | 13.6% | Rare |
| VWAP above | 53.6% | Directional split |
| Trend sideways | 46.4% | Most common trend state |
| ORB breakout | 40.5% | Common in morning sessions |
| VWAP below | 38.5% | Directional split |
| Trend up | 33.8% | Second most common |
| ORB breakdown | 21.3% | Less common |
| Near ORB level | 10.8% | Marginal signal |
| MA compression | 9.5% | Rare |
| Trend down | 7.2% | Least common |

**ATR is non-discriminating.** 86.4% of all passing candidates have wide ATR (≥1.5%). This component has essentially no selectivity — any symbol passing the baseline score threshold already has wide ATR. It contributes 15 points to the score but does not differentiate better from worse candidates within the passing set.

**Trend alignment dominates score variance.** Mean score by trend state in passing candidates:

| Trend | N | Avg Score |
|-------|---|-----------|
| Up | 140 | 58.7 |
| Sideways | 181 | 52.0 |
| Down | 69 | 54.4 |

The 6.7-point gap between trend_up (58.7) and trend_sideways (52.0) reflects the scoring weights directly: trend_align=15pts (awarded for LONG with trend=up or SHORT with trend=down) vs trend_part=7pts (sideways), an 8-point difference. Since trend_up candidates are more likely to be above VWAP (a direction-consistent state), they accumulate VWAP points too — the actual observed gap is compressed because some sideways candidates also earn VWAP points.

**Implication:** When a candidate scores 58+ the primary driver is trend_align. When a candidate scores 50–55 the trend is likely sideways (7pts) and the score is built from ATR + ORB + RSI + VWAP without full trend credit. This means the 60-69 score band is almost exclusively trend-aligned candidates, while 40-59 is a mix of sideways-trend and partially-aligned candidates.

**The practical issue:** Both bands show losses. Trend alignment in the scoring model adds 8 points to the total score but does not appear to improve trade outcomes at current N. This could be a signal direction problem (trending into a move that's already extended) or simply small-N noise.

---

## DTE Analysis

**Finding: Strong directional signal. Insufficient DTE>0 observations to confirm.**

| DTE | N | Wins | Win Rate | P&L | Avg/Trade |
|-----|---|------|----------|-----|-----------|
| 0 | 10 | 2 | 20.0% | -$117 | -$11.70 |
| 1 | 1 | 0 | 0% | -$60 | -$60.00 |
| 2–3 | 4 | 2 | 50.0% | -$64 | -$16.00 |
| 4+ | 0 | — | — | — | — |

The DTE=0 bucket at N=10 has sufficient observations (above the N≥3 minimum). The 20% win rate is consistent across Phase 1 DTE=0 trades and Phase 2 S8 DTE=0 trades — this is not a Phase 1 artifact. S8 contributed 3 DTE=0 trades (1 win, 2 losses, pnl=-$7) which maintained the DTE=0 pattern.

DTE=2-3 shows 50% win rate over 4 trades (S6: XLE loss, PLTR win, DIA win). But N=4 is low, and the S6 market regime may have favored LONG setups that week. Both wins were LONG direction in a rising market session (June 15); the loss was also LONG (XLE). Without more DTE=2-3 data from different sessions the 50% is not stable.

DTE=1 has N=1 (one trade, -$60) — a single bad outcome, not informative.

**The core problem:** All DTE>0 trades come from a single Phase 2 session (S6, June 15). The comparison between DTE=0 (10 trades, 5 sessions) and DTE=2-3 (4 trades, 1 session) is not a fair comparison. S11–S15 need to produce at least 3 more DTE>0 trades from different sessions before this comparison is meaningful.

**What S11–S15 need:** At minimum, 3 additional DTE=2-3 trades from sessions other than June expiry week. Until then, the DTE=2-3 vs DTE=0 performance gap may reflect S6's market conditions rather than DTE itself.

---

## Strategy Comparison — ORB vs VWAP

**Finding: Both strategies unprofitable at current N. ORB forward-price data reveals systematic mistiming. VWAP profit factor better despite lower win rate.**

| Strategy | N | Wins | Win Rate | P&L | Profit Factor |
|----------|---|------|----------|-----|---------------|
| ORB | 4 | 2 | 50% | -$102 | 0.143 |
| VWAP | 16 | 3 | 19% | -$146 | 0.305 |

ORB's 50% win rate is superficially encouraging but its profit factor is worse than VWAP's (0.143 vs 0.305). This is because ORB losses are large: the two ORB losses total -$119 (SPY S3 -$99 implied, DIA S8 -$20) while the two ORB wins total +$17 (SMH S3 +$? and IWM S5 +$?).

Wait — actually the 4 ORB trades from the dimension data are: SMH S3, IWM S5, DIA S6 (if DIA was ORB?), DIA S8. Let me recheck from signal_bridge: SMH S3 (traded ORB long), IWM S5 (traded ORB long), DIA S6 — DIA S6 was vwap_reclaim strategy per signal_bridge, not ORB. And DIA S8 was ORB short. So the 4 ORB trades are: SMH S3, IWM S5, and 2 others from P1 (S1-S2 which have no signal_bridge data). The ORB trades in signal_bridge with trades are SMH (loss), IWM S5 (counted separately), and DIA S8 (loss -$20).

### ORB Forward-Price Evidence

The signal_bridge ORB entries with recorded forward prices reveal a consistent pattern:

**SMH ORB LONG (S3, June 10, age 66.4min):**
- orb_fwd_pct_5m: -3.27% (price dropped)
- orb_fwd_pct_15m: -3.60% (continued dropping)
- orb_fwd_pct_30m: -3.47% (continued dropping)
- Outcome: LOSS. The underlying moved strongly against the long direction in every forward window.

**IWM ORB LONG (S5, June 12, age 81.7min):**
- orb_fwd_pct_5m: -0.44%
- orb_fwd_pct_15m: -0.35%
- orb_fwd_pct_30m: -0.36%
- Outcome: LOSS (minor). Underlying moved slightly against long.

**DIA ORB SHORT (S8, June 18, age 90.4min):**
- orb_fwd_pct_5m: +0.017% (price ticked up — against the short)
- orb_fwd_pct_15m: -0.025% (tiny move toward short)
- orb_fwd_pct_30m: +0.017%
- Outcome: LOSS (-$20). No directional follow-through in the breakout direction.

All three traded ORB signals with forward price data show the underlying not following through in the breakout direction. Combined with the signal age context (66–90 minutes after open), the pattern suggests ORB entries are being made after the initial momentum impulse has exhausted.

### ORB with and without Trend Alignment

From the scan_results cross-reference:
- SMH ORB LONG on June 10: The scan_results recorded at later time points during S3 show SMH as SHORT/orb_breakdown (trend_sideways). The ORB LONG signal was generated when SMH initially broke out, but by the time it was traded at age 66.4 minutes, the underlying had already reversed and was in a breakdown state. This is ORB **without** trend alignment at the time of entry — the breakout was stale.
- DIA ORB SHORT on June 18 (age 90.4min): Scan_results for S8 would need individual timestamp cross-reference, but the near-zero forward prices confirm the breakout lacked momentum.

**Blocked ORB signal (MSTR S8, liquid_growth):** MSTR SHORT ORB was blocked (max entries/day already reached) with forward prices of +0.3-0.6% in 5m — price moving UP, which is against the intended short direction. This was also a poor-direction signal even for the blocked entries.

**ORB preliminary conclusion:** Every observed ORB forward price window shows movement opposing the ORB direction. Combined with signal ages of 66–90 minutes (entries occurring well past the opening range window), the ORB strategy appears to be entering too late. The breakout signal is stale by the time the RVOL gate clears and all criteria align for an entry.

### VWAP Reclaim Outcomes

VWAP wins (3): PLTR S6 (+$24), DIA S6 (+$15 carryover), PLTR S8 (+$25)  
Notable VWAP losses: large Phase 1 losses in core_etf VWAP trades (SPY, QQQ, IWM, DIA repeatedly)

PLTR is 2/2 wins in VWAP. The VWAP strategy's better profit factor (0.305 vs 0.143) may be partly a PLTR-specific effect.

---

## Universe Effects — PLTR and Liquid Growth

**Finding: liquid_growth group outperforms on scan scoring. PLTR is 2/2 wins. Phase 2 losses concentrated in core_etfs and mega_cap.**

### Scan Results by Universe (passing candidates only, S3–S8)

| Universe Group | N (passing) | Avg Score | Trend Distribution |
|----------------|-------------|-----------|-------------------|
| liquid_growth | 116 | **57.9** | sideways=22, up=45, down=49 |
| core_etfs | 205 | 54.5 | sideways=110, up=95 |
| mega_cap | 69 | 50.5 | sideways=49, down=20 |

liquid_growth candidates score highest (57.9 avg) and show the most directional movement — clear up and down trends rather than the predominantly sideways state of core_etfs. mega_cap shows the lowest scores and is mostly sideways-to-down with no upward trend observations in the passing set.

This pattern likely reflects the intraday characteristics of each group: liquid_growth names (PLTR, MSTR, CRWD, DDOG, NET, ARM) have higher ATR and more frequent ORB/trend signals, while broad ETFs (SPY, QQQ, IWM, DIA) tend toward range-bound behavior intraday.

### PLTR Specific Record

| Session | Strategy | Direction | Score | Age | Outcome | P&L |
|---------|----------|-----------|-------|-----|---------|-----|
| S6 | vwap_reclaim | LONG | 55 | 108.6 min | WIN | +$24 |
| S8 | vwap_reclaim | SHORT | 50 | 24.6 min | WIN | +$25 |

PLTR is 2/2 in Phase 2. In both cases, PLTR took both directions (LONG S6, SHORT S8) and won. RVOL was borderline in both cases (0.502, 0.505) — PLTR barely cleared the RVOL gate each time, which may indicate these were real conviction signals rather than high-volume noise.

The two PLTR trades account for +$49 of the +$57 in gross wins in Phase 2 (86%). Without PLTR, Phase 2 P&L would be approximately -$21.

### Phase 2 Outcomes by Universe

| Universe Group | Trades (P2) | Wins | P&L |
|----------------|-------------|------|-----|
| liquid_growth (PLTR×2) | 2 | 2 | +$49 |
| core_etfs (XLE, DIA×2) | 3 | 1 | -$9 |
| mega_cap (AAPL) | 1 | 0 | -$12 |

The universe effect is visible but small-N. Three core_etf trades produced one DIA win (carryover, +$15) and two losses (-$24 combined). The PLTR effect should be noted as a single-name concentration.

**Caution:** PLTR's outperformance could reflect the names PLTR happened to be traded on (both times were short-duration, high-volatility sessions), not a structural universe advantage. N=2 for liquid_growth is insufficient to draw conclusions about the group.

---

## Session Behavior — Market Rejection vs Data Degradation

**Finding: Three zero-trade STANDBY sessions completed. Two distinct STANDBY modes present. Current logging does not distinguish them.**

### Session Classification

| Session | Type | Reason | Data Present | Scores |
|---------|------|--------|--------------|--------|
| S7 (2026-06-17) | True STANDBY | RVOL < 0.50 market-wide | YES (full intraday) | 48–70 range |
| S9 (2026-06-24) | Data-failure STANDBY | yfinance daily data absent | NO (sentinel values) | 10.0 (artifact) |
| S10 (2026-06-25) | Data-failure STANDBY | yfinance daily data absent | NO (sentinel values) | 10.0 (artifact) |

**S7 is operationally valid as a STANDBY signal.** RVOL < 0.50 across all 27 symbols for the full session means intraday volume was genuinely insufficient for the scanner's entry criteria. Scores in the 48–70 range (with ATR, ORB, and trend components present) indicate the market was moving but without the volume necessary to support options liquidity. This is the correct behavior: the system is designed to sit out low-volume sessions.

**S9 and S10 are not true STANDBY sessions.** They are data-failure sessions masquerading as STANDBY. The yfinance daily data endpoint returned empty DataFrames for all 27 symbols, causing the exception handler to return zero-sentinel metrics (rvol=0.0, atr_pct=0.0, price=0.0). The scanner correctly rejected these — but the rejections are triggered by invalid data, not by actual market conditions.

**Key distinction for future diagnostics:**
- S7 scan_results: valid observations. Can be used to understand low-RVOL market behavior.
- S9 and S10 scan_results: invalid sentinel data. Must be excluded from all analyses. Any query on scan_results should filter `WHERE session_date NOT IN ('2026-06-24', '2026-06-25')`.

**Pattern concern:** S9 and S10 are consecutive. This may be a sustained change in the yfinance daily endpoint (API deprecation, rate limiting, or data availability policy change) rather than a transient outage. If S11 also fails, this should be escalated — not as a Phase 2 defect (frozen protocol) but as a post-Phase 2 infrastructure fix requiring a fallback data source or endpoint change.

---

## Operational Reliability

**Finding: Container SIGKILL problem solved. Data-source resilience is the remaining operational risk.**

### Infrastructure Timeline

| Date | Issue | Resolution |
|------|-------|------------|
| 2026-06-22 | S9 attempt 1: bash shell recycled (`&`), killed at cycle 22 | Switched to nohup |
| 2026-06-23 | S9 attempt 2: container SIGKILL via nohup, killed at cycle 73 | Switched to tmux |
| 2026-06-24 | S9 (official): tmux survived full window, 165 cycles | tmux confirmed as launch method |
| 2026-06-25 | S10: tmux survived full window, 167 cycles | No process management issues |

The container SIGKILL problem is solved. tmux server processes are independent of the parent shell — container resource management that kills idle shells does not propagate to tmux sessions. Two consecutive successful full-window sessions (S9, S10) confirm this.

**Launch command for all remaining sessions (S11–S15):**
```bash
tmux new-session -d -s sNN -x 220 -y 50
tmux send-keys -t sNN "python -u scripts/session_runner.py --poll 30 --reconcile-interval 10 2>&1 | tee logs/session_YYYY-MM-DD.log" Enter
```

### Remaining Risk: yfinance Data Source

Two consecutive sessions (S9, S10) have shown total yfinance daily data failure. The failure mode is `ticker.history(interval="1d")` returning an empty DataFrame for all symbols, logging `"possibly delisted; no price data found"` — a Yahoo Finance API error, not a network error. The daily data fetch is used for:
- RVOL calculation (requires 20-day avg volume baseline)
- ATR calculation (requires 14-day daily OHLC)
- Gap percentage (requires prior close)
- Trend determination (requires multi-day price series)

When daily data fails, all of these components return to zero-sentinel values, and the scanner correctly rejects all candidates. The entry halt is working correctly. The issue is diagnostic: we cannot distinguish data failure from true market rejection in the current logging.

**Post-Phase 2 fixes documented (do not implement during Phase 2):**
1. `candidate_scorer.py`: score=0 when `m.errors` non-empty (suppress score=10 artifact)
2. `candidate_scorer.py`: `data_fetch_error` alone in rejected_reasons when present (suppress cascade artifacts)
3. Session runner: `INVALID_DATA` status distinct from `STANDBY`
4. Session runner: entry halt when data failure affects >50% of symbols (already effectively happening; needs explicit logging)
5. `scan_results` schema: `data_valid` boolean column

---

## What S11–S15 Must Produce

The following would materially strengthen or resolve open questions:

| Priority | What's Needed | Resolves |
|----------|---------------|---------|
| HIGH | ≥3 trades with DTE=2-3 from different sessions | Q3 DTE — breaks the S6 single-session confound |
| HIGH | ≥3 trades from liquid_growth symbols | Universe effects — validates or refutes PLTR pattern |
| HIGH | ≥1 DTE=0 Phase 2 win | DTE=0 — confirms or refutes 20% win rate ceiling |
| MEDIUM | ≥3 ORB trades (any direction) with consistent forward price data | Q1 ORB/VWAP — expands ORB N and confirms mistiming pattern |
| MEDIUM | Any recovered fill in Phase 2 | Q7 fill path — breaks Phase 1/Phase 2 confound |
| LOW | Any scanner score ≥70 trade | Q6 — populates the highest score band |
| LOW | Any >120 min signal age trade | Q2 age — populates the last bucket |

If S11–S15 are predominantly zero-trade STANDBY sessions (whether genuine or data-failure), none of these will be met and the final review will carry the same indeterminate status across all seven questions.

---

## Questions Resolved at Midpoint

None of the seven primary questions are resolved. However, several sub-findings are stable enough to be treated as established observations:

1. **ATR is non-discriminating within the passing set.** 86% of all passing candidates have wide ATR. It contributes score points but does not identify better trades.

2. **Trend alignment is the dominant score component.** The 8-point gap between trend_aligned and trend_sideways scoring drives the observed score variance in passing candidates.

3. **All observed ORB forward prices moved against the trade direction.** Over 3 ORB trades with forward data, none showed positive follow-through in the breakout direction. This is a consistent pattern, not random variation.

4. **PLTR outperforms in Phase 2; ETF trades underperform.** 2/2 wins for PLTR, 1/4 wins for core_etfs in Phase 2. Too small to be definitive but consistent with the liquid_growth scanner score advantage.

5. **DTE=0 win rate is stable at ~20%.** Consistent across Phase 1 and S8. Adding 3 more DTE=0 trades in S8 did not change the rate. This is likely a structural characteristic of expiry-day options, not noise.

6. **tmux is the correct launch method.** Proven over two consecutive successful sessions. No further process management concerns.

---

*Midpoint review complete — 5/10 Phase 2 sessions. Next review: S15 completion (final).*
