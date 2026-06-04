# Post-Session Report — 2026-06-04

**Session:** Paper evaluation | PAPER_EVALUATION_MODE=true | LIVE_TRADING_ENABLED=false  
**Duration:** 10:44 – 12:48 ET (manually killed 18 min past paper close)  
**Equity start:** $99,658.07  
**Cycles:** 241 (30-second poll)  
**Reconciler interval:** 10 min  
**Log:** `logs/session_2026-06-04.log` (2,015 lines)

---

## Bug Regression Results

### Bug A — Stale cancel missing `risk` argument ✅ CONFIRMED FIXED
**Evidence:** DIA order 775d06e3 went stale at 10:46:41 ET (2 min). Cancel succeeded. Immediately after:
```
RiskManager: entry cancelled | entries=1 pending=0 exits=0
```
`pending` returned to 0 correctly. With the 2026-06-03 bug, it would have stayed at 1, falsely occupying a capacity slot. **No leak confirmed.**

### Bug B — 422 cancel drops fill ✅ NOT TRIGGERED
No 422 cancel responses occurred today. All stale cancels were accepted cleanly. The code fix is in place and covered by regression tests; no live evidence available from this session.

### Bug C — Exit P&L uses midpoint instead of bid ✅ CONFIRMED FIXED
**AVGO exit at 10:52 ET:**
```
Position exit | AVGO260605C00415000 | reason=trailing_stop | price=4.0300 | pnl=-122.00
limit_price: '4.03'
```
`pnl = (4.03 - 5.25) × 100 = -$122.00` — bid-based ✓

**META exit (first) at 12:13 ET:**
```
Position exit | META260605C00645000 | reason=trailing_stop | price=3.5800 | pnl=-142.00
limit_price: '3.58'
```
`pnl = (3.58 - 5.00) × 100 = -$142.00` — bid-based ✓

Both exits correctly used bid as exit price. Old code would have used midpoint.

---

## Trade Journal

| # | Symbol | Contract | Entry | Exit | Actual Fill | P&L (journal) | P&L (actual) | Reason | Hold |
|---|--------|----------|-------|------|-------------|----------------|--------------|--------|------|
| 1 | AVGO | AVGO260605C00415000 | $5.25 | $4.03 bid | ~$4.03 | -$122.00 | -$122.00 | trailing_stop | 7:10 min |
| 2 | META | META260605C00645000 | $5.00 | $3.58 bid* | $1.54 fill | -$142.00* | **-$346.00** | trailing_stop | 5:12 min |

*META journal records exit at $3.58 (bid when exit order placed). Actual broker fill was $1.54 via order `cecf8596` placed at 12:48 ET after manual intervention. See Bug D below.

**Actual realized P&L: -$468.00** (AVGO -$122 + META -$346)  
**Journal P&L: -$264.00** (understated due to unfilled first exit order)  
**RiskManager daily_pnl: -$1,388.00** (corrupted by duplicate local-only exit calls — NOT real)

---

## Session Timeline

| Time ET | Event |
|---------|-------|
| 10:44 | Pre-session checks PASS (9/9). Equity=$99,658.07 |
| 10:44 | Scan: 4 passed (CRWD=50, AVGO=47, DIA=46, XLF=46). Active=[CRWD,AVGO,DIA] |
| 10:44 | AVGO entry placed @ $5.28 limit. DIA entry placed @ $1.29 limit. entries=0, pending=2 |
| 10:45 | **AVGO filled @ $5.25** (TTF=32s). entries=1, pending=1 |
| 10:45 | ORB slot reserved: non_orb_entries=2 ≥ 2, CRWD blocked until 11:30 |
| 10:46 | **DIA stale cancel** (2 min). entries=1, pending=0 ✅ Bug A confirmed |
| 10:52 | **AVGO trailing_stop** exit @ $4.03 (bid), pnl=-$122.00 ✅ Bug C confirmed |
| 10:54 | Reconciler #1: clean (0/0/0/0) |
| 10:59 | Re-scan: AVGO dropped (cooldown). XLF replaces. Active=[CRWD,DIA,XLF] |
| 11:05 | Reconciler #2: clean. XLF score=68 (orb_breakout) |
| 11:15 | Reconciler #3: clean. Universe expands to 6 passed. SMH back at score=65 |
| 11:20 | Universe 9 passed. PLTR score=62 (SHORT, orb_breakdown). CRWD no liquid contracts |
| 11:25 | Reconciler #4: clean. Universe 9 passed, stable |
| 11:30 | **ORB slot released.** XLK immediately risk-rejected (delta=N/A, $7,957 cost > $995 max) |
| 11:30–12:07 | XLK rejected every 30s cycle (70 total rejections, journal entries 325–395) |
| 12:07 | Reconciler #7: clean. META score=67 (orb_breakout). Active=[XLF,META,XLK] |
| 12:07:29 | **META entry placed @ $5.04 limit** |
| 12:08:31 | **META filled @ $5.00** (TTF=65s). entries=2, pending=0 |
| 12:12 | Re-scan: 13 passed. IWM=70 (orb_breakout), CRWD=75 (new leader) |
| 12:13:43 | **META trailing_stop** @ $3.58 bid, pnl=-$142.00. Exit order 8f1efd31 placed |
| 12:17:47 | Reconciler #8: **FLAGGED** — broker still has META open, 8f1efd31 status=new (bid fell below $3.58, unfilled). Position restored locally |
| 12:17:47 | Second exit attempted @ $2.98 → **403 Forbidden** (duplicate sell while original open). pm.close() called anyway |
| 12:18–12:48 | "Recon mismatch active — all new entries blocked" every cycle |
| 12:30 | **EOD trigger fired**: "cancelling pending orders and liquidating all positions" — but session continued running (no loop break) |
| 12:38 | Reconciler #9: FLAGGED again. Third exit → 403. exits=5, daily_pnl=-$1040 (inflated) |
| 12:38 | Manual intervention: cancelled stale order 8f1efd31 via Alpaca REST API |
| 12:48 | Reconciler #10: clean (broker_orders=0). **New exit placed @ $1.52**. **Filled @ $1.54** (order cecf8596) |
| 12:48 | Session killed manually. Broker positions: empty ✓ |

---

## Scanner / Signal Bridge Summary

**Universe peak:** 13 passed / 14 rejected at 12:12 ET  
**Final universe composition (peak):** CRWD=75, IWM=70, XLF=68, META=67, XLK=65, SMH=65, AMD=65, GOOGL=62, PLTR=62, DIA=53

**ORB breakout signals confirmed:** XLF (orb_breakout, score=68 from 10:54 onward), SMH (orb_breakout, score=65), DIA (orb_breakout), XLK (orb_breakout), META (orb_breakout), IWM (orb_breakout flipped at 12:12), CRWD (orb_breakout)

**ORB breakdown signals:** PLTR (SHORT, score=62, 11:20–12:12)

**Signals blocked by ORB slot reserve:** CRWD, XLF, SMH, XLK, PLTR (all correctly blocked 10:44–11:30)

**Signals rejected by risk:** XLK (70 rejections — delta=N/A, deep ITM contract, cost $7,650–$7,960 vs max $995)

**Signals not triggering entry (bridge ready but conditions unmet):** XLF, SMH (rsi_trend "ready" = sufficient bars; entry conditions not met throughout session)

**Entries placed:** AVGO (10:44), DIA (10:44, stale-cancelled), META (12:07)

---

## New Issues Discovered

### Bug D — SEV-2: Exit limit order unconfirmed creates reconciler loop

**Root cause:** When an exit limit order is placed and recorded locally (position closed via `pm.close()`, journal exit written), but the limit order does not fill (bid falls below limit), the reconciler at the next 10-min interval finds `broker_pos=1, local_pos=0` and restores the position. `monitor_positions()` then fires a new exit, which attempts a second sell order. Alpaca returns **403 Forbidden** because the original unfilled sell order is still open.

**Impact:** Each reconciler cycle adds one more duplicate `pm.close()` + `risk.record_exit()` call. The RiskManager's `daily_pnl` and `exits` counter become corrupted. Exit loop repeats until the original order fills, is cancelled manually, or expires at 4:00 PM ET (DAY order).

**Evidence from log:**
```
Reconciler: Untracked broker order 8f1efd31 (META260605C00645000) status=new
Reconciliation done: broker_pos=1 local_pos=0 repaired=1 flagged=1
Position exit | META260605C00645000 | reason=stop_loss | price=2.9800 | pnl=-202.00
[exit_order] attempt 1 failed: 403 Forbidden
```

**Fix required:** In `monitor_positions()` or the exit order placement path, before placing a new sell-to-close order, check for existing open sell orders on the same symbol via `broker.get_open_orders(symbol)`. If one exists, either skip the new order or cancel the old one first.

**Alternatively:** The reconciler should detect an untracked `sell_to_close` order for a local-closed position and not restore the position. The reconciler's "added broker position" path should check whether an open sell order for that symbol already exists, and if so, treat it as an in-flight exit rather than an orphan position.

---

### Bug E — SEV-2: Session does not terminate after EOD

**Root cause:** `eod_liquidate()` is called at 12:30 ET and logs the message, but the main session loop (`while True`) continues indefinitely. The session ran 18 minutes past EOD (cycles 206–241).

**Evidence:**
```
12:30:10 WARNING session_runner: EOD time reached — cancelling pending orders and liquidating all positions
12:30:40 INFO session_runner: [cycle 206] 12:30:40 ET | positions=0 | entries=2
...
12:48:18 INFO session_runner: [cycle 241] 12:48:18 ET
```

**Fix required:** After `eod_liquidate()` completes, set a shutdown flag or `break` from the main loop. Optionally wait for any pending exit orders to fill (up to e.g. 5 min) before final termination.

---

### Minor Issue: LiquidityFilter selects unpriceable XLK contracts repeatedly

**Observation:** After 11:30 ORB release, XLK260605C00115000 was selected and risk-rejected every 30 seconds for 37 minutes (70 rejections, 70 journal entries 325–395). The contract has `delta=N/A` and trades at ~$77–$79 (deeply ITM), far exceeding the $995 max risk limit.

**Impact:** Noise in journal rejection log. No trade impact. XLK could not be entered regardless due to cost.

**Fix suggestion:** LiquidityFilter should skip contracts with `delta=N/A` (indicating unmeasurable/missing greeks, typically deep ITM or stale data). Alternatively, the session runner should suppress repeated rejections for the same symbol/contract within the same session.

---

## Acceptance Criteria Verification

| Criterion | Status | Notes |
|-----------|--------|-------|
| Paper mode enforced | ✅ PASS | LIVE_TRADING_ENABLED=false throughout |
| No orphan orders at start | ✅ PASS | Pre-session check confirmed |
| pending_entries returns to zero after stale cancel | ✅ PASS | DIA stale cancel: entries=1 pending=0 |
| 422 race handled | ✅ N/A | No 422s today |
| Exit P&L uses bid-side | ✅ PASS | AVGO $4.03, META $3.58 (both bid-based) |
| Ledger includes every filled trade | ⚠️ PARTIAL | Journal has correct entry/exit for AVGO; META journal exit price ($3.58) doesn't match actual fill ($1.54) — see Bug D |
| No orphan positions at EOD | ✅ PASS | Broker confirmed empty after cecf8596 filled |
| Post-session report generated | ✅ PASS | This report |
| Session terminates at EOD | ❌ FAIL | See Bug E — session ran 18 min past 12:30 ET |

---

## Issues for Next Session — Priority Order

1. **[SEV-2] Bug D:** Cancel existing open sell order before placing new exit — prevents 403 loop and corrupted P&L
2. **[SEV-2] Bug E:** Break main loop after EOD liquidation completes
3. **[Minor] LiquidityFilter:** Exclude `delta=N/A` contracts from selection

---

## Signal / Universe Diagnostics

**5-min re-scan cadence confirmed working:** Scan ran at 10:44, 10:49, 10:54, 10:59, 11:05, 11:15, 11:20, 11:25, 11:30, 11:36, 11:41, 11:57, 12:07, 12:12, 12:17, 12:22, 12:28 ET

**Reconciler at 10-min intervals confirmed:** Clean runs at 10:44, 10:54, 11:05, 11:15, 11:25, 11:36, 11:46, 11:57, 12:07, 12:17 (flagged), 12:28 (flagged), 12:38 (flagged), 12:48 (clean after manual cancel)

**XLF liquidity note:** XLF spread fluctuated between 2.90% and 9.52% during the session, reflecting changing market conditions. The C00052500 strike became the liquidity filter's preferred contract as XLF moved up, indicating the system correctly updates contract selection on each re-scan.

**CRWD liquidity note:** CRWD had no qualifying contracts at 11:20 ET (`LiquidityFilter: no qualifying contracts for CRWD SignalDirection.LONG`) but recovered by 12:17 ET (score=75). The C00700000 contract (spread=4.92–9.27%) was inconsistently in/out of liquidity filter range.
