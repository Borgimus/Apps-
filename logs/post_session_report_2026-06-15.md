# Post-Session Report — 2026-06-15 (Session 6 of 15)

**Protocol:** Phase 2 Clean Data Collection (frozen config)
**Session Date:** 2026-06-15 (Monday)
**Session Start:** 09:37:06 ET
**Session End:** 12:30:18 ET
**Total Cycles:** 342
**data_clean:** FALSE — DIA exit order unfilled at EOD (position open at broker); XLE exit price discrepancy

---

## Session Result

| Metric | Value |
|--------|-------|
| Session P&L (journal) | +$21.00 |
| Session P&L (confirmed fills) | +$20.00 (XLE -$4 actual + PLTR +$24; DIA pending) |
| Session P&L (actual fills incl. journal DIA) | +$23.00 (XLE -$4 + PLTR +$24 + DIA +$3 journal) |
| Trades | 3 (1 recovered / 2 direct) |
| Wins | 2 (PLTR +$24, DIA +$3 provisional) |
| Losses | 1 (XLE -$4 actual / -$6 journal) |
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
| 35 | DIA | DIA260618C00522000 | vwap_reclaim | 11:48:54 | pending | $1.80 | $1.83† | +$3† | eod_exit | 40.8 min | 2 | 58 | 73.9 | 3 | 8.6% | n/a | core_etfs (ETF) | direct |

*XLE: journal exit_price=0.36 (limit), actual broker fill=0.38. Journal corrected; realized_pnl updated to -$4. See discrepancy note.  
†DIA: EOD limit sell at $1.83 submitted 12:30 ET, order status=new at broker as of report time (12:43 ET, bid ~$1.64). Journal and health report recorded $1.83/+$3.00 assuming fill; not yet confirmed. Day order remains valid until 4 PM ET.

**Session P&L (actual confirmed fills):** +$20.00 (XLE -$4, PLTR +$24)
**Session P&L (journal, incl. DIA at limit):** +$21.00
**Session P&L (corrected entry + DIA at limit):** +$23.00
**Discrepancy — XLE:** journal -$6 vs actual -$4 (+$2 in favor; journal recorded limit, broker filled better)
**Discrepancy — DIA:** journal closed at $1.83 (+$3), broker position still open (order not filled as of 12:43 ET)

---

## Infrastructure Verification

| Check | Result |
|-------|--------|
| Session terminated ≤12:35 ET | PASS (actual: 12:30:18 ET) |
| Broker positions at EOD | FAIL — DIA260618C00522000 qty=1 still open |
| Broker open orders at EOD | FAIL — DIA260618C00522000 sell limit $1.83 status=new |
| 403 errors on exit orders | None |
| daily_pnl vs sum of confirmed fills | MISMATCH — session runner reports $21, confirmed fills $20 (XLE -$4 + PLTR +$24), DIA TBD |
| Every fill in ledger | PASS (3 trades recorded) |
| Reconciler: repaired / flagged | 1 / 0 (XLE at 10:58 ET) |
| Journal rows: closed / cancelled / rejected | 3 / 0 / 0 |
| XLE exit_price journal vs broker | MISMATCH — journal $0.36, broker fill $0.38 (+$2); corrected in DB and ledger |
| DIA exit_price journal vs broker | UNCONFIRMED — journal $1.83 (limit), broker order unfilled |
| LIVE_TRADING_ENABLED | false ✓ |

**data_clean determination:** FALSE

Failing criteria:
1. Broker positions at EOD ≠ 0 (DIA open)
2. Journal exit_price vs Alpaca mismatch: XLE ($0.36 journal / $0.38 actual) — corrected post-session
3. DIA exit unconfirmed — cannot verify "every status=closed trade has verified fill price"

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
| vwap_reclaim | 3 | 2 | +$23 (confirmed+journal) |
| orb | 0 | — | — |

### By Quality Score

| Quality | N | Wins | P&L |
|---------|---|------|-----|
| 2 | 2 | 1 (DIA) | -$4 + $3 = -$1 |
| 3 | 1 | 1 (PLTR) | +$24 |

### By DTE

| DTE | N | Wins | P&L |
|-----|---|------|-----|
| 3 | 3 | 2 | +$23 |

(All three trades: June 18 expiry from June 15 session date = DTE 3)

### By Signal Age

| Age Bucket | N | Wins | P&L |
|------------|---|------|-----|
| 30–60 min | 1 (XLE, 37.9 min) | 0 | -$4 |
| 60–90 min | 1 (DIA, 73.9 min) | 1 | +$3 |
| 90–120 min | 1 (PLTR, 108.6 min) | 1 | +$24 |

### By Asset Class

| Class | N | Wins | P&L |
|-------|---|------|-----|
| ETF (core_etfs) | 2 (XLE, DIA) | 1 (DIA) | -$1 |
| Single-stock (liquid_growth) | 1 (PLTR) | 1 | +$24 |

### By Scanner Score

| Score Bucket | N | Wins | P&L |
|-------------|---|------|-----|
| 50–59 | 2 (PLTR=55, DIA=58) | 2 | +$27 |
| 60–69 | 1 (XLE=67) | 0 | -$4 |

### By Fill Path

| Path | N | Wins | P&L |
|------|---|------|-----|
| recovered (Pattern A) | 1 (XLE) | 0 | -$4 |
| direct (FillTracker) | 2 (PLTR, DIA) | 2 | +$27 |

---

## Cumulative Phase 2 Running Totals

| Session | Date | P&L | Trades | Wins | data_clean | Running P&L (Phase 2 only) |
|---------|------|-----|--------|------|------------|---------------------------|
| S6 | 2026-06-15 | +$23* | 3 | 2 | FALSE | +$23* |

*Using actual XLE fill (-$4) + confirmed PLTR (+$24) + provisional DIA (+$3 journal). If DIA fills at $1.83 confirmed, total = +$23. If DIA expires or sells at lower price, total will differ.

**Phase 1 baseline (S1–S5, carried forward):** -$276.00, 14 trades, 2 wins

**Combined Phase 1 + Phase 2 (S6 provisional):**
- Total trades: 17 (14 P1 + 3 S6)
- Total wins: 4 (2 P1 + 2 S6)
- Net P&L: -$253.00 (-$276 + $23)
- Win rate: 23.5%

---

## Infrastructure Anomalies

### 1. DIA EOD Exit Order Unfilled (primary anomaly)

EOD limit sell for DIA260618C00522000 submitted at 12:30:17 ET at limit $1.83. As of 12:43 ET (report time), broker order status=new and position still open. Market value of DIA call is ~$1.64 vs limit $1.83.

The session runner recorded this trade as closed in the journal (status=closed, exit_price=1.83, pnl=+3.0) at time of order submission — standard behavior observed in all prior sessions. The day order remains valid until 4 PM ET.

**Resolution options (requiring user decision):**
- Wait for potential fill before 4 PM ET (DIA would need to rally ~$0.19 above current level)
- Cancel limit and submit market order before 4 PM ET to close at current bid
- Allow day order to expire; position carries to next session (June 18 expiry, DTE becomes 2)

**This session will remain data_clean=False regardless of DIA resolution** (XLE exit price discrepancy was also confirmed).

### 2. XLE Exit Price Discrepancy (corrected)

Session runner logged XLE exit at $0.36 (trailing stop limit price). Actual broker fill (Alpaca order `ee1fa8cd`) was **$0.38** — $0.02 better than limit, consistent with `OPTIONS_EXIT_LIMIT_PRICE_MODE=marketable_limit` behavior getting a better price than the limit.

**Correction applied:** trade_journal row 33 updated (exit_price: 0.36→0.38, realized_pnl: -6→-4). Ledger S6 entry corrected.

The session runner records the exit limit price as exit_price without waiting for actual fill confirmation. This systematically understates P&L when fills occur at better-than-limit prices. Flagged for Phase 2 tracking; a code fix (record actual fill price from broker confirmation callback) would require a targeted defect fix under Phase 2 rules.

### 3. First Direct Fills in Phase 2

PLTR and DIA entry orders filled directly via FillTracker (not Pattern A stale-cancel recovery). All 14 Phase 1 fills and the XLE S6 fill were Pattern A recovered. This gives initial data for Q7 (fill path comparison):
- Recovered (N=15): 2 wins, 12 losses, -$282 cumulative (Phase 1 + XLE S6)
- Direct (N=2): 2 wins, 0 losses, +$27 (PLTR + DIA provisional)

(Insufficient for conclusions; N=2 direct is below the minimum-3 threshold.)

---

## Issues for Next Session

1. **Resolve DIA position** (user decision required): Check broker after 4 PM ET for DIA fill status. If unfilled, position carries as DTE-2 call into June 16.

2. **Exit price recording bug** (Phase 2-eligible fix): Session runner records limit price as exit_price without waiting for actual broker fill price confirmation. XLE demonstrated a +$2 discrepancy this session. A targeted fix to update exit_price from the broker's `filled_avg_price` after exit confirmation would improve accounting accuracy. This qualifies under Phase 2 defect criteria ("incorrect accounting / incorrect trade recording").

3. **Acceptance criterion reminder**: No ORB trades in S6 (ORB slot reserved through 11:30 ET; no ORB signals fired). ORB N remains at 3 (Phase 1 only). Need ORB trades for Q1 data.
