# Post-Session Report — 2026-06-15 (Session 6 of 15)

**Protocol:** Phase 2 Clean Data Collection (frozen config)
**Session Date:** 2026-06-15 (Monday)
**Session Start:** 09:37:06 ET
**Session End:** 12:30:18 ET
**Total Cycles:** 342
**data_clean:** FALSE — DIA EOD exit order expired unfilled at broker (confirmed 2026-06-16); position carried over to Session 7. XLE exit price discrepancy corrected.

**FINAL RESOLUTION (2026-06-16, at Session 7 startup):** Confirmed via broker order history that the DIA260618C00522000 EOD sell limit (order `8d4e94db`, limit $1.83) **expired unfilled** at 4:00 PM ET on 2026-06-15. The position remained open overnight (qty=1, entry fill=$1.80). Trade journal row 35 was corrected from a premature `status=closed` (with fabricated exit_price=$1.83) back to `status=open`, and a defect was fixed: `SessionRecovery.recover()` had no mechanism to re-link a broker position carried over from a prior session back to its original journal row — it would have created an orphan `strategy_id="recovered"` entry with no signal/quality/DTE metadata. Added `TradeJournal.find_open_for_symbol()`, wired into recovery. At Session 7 startup (09:46:39 ET, 2026-06-16) the carryover correctly re-linked to journal row 35 with its original entry_time (2026-06-15 11:48:54). This let the existing `max_hold` exit rule fire on schedule (~22 hr hold, no threshold changes) — exit order placed at $1.95, **broker-confirmed filled** at 09:46:58 ET, realized_pnl=+$15.00, hold=79,068s. All three S6 trades are now fully resolved and reconciled. DIA's P&L is attributed to S6 (this session) per Phase 2 carryover handling, even though it physically resolved during S7.

---

## Session Result

| Metric | Value |
|--------|-------|
| Session P&L (final, all 3 trades resolved) | +$35.00 (XLE -$4, PLTR +$24, DIA +$15) |
| Trades | 3 entries (3 closed — DIA resolved at Session 7 startup via carryover) |
| Wins | 2 (PLTR +$24, DIA +$15) |
| Losses | 1 (XLE -$4 actual / -$6 journal at limit) |
| Carryover (resolved in S7) | 1 (DIA — S6 EOD limit expired unfilled; max_hold exit filled $1.95 at S7 startup, P&L attributed to S6) |
| Breakeven | 0 |
| Avg signal age | 73.5 min |
| Avg DTE | 3 (all June 18 expiry — Thursday due to Juneteenth holiday June 19) |
| Avg quality score | 2.33 (XLE=2, PLTR=3, DIA=2) |
| Avg scanner score | 60.0 (XLE=67, PLTR=55, DIA=58) |
| ORB trades | 0 |
| VWAP trades | 3 |

---

## Trade Log

| # | Symbol | Contract | Strategy | Entry | Exit | Fill (entry) | Fill (exit) | P&L | Exit Reason | Hold | Quality | Score | Age (min) | DTE | Entry Spr% | Exit Spr% | Universe | Fill Path |
|---|--------|----------|----------|-------|------|--------------|-------------|-----|-------------|------|---------|-------|-----------|-----|-----------|-----------|----------|-----------|
| 33 | XLE | XLE260618C00056500 | vwap_reclaim | 10:52:55 | 11:09:14 | $0.42 | $0.38* | -$4* | trailing_stop | 11.3 min | 2 | 67 | 37.9 | 3 | 2.4% | 5.4% | core_etfs (ETF) | recovered |
| 34 | PLTR | PLTR260618C00136000 | vwap_reclaim | 11:38:35 | 12:30:17 | $2.08 | $2.32 | +$24 | eod_exit | 51.1 min | 3 | 55 | 108.6 | 3 | 1.5% | n/a | liquid_growth (stock) | direct |
| 35 | DIA | DIA260618C00522000 | vwap_reclaim | 11:48:54 | 09:46:58 (S7) | $1.80 | $1.95‡ | +$15‡ | max_hold (carryover, resolved S7) | 79,068s (~21.97 hr) | 2 | 58 | 73.9 | 3 | 8.6% | n/a | core_etfs (ETF) | direct |

*XLE: journal exit_price=0.36 (limit), actual broker fill=0.38. Journal corrected; realized_pnl updated to -$4. See discrepancy note.  
‡DIA: S6 EOD limit sell at $1.83 (order `8d4e94db`) expired unfilled at 4:00 PM ET market close on 2026-06-15. Position carried over open to Session 7. At S7 startup (2026-06-16 09:46:39 ET) the carryover was re-linked to this journal row (defect fix: `SessionRecovery`/`find_open_for_symbol`), preserving the original entry_time. The existing `max_hold` exit rule then fired on schedule, placing a sell at $1.95 (order `9a97cdfc...`), broker-confirmed filled at 09:46:58 ET. realized_pnl=+$15.00. P&L attributed to S6 per Phase 2 carryover handling.

**Session P&L (final, broker-confirmed fills, all 3 trades):** +$35.00 (XLE -$4, PLTR +$24, DIA +$15)
**Discrepancy — XLE (resolved):** journal originally recorded -$6 (limit price) vs actual -$4 broker fill (+$2 in favor); corrected in DB and ledger.
**Discrepancy — DIA (resolved):** journal originally recorded a premature closed status at $1.83 (+$3) assuming the EOD limit would fill; the order actually expired unfilled. Corrected back to open, then naturally closed by the max_hold rule at S7 startup for a broker-confirmed +$15.

---

## Infrastructure Verification

| Check | Result |
|-------|--------|
| Session terminated ≤12:35 ET | PASS (actual: 12:30:18 ET) |
| Broker positions at EOD | FAIL (as of S6 end) — DIA260618C00522000 qty=1 still open at 12:30:18 ET. RESOLVED 2026-06-16 09:46:58 ET: max_hold exit filled $1.95 at S7 startup. |
| Broker open orders at EOD | FAIL (as of S6 end) — DIA260618C00522000 sell limit $1.83 status=new at 4 PM ET; order subsequently expired unfilled. RESOLVED via S7 carryover exit. |
| 403 errors on exit orders | None |
| daily_pnl vs sum of confirmed fills | RESOLVED — final confirmed fills sum to $35 (XLE -$4 + PLTR +$24 + DIA +$15); session-runner-reported $21 (DIA at limit) was provisional and has been superseded by broker-confirmed figures. |
| Every fill in ledger | PASS (3 trades recorded) |
| Reconciler: repaired / flagged | 1 / 0 (XLE at 10:58 ET) |
| Journal rows: closed / cancelled / rejected | 3 / 0 / 0 (final — DIA closed at S7 startup) |
| XLE exit_price journal vs broker | MISMATCH — journal $0.36, broker fill $0.38 (+$2); corrected in DB and ledger |
| DIA exit_price journal vs broker | RESOLVED — journal originally recorded $1.83 (limit, unfilled); broker-confirmed actual exit fill $1.95 via max_hold rule at S7 startup. Journal corrected to match. |
| LIVE_TRADING_ENABLED | false ✓ |

**data_clean determination:** FALSE (final — this determination does not change retroactively; data_clean reflects state as of original session end, not subsequent cross-session resolution)

Failing criteria (as recorded at original session end, 2026-06-15):
1. Broker positions at EOD ≠ 0 (DIA open) — subsequently resolved 2026-06-16 at S7 startup, see FINAL RESOLUTION above
2. Journal exit_price vs Alpaca mismatch: XLE ($0.36 journal / $0.38 actual) — corrected post-session
3. DIA exit unconfirmed at original session end — now fully confirmed (broker fill $1.95, +$15) and reconciled

---

## Signal Bridge Summary

| Decision | Count | Top Block Reason |
|----------|-------|-----------------|
| traded | 3 | — |
| skipped | 86 | rsi_trend_diagnostic_only |
| skipped | 2 | pending_order_exists |
| blocked/rejected | 0 | — |

**Total signal_bridge rows:** 91

ORB slot: reserved until 11:30 ET, no ORB signals triggered  
Reconciler: 20 runs, 19 clean, 1 repaired (XLE at 10:58 ET), 0 flagged  
Stale cancels: 1 (XLE entry — Pattern A)  
Direct fills: 2 (PLTR, DIA entries — first direct fills of Phase 2)

---

## Dimension Tables

### By Strategy

| Strategy | N | Wins | P&L |
|----------|---|------|-----|
| vwap_reclaim | 3 | 2 | +$35 (final, broker-confirmed) |
| orb | 0 | — | — |

### By Quality Score

| Quality | N | Wins | P&L |
|---------|---|------|-----|
| 2 | 2 | 1 (DIA) | -$4 + $15 = +$11 |
| 3 | 1 | 1 (PLTR) | +$24 |

### By DTE

| DTE | N | Wins | P&L |
|-----|---|------|-----|
| 3 | 3 | 2 | +$35 |

(All three trades: June 18 expiry from June 15 session date = DTE 3)

### By Signal Age

| Age Bucket | N | Wins | P&L |
|------------|---|------|-----|
| 30–60 min | 1 (XLE, 37.9 min) | 0 | -$4 |
| 60–90 min | 1 (DIA, 73.9 min) | 1 | +$15 |
| 90–120 min | 1 (PLTR, 108.6 min) | 1 | +$24 |

### By Asset Class

| Class | N | Wins | P&L |
|-------|---|------|-----|
| ETF (core_etfs) | 2 (XLE, DIA) | 1 (DIA) | +$11 |
| Single-stock (liquid_growth) | 1 (PLTR) | 1 | +$24 |

### By Scanner Score

| Score Bucket | N | Wins | P&L |
|-------------|---|------|-----|
| 50–59 | 2 (PLTR=55, DIA=58) | 2 | +$39 |
| 60–69 | 1 (XLE=67) | 0 | -$4 |

### By Fill Path

| Path | N | Wins | P&L |
|------|---|------|-----|
| recovered (Pattern A) | 1 (XLE) | 0 | -$4 |
| direct (FillTracker) | 2 (PLTR, DIA) | 2 | +$39 |

---

## Cumulative Phase 2 Running Totals

| Session | Date | P&L | Trades | Wins | data_clean | Running P&L (Phase 2 only) |
|---------|------|-----|--------|------|------------|---------------------------|
| S6 | 2026-06-15 | +$35* | 3 | 2 | FALSE | +$35* |

*Final, broker-confirmed: XLE -$4 (actual fill) + PLTR +$24 (confirmed) + DIA +$15 (carryover, max_hold exit confirmed filled $1.95 at S7 startup 2026-06-16 09:46:58 ET, attributed to S6 per Phase 2 carryover handling).

**Phase 1 baseline (S1–S5, carried forward):** -$276.00, 14 trades, 2 wins

**Combined Phase 1 + Phase 2 (S6 final):**
- Total trades: 17 (14 P1 + 3 S6)
- Total wins: 4 (2 P1 + 2 S6)
- Net P&L: -$241.00 (-$276 + $35)
- Win rate: 23.5%

---

## Infrastructure Anomalies

### 1. DIA EOD Exit Order Unfilled (primary anomaly) — RESOLVED

EOD limit sell for DIA260618C00522000 submitted at 12:30:17 ET at limit $1.83. The order expired unfilled at 4:00 PM ET market close on 2026-06-15 (broker order `8d4e94db`, confirmed via order history). The session runner had recorded this trade as closed in the journal (status=closed, exit_price=1.83, pnl=+3.0) at time of order submission — standard behavior observed in all prior sessions, but incorrect since the order never filled.

The position remained open at the broker overnight and carried into Session 7. A defect in `SessionRecovery.recover()` (no mechanism to re-link a carried-over broker position to its original journal row) was identified and fixed — see FINAL RESOLUTION at top of report. At Session 7 startup (2026-06-16 09:46:39 ET) the carryover was correctly re-linked to journal row 35, the existing `max_hold` exit rule fired on schedule (no threshold/strategy changes), and the exit filled at $1.95, broker-confirmed at 09:46:58 ET, realized_pnl=+$15.00.

**This session remains data_clean=False** (as determined at original session end) — the XLE exit price discrepancy was also confirmed, and data_clean reflects the state at original session close, not subsequent cross-session resolution.

### 2. XLE Exit Price Discrepancy (corrected)

Session runner logged XLE exit at $0.36 (trailing stop limit price). Actual broker fill (Alpaca order `ee1fa8cd`) was **$0.38** — $0.02 better than limit, consistent with `OPTIONS_EXIT_LIMIT_PRICE_MODE=marketable_limit` behavior getting a better price than the limit.

**Correction applied:** trade_journal row 33 updated (exit_price: 0.36→0.38, realized_pnl: -6→-4). Ledger S6 entry corrected.

The session runner records the exit limit price as exit_price without waiting for actual fill confirmation. This systematically understates P&L when fills occur at better-than-limit prices. Flagged for Phase 2 tracking; a code fix (record actual fill price from broker confirmation callback) would require a targeted defect fix under Phase 2 rules.

### 3. First Direct Fills in Phase 2

PLTR and DIA entry orders filled directly via FillTracker (not Pattern A stale-cancel recovery). All 14 Phase 1 fills and the XLE S6 fill were Pattern A recovered. This gives initial data for Q7 (fill path comparison):
- Recovered (N=15): 2 wins, 12 losses, -$282 cumulative (Phase 1 + XLE S6)
- Direct (N=2): 2 wins, 0 losses, +$39 (PLTR + DIA, both broker-confirmed final)

(Insufficient for conclusions; N=2 direct is below the minimum-3 threshold.)

---

## Issues for Next Session

1. **Resolve DIA position** — DONE. Confirmed unfilled at 4 PM ET 2026-06-15; carried over as DTE-2 call into June 16; resolved at Session 7 startup via max_hold exit, broker-confirmed filled $1.95, +$15.00. See FINAL RESOLUTION above.

2. **Exit price recording bug** (Phase 2-eligible fix): Session runner records limit price as exit_price without waiting for actual broker fill price confirmation. XLE demonstrated a +$2 discrepancy this session. A targeted fix to update exit_price from the broker's `filled_avg_price` after exit confirmation would improve accounting accuracy. This qualifies under Phase 2 defect criteria ("incorrect accounting / incorrect trade recording").

3. **Acceptance criterion reminder**: No ORB trades in S6 (ORB slot reserved through 11:30 ET; no ORB signals fired). ORB N remains at 3 (Phase 1 only). Need ORB trades for Q1 data.
