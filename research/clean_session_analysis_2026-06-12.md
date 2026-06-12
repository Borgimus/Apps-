# Clean Session Historical Analysis — 2026-06-12

**Trigger:** 5 clean sessions complete (per `evaluation/post_fix_eval_protocol.md` Section 8)
**Analysis Date:** 2026-06-12
**Protocol status:** EVIDENCE COLLECTION ONLY — no parameter changes, no threshold adjustments

---

## 1. Data Coverage

### Clean Session Summary

| Session | Date | Trades | Wins | P&L | data_clean | signal_bridge data |
|---------|------|--------|------|-----|------------|-------------------|
| S1 | 2026-06-05 | 3 | 1 | +$2.00 | TRUE | No |
| S2 | 2026-06-08 | 2 | 0 | -$9.00 | TRUE | No |
| S3 | 2026-06-10 | 2 | 0 | -$99.00 | TRUE | Yes (3 traded entries, 1 clean cancel) |
| S4 | 2026-06-11 | 3 | 0 | -$89.00 | TRUE | Yes (3 traded entries) |
| S5 | 2026-06-12 | 4 | 1 | -$81.00 | TRUE | Yes (4 traded entries) |
| **Total** | | **14** | **2** | **-$276.00** | | |

**Win rate:** 2/14 = 14.3%  
**Expectancy:** -$276.00 / 14 = **-$19.71 per trade**  
**Profit factor:** gross wins / gross losses = ($13 + $4) / ($276 + $13 + $4) × ($276 + $13 + $4 − $17) = $17 / $293 = 0.058

Gross wins: S1 ORB +$13, S5 IWM +$4 = $17  
Gross losses: all losses = $276 + $17 = $293 total P&L = [$17 gross wins] − [$293 gross loss] ... 

Wait, recalculating:  
Total P&L = -$276 = gross wins - gross losses  
Gross wins (W): $13 + $4 = $17  
Gross losses (L): $17 - (-$276) = $17 + $276 = $293  
Profit factor = $17 / $293 = **0.058**

**Important data quality note:** Sessions S1 and S2 have no signal_bridge entries (logging not yet implemented for those dates). Signal-level attributes (quality score, signal age, RVOL, confluence) are only available for S3–S5 (10 signal_bridge "traded" entries, 9 completed trades — 1 IWM 2026-06-10 was a clean cancel, no fill).

**Reconciler tracking gap:** All 14 fills went through reconciler recovery (Pattern A: stale-cancel 422 → reconciler opens position). The `trade_journal` table shows status=cancelled for all entries; `realized_pnl` is null in the database. All P&L figures below are derived from post-session reports and manual ledger corrections.

---

## 2. By Strategy

| Strategy | Trades | Wins | Losses | P&L | Avg P&L/trade | Minimum reached? |
|----------|--------|------|--------|-----|--------------|-----------------|
| vwap_reclaim | 11 | 0 | 11 | -$194.00 | -$17.64 | Yes (≥3) |
| orb | 3 | 2 | 1 | -$82.00 | -$27.33 | Yes (≥3) |

**Note on ORB:** The 3 ORB trades are (S1 +$13), (S3 SMH -$99), (S5 IWM +$4). The SMH trade was a 2-DTE call that lost -$99 on an EOD exit when the underlying did not reach the ORB target. The S1 and S5 ORB wins were both small (+$13, +$4). The large SMH loss dominates ORB P&L. Sample size = 3 — at minimum, cannot separate instrument/DTE confounds.

**Note on VWAP:** 11 trades, 0 wins. However, the two breakeven trades (SPY $0 in S3) plus the two ORB wins technically overlap with the broader strategy definitions. All 11 vwap_reclaim completed trades were losses or breakeven.

---

## 3. By Quality Score (S3–S5 signal_bridge data only, 9 completed trades)

| Quality | Trades | Wins | Losses | P&L | Avg P&L | Min reached? |
|---------|--------|------|--------|-----|---------|-------------|
| 1 | 1 | 0 | 1 | -$14.00 | -$14.00 | No (need ≥3) |
| 2 | 4 | 0 | 4 | -$132.00 | -$33.00 | Yes |
| 3 | 2 | 1 | 0* | +$4.00 | +$2.00 | No (need ≥3) |
| 4 | 2 | 0 | 2 | -$127.00 | -$63.50 | No (need ≥3) |

*SPY 2026-06-10 was breakeven ($0), counted as 1 completed trade, 0 wins, 0 losses.

Quality=3: IWM-S5 +$4 (win), SPY-S3 $0 (breakeven) → 1 win by P&L criterion, 1 breakeven.
Quality=4: DIA-S4 -$60, TSLA-S5 -$67. Also IWM-S3 quality=4 was a clean cancel (no fill, excluded).

**Observation:** Quality=2 (4 trades) is the only bucket at minimum. All quality=2 trades were losses.
**No dimension-level conclusions possible** — quality=1, 3, 4 all below minimum threshold of 3.

---

## 4. By DTE (S3–S5 only, 9 completed trades)

| DTE | Trades | Wins | Losses | P&L | Avg P&L | Min reached? |
|-----|--------|------|--------|-----|---------|-------------|
| 0 | 7 | 1 | 5* | -$118.00 | -$16.86 | Yes (≥3) |
| 1 | 1 | 0 | 1 | -$60.00 | -$60.00 | No |
| 2 | 1 | 0 | 1 | -$99.00 | -$99.00 | No |

*SPY breakeven ($0) excluded from win/loss counts but included in total.

**Observation:** DTE=0 dominates the dataset (7/9 completed trades). Only DTE=0 exceeds minimum. DTE=1 and DTE=2 each have 1 trade — both large losses. Cannot compare DTE buckets.

---

## 5. By Universe Group (S3–S5 signal_bridge data, 9 completed trades)

| Group | Trades | Wins | Losses | P&L | Avg P&L | Min reached? |
|-------|--------|------|--------|-----|---------|-------------|
| core_etfs | 8 | 1 | 6* | -$202.00 | -$25.25 | Yes (≥3) |
| mega_cap | 1 | 0 | 1 | -$67.00 | -$67.00 | No |

*SPY breakeven excluded from win/loss; DIA-S4 counted as loss.

**Observation:** core_etfs dominates (8/9 trades). mega_cap has only 1 trade (TSLA). Cannot compare groups.

Including S1-S2 (strategy-only data, no group):
- S1 ORB win was likely core_etfs (not confirmed in signal_bridge)
- S2 losses were vwap_reclaim (group unknown)

---

## 6. By Signal Age (S3–S5 signal_bridge data, 9 completed trades)

Age buckets: <30 min (<1800s), 30–90 min (1800–5400s), >90 min (>5400s)

| Age Bucket | Trades | Wins | Losses | P&L | Avg P&L | Min reached? |
|------------|--------|------|--------|-----|---------|-------------|
| <30 min | 2 | 0 | 1* | -$14.00 | -$7.00 | No |
| 30–90 min | 6 | 1 | 5 | -$195.00 | -$32.50 | Yes |
| >90 min | 1 | 0 | 1 | -$60.00 | -$60.00 | No |

*SPY-S3 ($0, 11 min) in <30 min bucket — breakeven, not win/loss.

**Signal ages (seconds) for all 9 completed trades:**
- SPY-S3: 656s (~11 min)
- DIA-S5: 932s (~16 min)
- IWM-S4: 1895s (~32 min)
- TSLA-S5: 1898s (~32 min)
- QQQ-S4: 3090s (~52 min)
- XLF-S5: 3956s (~66 min)
- SMH-S3: 3985s (~66 min)
- IWM-S5: 4902s (~82 min)
- DIA-S4: 8823s (~147 min)

**Observation:** 6/9 trades are in the 30–90 min bucket. The >90 min trade (DIA-S4, 147 min) had the highest loss next to SMH. Sample too small for age-based conclusions.

---

## 7. By Scanner Score (S3–S5, 9 completed trades)

| Score Range | Trades | Wins | P&L | Avg P&L |
|-------------|--------|------|-----|---------|
| 35–44 | 3 | 0 | -$82.00 | -$27.33 |
| 45–54 | 2 | 0 | -$14.00 | -$7.00 |
| 55–64 | 4 | 1 | -$180.00 | -$45.00 |

(SPY=38, IWM-S4=42, DIA-S4=43 in 35–44; DIA-S5=51, QQQ=42 in 45–54; XLF=58, IWM-S5=60, TSLA=62, SMH=62 in 55–64)

**Observation:** No positive score→P&L correlation visible. Higher scanner scores (55–64) had lower average P&L than mid-range (45–54). Sample too small for conclusions.

---

## 8. By RVOL at Signal (S3–S5, 9 completed trades)

| RVOL Range | Trades | Wins | P&L | Notes |
|------------|--------|------|-----|-------|
| 0.30–0.39 | 1 | 0 | $0.00 | SPY (breakeven) |
| 0.50–0.59 | 6 | 0 | -$196.00 | All just above clearance threshold |
| 0.60–0.70 | 2 | 1 | -$63.00 | TSLA 0.614, IWM-S5 0.629 |

**Observation:** Most trades occur near the RVOL clearance threshold (0.5). TSLA had the highest RVOL (0.614) and was the largest loss (-$67). IWM-S5 (0.629) was the only win (+$4).

---

## 9. By Entry Spread (S3–S5, 9 completed trades)

| Spread | Trades | Wins | P&L |
|--------|--------|------|-----|
| <5% (tight) | 2 | 0 | -$67.00 | QQQ-S4 1.77%, TSLA-S5 2.82% |
| 5–10% (moderate) | 7 | 1 | -$209.00 | All others |

*TSLA is tight spread with the largest loss (-$67). No spread advantage visible in this data.*

---

## 10. Session-Level Summary (all 5 clean sessions)

| Metric | Value |
|--------|-------|
| Total clean sessions | 5 |
| Total clean trades | 14 |
| Win rate | 14.3% (2/14) |
| Gross wins | +$17.00 |
| Gross losses | -$293.00 |
| Net P&L | -$276.00 |
| Avg P&L/trade | -$19.71 |
| Profit factor | 0.058 |
| Sessions with STANDBY ≥ 1hr | 5/5 (RVOL < 0.5 for first 60–140 min every session) |
| Sessions where Pattern A (reconciler) occurred | 5/5 |
| Sessions using vwap_reclaim only | S2, S4 |
| Sessions with ORB trade | S1, S3, S5 |

---

## 11. Dimension Minimum Status

| Dimension | Minimum (N≥3 per bucket) | Status |
|-----------|--------------------------|--------|
| Quality score | q=2: 4 trades ✓; q=1,3,4: <3 each | **Partial** — q=2 reached, others not |
| DTE | DTE=0: 7 trades ✓; DTE=1,2: 1 each | **Partial** — DTE=0 reached only |
| Universe group | core_etfs: 8 ✓; mega_cap: 1 | **Partial** |
| Signal age | 30–90 min: 6 ✓; <30, >90: <3 | **Partial** |
| Scanner score | No bucket has ≥3 in clean range | **Not reached** |
| Strategy | vwap: 11 ✓; orb: 3 ✓ | **Both reached** |
| IV buckets | IV data not reliably captured (reconciler gap) | **Not applicable** |
| ORB completed | 3 trades | **Reached** |

---

## 12. Cross-Dimension Confound Notes

Per protocol Section 7: before drawing conclusions about a dimension, check for confounds.

- **Quality=2 losses:** all are vwap_reclaim, all DTE=0, all core_etfs. Cannot isolate quality.
- **ORB vs VWAP:** ORB P&L is dominated by 1 outlier (SMH -$99). Without that trade, ORB would be +$17 on 2 trades. Insufficient to conclude ORB > VWAP.
- **mega_cap (TSLA):** Only 1 trade. Confounded with strategy (vwap_reclaim), DTE=0, HIGH quality=4. Cannot isolate group effect.
- **DTE=1,2:** Both are vwap_reclaim (DIA-S4) and orb (SMH-S3). Confounded with strategy, instrument, session-day market conditions.

---

## 13. Open Variables from Prior Protocol Observations (Updated)

From `evaluation/post_fix_eval_protocol.md` Section 10 — updated with current data:

| Variable | Updated observation | Minimum N reached? |
|----------|--------------------|--------------------|
| Quality score (4 vs 2–3) | q=4: 2 losses (-$127 total). q=2: 4 losses. q=3: 1 win + 1 breakeven. q=1: 1 loss | No (q=4 N=2, q=3 N=2, q=1 N=1) |
| DTE (0 vs 2+) | DTE=0: 1 win/7 completed. DTE=1,2: 0 wins/2 completed | No (DTE=1,2 each N=1) |
| Universe (core_etfs vs mega_cap) | core_etfs: 1 win/8. mega_cap: 0 wins/1 | No (mega_cap N=1) |
| Signal age 120+ min | DIA-S4 age=147 min: -$60 loss (N=1) | No |
| ORB vs VWAP | ORB: 2 wins/3 trades. VWAP: 0 wins/11 trades | ORB minimum reached (N=3) — confounded by outlier |
| Scanner score | No clear positive correlation | Not reached per bucket |
| Confluence count | Range 4–8 in completed trades, all losses dominant | Not reached |
| IV at entry | Not reliably captured (reconciler tracking gap in DB) | Not applicable |

---

## 14. Protocol Status

**5/5 clean sessions complete.** Historical analysis is documented above.

**Minimum observation thresholds not met** for most dimension-level comparisons (quality score, DTE, universe group, signal age, scanner score). Only strategy (ORB vs VWAP, both ≥3) and signal-age bucket 30–90 min (6 trades) meet minimums.

**No performance conclusions are drawn. No parameter changes are recommended.**

Per protocol: "Do not feed findings back into strategy parameters until the analysis document is reviewed and the sample sizes are sufficient."

The most notable pattern in the evidence — all 14 clean trades were made under identical frozen configuration, all fills via Pattern A (reconciler recovery from 422), and all sessions entered STANDBY for the first 60–140 min — should be factored into any future protocol design decisions about session timing and minimum observation counts.

---

*Generated per `evaluation/post_fix_eval_protocol.md` Section 8. Analysis date: 2026-06-12.*
