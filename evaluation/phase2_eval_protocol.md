# Phase 2 Evaluation Protocol — Sessions 6–15

**Created:** 2026-06-13  
**Status:** ACTIVE  
**Supersedes:** `evaluation/post_fix_eval_protocol.md` for session numbering and reporting; that document remains the reference for config freeze and acceptance criteria  
**Midpoint review:** After Session 10 (5 additional sessions from today)  
**Final review:** After Session 15 (10 additional sessions from today)

---

## 1. Objective

Collect enough clean observations to determine whether any strategy, signal characteristic, or trade attribute demonstrates consistent predictive value.

**This phase is data collection only.** No optimization is permitted during the run.

### What must not change

- Strategy thresholds
- Scanner thresholds
- Risk settings
- Universe composition
- DTE filters
- Quality-score logic
- Exit rules (trailing stop, EOD exit time)

### Permitted code changes

Only defects causing:
- Incorrect accounting
- Incorrect trade recording
- Incorrect position management
- Incorrect risk enforcement
- Data corruption

All other observations are research findings only. No parameter changes until the final review is complete and evidence is deemed sufficient.

---

## 2. Config Freeze

Identical to `evaluation/post_fix_eval_protocol.md` Section 2.

```
LIVE_TRADING_ENABLED=false
PAPER_EVALUATION_MODE=true
PAPER_EVAL_PERMISSIVE_ENTRY_MODE=true
UNIVERSE_SCAN_INTERVAL_MINUTES=5
RISK_MAX_TRADES_PER_DAY=3
UNIVERSE_MAX_ACTIVE_POSITIONS=2
UNIVERSE_MAX_SYMBOLS_TRADED_PER_DAY=3
UNIVERSE_MAX_CONTRACTS_PER_POSITION=1
ORB_SLOT_RESERVE_UNTIL=11:30
UNIVERSE_GROUPS_ENABLED=core_etfs,mega_cap,liquid_growth
UNIVERSE_ALLOW_CLI_FALLBACK_WHEN_SCANNER_REJECTS=false
POSITION_EOD_EXIT_TIME=12:30
OPTIONS_ENTRY_LIMIT_PRICE_MODE=marketable_limit
OPTIONS_EXIT_LIMIT_PRICE_MODE=marketable_limit
```

Launch command:
```bash
python scripts/session_runner.py --poll 30 --reconcile-interval 10
```

---

## 3. Primary Questions

| ID | Question |
|----|---------|
| Q1 | Does ORB outperform VWAP over a meaningful sample? |
| Q2 | Does signal age correlate with outcome? |
| Q3 | Does DTE correlate with outcome? |
| Q4 | Do ETFs behave differently than single stocks? |
| Q5 | Do quality scores separate winners from losers? |
| Q6 | Does scanner score contain predictive value? |
| Q7 | Do reconciler-recovered fills behave differently from normal (direct FillTracker) fills? |

---

## 4. Tracked Dimensions

### Strategy
- `orb`
- `vwap_reclaim`

### Signal Age
- < 30 min
- 30–60 min
- 60–90 min
- 90–120 min
- > 120 min

### DTE
- 0
- 1
- 2–3
- 4+

### Asset Class
- ETF (`core_etfs` universe group: SPY, QQQ, IWM, DIA, XLF, SMH, XLE, XLK)
- Single-stock (`mega_cap`, `liquid_growth` universe groups)

### Quality Score
- 1, 2, 3, 4

### Scanner Score
- < 40
- 40–49
- 50–59
- 60–69
- 70+

### Fill Path (Q7)
- `recovered` — fill discovered by periodic reconciler (Pattern A: stale-cancel 422 → reconciler)
- `direct` — fill confirmed by FillTracker within timeout window

**How to determine:** Check session log for `"Reconciler: repaired"` entries. Each repair corresponds to one recovered fill. Remaining filled trades (where FillTracker's `_handle_filled` fired) are direct. In practice, most fills have been recovered; direct fills may be rare.

---

## 5. Phase 1 Baseline (Carried Forward)

Phase 1 comprises clean sessions S1–S5 (2026-06-05 through 2026-06-12). All data from `research/clean_session_analysis_2026-06-12.md`.

| Metric | Value |
|--------|-------|
| Sessions | 5 |
| Trades | 14 |
| Wins | 2 |
| Losses | 11 |
| Breakeven | 1 |
| Net P&L | −$276.00 |
| Gross wins | +$17.00 |
| Gross losses | −$293.00 |
| Win rate | 14.3% |
| Avg P&L/trade | −$19.71 |
| Profit factor | 0.058 |
| Recovered fills | 14 / 14 |
| Direct fills | 0 / 14 |

**Dimension counts from Phase 1 (S3–S5 only, signal_bridge data available):**

| Dimension | Bucket | N | Wins | P&L |
|-----------|--------|---|------|-----|
| Strategy | vwap_reclaim | 11 | 0 | −$194.00 |
| Strategy | orb | 3 | 2 | −$82.00 |
| Quality | 1 | 1 | 0 | −$14.00 |
| Quality | 2 | 4 | 0 | −$132.00 |
| Quality | 3 | 2 | 1 | +$4.00 |
| Quality | 4 | 2 | 0 | −$127.00 |
| DTE | 0 | 7 | 1 | −$118.00 |
| DTE | 1 | 1 | 0 | −$60.00 |
| DTE | 2–3 | 1 | 0 | −$99.00 |
| Asset class | ETF | 13 | 2 | −$209.00 |
| Asset class | Single-stock | 1 | 0 | −$67.00 |
| Signal age | < 30 min | 2 | 0 | $0.00* |
| Signal age | 30–60 min | 3 | 0 | −$93.00 |
| Signal age | 60–90 min | 3 | 1 | −$102.00 |
| Signal age | 90–120 min | 1 | 0 | −$60.00 |
| Signal age | > 120 min | 0 | — | — |
| Scanner score | < 40 | 3 | 0 | −$82.00 |
| Scanner score | 40–49 | 2 | 0 | −$14.00 |
| Scanner score | 50–59 | 4 | 1 | −$180.00 |
| Scanner score | 60–69 | 0 | — | — |
| Scanner score | 70+ | 0 | — | — |
| Fill path | recovered | 14 | 2 | −$276.00 |
| Fill path | direct | 0 | — | — |

*SPY S3 breakeven ($0, 11 min) in < 30 min bucket.

**Note on asset class:** S1 and S2 signal_bridge data not available; asset class assumed ETF based on traded symbols. 13/14 fills were ETF symbols; TSLA (S5) was the only single-stock.

**Dimension minimums reached (Phase 1 only):**

| Dimension | Status |
|-----------|--------|
| Strategy (ORB, VWAP) | ORB N=3 ✓, VWAP N=11 ✓ — both reached |
| Quality (all four levels) | Only Q=2 (N=4) reached; Q=1,3,4 below minimum |
| DTE | Only DTE=0 (N=7) reached; DTE=1,2+ below minimum |
| Asset class | ETF (N=13) ✓; single-stock (N=1) not reached |
| Signal age | 30–60 (N=3) ✓, 60–90 (N=3) ✓; other buckets not reached |
| Scanner score | No bucket at minimum (≥3) |
| Fill path | Only recovered (N=14); direct N=0 |

---

## 6. Per-Session Reporting Requirements

After every session, produce:

1. `logs/post_session_report_<YYYY-MM-DD>.md` using the template from Section 7
2. Update `evaluation/ledger.json` (same requirements as Phase 1)
3. Update `evaluation/phase2_tracking.json` with the session's dimension data

### Minimum per-session metrics to record

| Metric | Source |
|--------|--------|
| Session date | — |
| Session P&L | Post-session report |
| Trade count | Ledger |
| Win count | Ledger |
| Loss count | Ledger |
| ORB trades | Session log |
| VWAP trades | Session log |
| Avg signal age (min) | `signal_bridge` table |
| Avg DTE | `trade_journal` table |
| Avg scanner score | `signal_bridge` table |
| Avg quality score | `signal_bridge` table |
| Recovered fills | Session log (reconciler repaired count) |
| Direct fills | Session log |

---

## 7. Post-Session Report Template (Phase 2)

```markdown
# Post-Session Report — <DATE> (Session N of 15)

**Protocol:** Phase 2 Clean Data Collection (frozen config)
**Session Date:** <DATE>
**Session Start:** HH:MM:SS ET
**Session End:** HH:MM:SS ET
**Total Cycles:** N
**data_clean:** TRUE/FALSE

---

## Session Result

| Metric | Value |
|--------|-------|
| Session P&L | $X.XX |
| Trades | N (N direct / N recovered) |
| Wins | N |
| Losses | N |
| Breakeven | N |
| Avg signal age | N min |
| Avg DTE | N |
| Avg quality score | N.N |
| Avg scanner score | N.N |
| ORB trades | N |
| VWAP trades | N |

---

## Trade Log

| # | Symbol | Contract | Strategy | Entry | Exit | Fill (entry) | Fill (exit) | P&L | Exit Reason | Hold | Quality | Score | Age (min) | DTE | Entry Spr% | Exit Spr% | Universe | Fill Path |
|---|--------|----------|----------|-------|------|--------------|-------------|-----|-------------|------|---------|-------|-----------|-----|-----------|-----------|----------|-----------|

**Session P&L (actual broker fills):** $X.XX
**Session P&L (journal):** $X.XX
**Discrepancy:** $X.XX

---

## Infrastructure Verification

| Check | Result |
|-------|--------|
| Session terminated ≤12:35 ET | PASS/FAIL |
| Broker positions at EOD | 0 |
| Broker open orders at EOD | 0 |
| 403 errors on exit orders | None |
| daily_pnl vs sum of fills | MATCH/MISMATCH |
| Every fill in ledger | PASS/FAIL |
| Reconciler: repaired / flagged | N / N |
| Journal rows: closed / cancelled / rejected | N / N / N |

---

## Dimension Tables

### By Strategy

| Strategy | N | Wins | P&L |
|----------|---|------|-----|

### By Quality Score

| Quality | N | Wins | P&L |
|---------|---|------|-----|

### By DTE

| DTE | N | Wins | P&L |
|-----|---|------|-----|

### By Signal Age

| Age Bucket | N | Wins | P&L |
|------------|---|------|-----|

### By Asset Class

| Class | N | Wins | P&L |
|-------|---|------|-----|

### By Fill Path

| Path | N | Wins | P&L |
|------|---|------|-----|

---

## Cumulative Phase 2 Running Totals

| Session | Date | P&L | Trades | Wins | data_clean | Running P&L |
|---------|------|-----|--------|------|------------|-------------|
[carry forward from prior session]

---

## Infrastructure Anomalies

---

## Issues for Next Session
```

---

## 8. Acceptance Criteria (unchanged from Phase 1)

A session is accepted into the clean dataset if and only if:

- [ ] `LIVE_TRADING_ENABLED=false` confirmed in log
- [ ] Session terminated at or before 12:35 ET
- [ ] Broker positions at EOD = 0
- [ ] Broker open orders at EOD = 0
- [ ] No 403 Forbidden responses on exit order placement
- [ ] `daily_pnl` from RiskManager matches sum of fills (±$5 tolerance)
- [ ] Every `status=closed` trade in trade_journal has verified fill price
- [ ] Journal `exit_price` matches Alpaca `filled_avg_price` (or discrepancy explained)
- [ ] Post-session report filed
- [ ] Ledger updated
- [ ] `phase2_tracking.json` updated

---

## 9. Midpoint Review — After Session 10

**Trigger:** 5 additional clean sessions complete (cumulative sessions: S1–S10)

**Output:** `research/phase2_midpoint_review.md`

**Contents:**
- Sample counts per dimension bucket (cumulative S1–S10)
- Win rate and P&L per bucket (no conclusions, report only)
- Identification of which dimensions have reached minimum observation threshold
- Open questions that remain unanswered
- Distribution of signal ages, DTE, quality scores, scanner scores
- Pattern A (reconciler recovery) vs direct fill rate
- No recommendations

---

## 10. Final Review — After Session 15

**Trigger:** 10 additional clean sessions complete (cumulative sessions: S1–S15)

**Output:** `research/phase2_final_analysis.md`

**Contents for every tracked dimension:**

| Section | Content |
|---------|---------|
| Sample size | N per bucket |
| Win rate | wins / total |
| Total P&L | sum |
| Average P&L | mean per trade |
| Profit factor | gross wins / gross losses |
| Status | SUPPORTED / NOT SUPPORTED / INDETERMINATE |

**Verdict criteria:**

| Status | Meaning |
|--------|---------|
| SUPPORTED | ≥3 trades per compared bucket; pattern holds after confound check; not reversed by removing a single outlier |
| NOT SUPPORTED | ≥3 trades per compared bucket; no meaningful difference between buckets |
| INDETERMINATE | < 3 trades in at least one bucket being compared; cannot conclude |

Primary questions:

- **Q1 (ORB vs VWAP):** SUPPORTED if ORB profit factor > 1 or win rate > VWAP with N≥5 each and the difference survives outlier removal
- **Q2 (Signal age):** SUPPORTED if any age bucket is clearly separated from at least one other with N≥3 each
- **Q3 (DTE):** SUPPORTED if DTE buckets separate with N≥3 each
- **Q4 (ETF vs single-stock):** SUPPORTED if N≥3 single-stock trades exist and performance differs materially from ETFs
- **Q5 (Quality score):** SUPPORTED if higher quality buckets show higher win rate with N≥3 per level
- **Q6 (Scanner score):** SUPPORTED if any score range shows positive expectancy with N≥3
- **Q7 (Fill path):** SUPPORTED if recovered fills and direct fills differ after controlling for other dimensions; INDETERMINATE if direct fills remain N=0

Do not recommend strategy changes unless evidence is SUPPORTED with sufficient observations.

---

## 11. Minimum Observations Tracker

Updated after every session. Minimum is ≥3 per bucket being compared (from Phase 1 protocol).

| Dimension | Bucket | Phase 1 N | Target |
|-----------|--------|-----------|--------|
| Strategy | orb | 3 | 5+ |
| Strategy | vwap_reclaim | 11 | — |
| Quality | 1 | 1 | 3 |
| Quality | 2 | 4 | — |
| Quality | 3 | 2 | 3 |
| Quality | 4 | 2 | 3 |
| DTE | 0 | 7 | — |
| DTE | 1 | 1 | 3 |
| DTE | 2–3 | 1 | 3 |
| DTE | 4+ | 0 | 3 |
| Asset class | ETF | 13 | — |
| Asset class | Single-stock | 1 | 3 |
| Signal age | < 30 min | 2 | 3 |
| Signal age | 30–60 min | 3 | — |
| Signal age | 60–90 min | 3 | — |
| Signal age | 90–120 min | 1 | 3 |
| Signal age | > 120 min | 0 | 3 |
| Scanner | < 40 | 3 | — |
| Scanner | 40–49 | 2 | 3 |
| Scanner | 50–59 | 4 | — |
| Scanner | 60–69 | 0 | 3 |
| Scanner | 70+ | 0 | 3 |
| Fill path | recovered | 14 | — |
| Fill path | direct | 0 | 3 |

---

## 12. Success Criteria

- [ ] 10 additional clean sessions completed (S6–S15)
- [ ] No unresolved accounting discrepancies
- [ ] Ledger reconciles with account equity (within $1, or all differences explained)
- [ ] `research/phase2_midpoint_review.md` generated after S10
- [ ] `research/phase2_final_analysis.md` generated after S15
- [ ] Evidence-based conclusions only
- [ ] No optimization during collection period
