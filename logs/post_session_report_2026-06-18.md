# Post-Session Report — 2026-06-18 (Session 8 of 15)

**Protocol:** Phase 2 Clean Data Collection (frozen config)
**Session Date:** 2026-06-18 (Thursday — June 18 expiry day, weekly contract)
**Session Start:** 09:28:30 ET
**Session End:** 12:30:25 ET
**Total Cycles:** 356
**data_clean:** FALSE — all 3 exit fills required post-session correction from broker-confirmed prices (exit price recording defect, systematic, deferred per Phase 2 protocol). Journal P&L -$14.00 corrected to -$7.00.

---

## Session Result

| Metric | Value |
|--------|-------|
| Session P&L (journal) | -$14.00 |
| Session P&L (corrected) | **-$7.00** |
| Trades | 3 |
| Wins | 1 |
| Losses | 2 |
| Breakeven | 0 |
| Win rate | 33.3% |
| Avg win | +$25.00 |
| Avg loss | -$16.00 |
| Avg signal age | 48.0 min |
| Avg DTE | 0 |
| Avg quality score | 2.67 |
| Avg scanner score | 46.7 |
| ORB trades | 1 |
| VWAP trades | 2 |
| Direct fills | 3 |
| Recovered fills | 0 |

---

## Trade Log

| # | Symbol | Contract | Strategy | Entry Time | Exit Time | Entry Fill | Exit Fill (journal) | Exit Fill (actual) | P&L (actual) | Exit Reason | Hold | Quality | Scanner | DTE | Age (min) |
|---|--------|----------|----------|------------|-----------|------------|--------------------|--------------------|--------------|-------------|------|---------|---------|-----|-----------|
| 36 | AAPL | AAPL260618C00300000 | vwap_reclaim | 10:29:00 | 10:33:06 | $0.78 | $0.64 | **$0.66** | **-$12.00** | trailing_stop | 212s (~3.5 min) | 3 | 42 | 0 | 29.0 |
| 37 | PLTR | PLTR260618P00127000 | vwap_reclaim | 11:14:36 | 11:49:33 | $0.72 | $0.94 | **$0.97** | **+$25.00** | trailing_stop | 2062s (~34.4 min) | 2 | 50 | 0 | 24.6 |
| 38 | DIA | DIA260618P00515000 | orb | 11:40:22 | 11:51:38 | $0.53 | $0.31 | **$0.33** | **-$20.00** | trailing_stop | 612s (~10.2 min) | 3 | 48 | 0 | 90.4 |

All exit fills were broker-confirmed via Alpaca paper API. All fills at better-than-limit price (marketable_limit mode). All fills direct (FillTracker registered, ttf: AAPL=34s, PLTR=34s, DIA=65s).

---

## Infrastructure Verification

| Check | Result |
|-------|--------|
| Session terminated ≤12:35 ET | PASS (actual: 12:30:25 ET) |
| Broker positions at EOD | PASS — 0 open |
| Broker open orders at EOD | PASS — 0 open |
| Exit order 403 errors | None |
| daily_pnl vs sum of corrected fills | PASS — both -$7.00 after correction |
| Every fill in ledger | PASS (3 entries, all verified) |
| Reconciler: repaired / flagged | 0 / 0 (18 clean runs) |
| Journal rows: closed / cancelled / rejected | 3 / 0 / 156 |
| Exit spread warnings | 2 — AAPL (10.37%), DIA (45.0%) |
| LIVE_TRADING_ENABLED | false ✓ |

**data_clean determination:** FALSE
- All 3 exit prices recorded as limit price at order submission rather than broker's confirmed `filled_avg_price` (known defect, deferred per Phase 2 protocol).
- Corrections applied post-session: AAPL 0.64→0.66, PLTR 0.94→0.97, DIA 0.31→0.33.
- DIA exit spread (45%) substantially exceeded the 10% maximum — runner issued WARNING and proceeded. Broker filled at $0.33 vs limit $0.31.
- 18 reconciler runs all clean. 0 broker anomalies. 0 API errors.

---

## Exit Fill Discrepancy Detail

| # | Symbol | Exit Limit | Broker Fill | Diff | P&L (journal) | P&L (actual) |
|---|--------|-----------|-------------|------|---------------|--------------|
| 36 | AAPL | $0.64 | $0.66 | +$0.02 | -$14.00 | -$12.00 |
| 37 | PLTR | $0.94 | $0.97 | +$0.03 | +$22.00 | +$25.00 |
| 38 | DIA | $0.31 | $0.33 | +$0.02 | -$22.00 | -$20.00 |
| | **Total** | | | **+$7.00** | **-$14.00** | **-$7.00** |

All three discrepancies are consistent with `OPTIONS_EXIT_LIMIT_PRICE_MODE=marketable_limit`: sell orders submitted at the bid, filled at a price better than (above) the limit when market conditions allowed. This is the same defect that affected XLE in S6 and AAPL in S8. trading.db rows corrected to broker-confirmed values.

---

## Signal Bridge Summary

| Symbol | Strategy | Direction | Scanner Score | Quality | Age (min) | rvol | Final Decision |
|--------|----------|-----------|--------------|---------|-----------|------|----------------|
| AAPL | vwap_reclaim | LONG (call) | 42 | 3 | 29.0 | 0.524 | traded |
| PLTR | vwap_reclaim | SHORT (put) | 50 | 2 | 24.6 | 0.505 | traded |
| DIA | orb | SHORT (put) | 48 | 3 | 90.4 | 0.523 | traded |

**rvol gate behavior:** rvol cleared 0.50 threshold for active symbols in the 10:15–11:45 ET window. By 12:29 ET, MSTR was rejected for "Max entries per day reached: 3/3" rather than `low_volume_chop`, confirming afternoon volume recovery. The daily entry limit (3) was exhausted before EOD.

---

## Session Narrative

**10:29 ET — AAPL entry:** AAPL was the first symbol to clear the rvol gate (0.524). vwap_reclaim signal, LONG, call $300 strike, DTE=0, scanner score=42 (just above 40 minimum). Direct fill at $0.78 in 34s. Position peaked at $0.97 (MFE=$19). Trailing stop triggered at 10:33 ET as price dropped to $0.64 bid. Actual fill $0.66. Hold 212s. P&L -$12.00.

**15-min cooldown:** Active 10:33–10:48 ET.

**11:14 ET — PLTR entry:** PLTR cleared rvol gate (0.505). vwap_reclaim signal, SHORT (put), $127 strike, DTE=0, score=50, quality=2. Direct fill at $0.72 in 34s. Position appreciated to peak $1.325 (MFE=$60.50). Trailing stop triggered at 11:49 ET at $0.94 bid; actual fill $0.97. Hold 2062s (~34 min). P&L +$25.00.

**11:40 ET — DIA ORB entry:** DIA ORB signal, SHORT (put), $515 strike, DTE=0, score=48, quality=3, signal age 90.4 min. Direct fill at $0.53 in 65s. Position had minimal favorable movement (MFE=$2). Trailing stop triggered at 11:51 ET — extreme exit spread: bid=0.31, ask=0.49 (45%). Runner issued WARNING and proceeded. Actual fill $0.33. Hold 612s (~10 min). P&L -$20.00.

**12:00–12:30 ET — Standby:** All additional candidates blocked by cooldown_after_loss or max_entries_per_day (3/3 reached). Session cycled through EOD at 12:30:25 ET.

---

## Dimension Tables

### By DTE (all DTE=0 this session — expiry day)

| DTE | Trades | Wins | Losses | P&L |
|-----|--------|------|--------|-----|
| 0 | 3 | 1 | 2 | -$7.00 |

### By Strategy

| Strategy | Trades | Wins | Losses | P&L |
|----------|--------|------|--------|-----|
| vwap_reclaim | 2 | 1 | 1 | +$13.00 |
| orb | 1 | 0 | 1 | -$20.00 |

### By Signal Age

| Bucket | Symbol | Age (min) | Outcome | P&L |
|--------|--------|-----------|---------|-----|
| <30 | PLTR | 24.6 | win | +$25.00 |
| <30 | AAPL | 29.0 | loss | -$12.00 |
| 90-120 | DIA | 90.4 | loss | -$20.00 |

### By Quality Score

| Quality | Symbol | Outcome | P&L |
|---------|--------|---------|-----|
| 2 | PLTR | win | +$25.00 |
| 3 | AAPL | loss | -$12.00 |
| 3 | DIA | loss | -$20.00 |

### By Scanner Score

| Bucket | Symbol | Outcome | P&L |
|--------|--------|---------|-----|
| 40-49 | AAPL (42) | loss | -$12.00 |
| 40-49 | DIA (48) | loss | -$20.00 |
| 50-59 | PLTR (50) | win | +$25.00 |

### By Asset Class

| Class | Trades | Wins | Losses | P&L |
|-------|--------|------|--------|-----|
| single_stock | 2 | 1 | 1 | +$13.00 |
| etf | 1 | 0 | 1 | -$20.00 |

### By Fill Path

| Path | Trades | Wins | Losses | P&L |
|------|--------|------|--------|-----|
| direct | 3 | 1 | 2 | -$7.00 |
| recovered | 0 | — | — | — |

---

## Cumulative Phase 2 Running Totals

| Session | Date | P&L (actual) | Trades | Wins | data_clean | Running P&L (P2 only) |
|---------|------|-------------|--------|------|------------|----------------------|
| S6 | 2026-06-15 | +$35.00 | 3 | 2 | FALSE | +$35.00 |
| S7 | 2026-06-17 | $0.00 | 0 | 0 | TRUE | +$35.00 |
| S8 | 2026-06-18 | **-$7.00** | 3 | 1 | FALSE | **+$28.00** |

**Phase 1 baseline (S1–S5, carried forward):** -$276.00, 14 trades, 2 wins

**Combined Phase 1 + Phase 2 (through S8):**
- Total trades: 20 (14 P1 + 3 S6 + 0 S7 + 3 S8)
- Total wins: 5
- Net P&L: -$248.00
- Win rate: 25.0%
- 7 sessions remaining (S9–S15)
- Midpoint review at S10

---

## Infrastructure Anomalies

1. **Exit price recording defect (all 3 trades):** Session runner records limit price as `exit_price` at order submission rather than broker's confirmed `filled_avg_price`. Affected all 3 exits. Combined discrepancy: +$7.00 (all fills better than limit). Corrected post-session in trading.db. Deferred per Phase 2 protocol.

2. **DIA exit spread (45%):** Exit spread of 45% on DIA260618P00515000 far exceeded the 10% max threshold. Runner logged WARNING and proceeded. Actual fill ($0.33) was above limit ($0.31) by $0.02, suggesting some market depth existed despite the wide spread. Expected behavior on DTE=0 far-OTM puts as expiry approached.

3. **AAPL exit spread (10.37%):** Just over the 10% threshold; runner issued WARNING and proceeded. Fill at $0.66 confirmed.

---

## Issues for Next Session

1. **Exit price recording defect (ongoing):** S8 is the third consecutive trading session where this defect has produced accounting discrepancies requiring post-session manual correction. Same fix applied each time. Phase 2 protocol prohibits code changes to fix this unless it causes incorrect position management or risk enforcement. Documenting for post-Phase-2 remediation.

2. **DTE=0 performance:** All 3 S8 trades were DTE=0 (expiry day). Combined S8 DTE=0 P&L is -$7; cumulative DTE=0 P&L through S8 is -$125 over 10 trades (20% win rate). DTE=2-3 shows 50% win rate over 4 trades. Q3 signal is emerging.

3. **Q7 fill path direct N≥3 reached:** Direct fills now at N=5 (3/5 wins, pnl=+$32). Recovered at N=15 (2/15 wins, pnl=-$280). First dimension to show potentially SUPPORTED status — but confound exists (all S8 direct fills are DTE=0 on expiry day, while most recovered fills were DTE=2-3 in Phase 1).
