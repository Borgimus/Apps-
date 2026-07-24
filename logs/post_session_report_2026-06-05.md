# Post-Session Report — 2026-06-05

**Session:** Paper evaluation | PAPER_EVALUATION_MODE=true | LIVE_TRADING_ENABLED=false  
**Duration:** 11:04 – 12:30 ET (86 min)  
**Equity start:** ~$99,500 (paper account)  
**Cycles:** 170 (30-second poll)  
**Reconciler interval:** 10 min  
**Clean session #:** 1 of 5 required

---

## Infrastructure Verification

| Check | Result |
|-------|--------|
| Session terminated ≤12:35 ET | PASS (actual: 12:30:27 ET, 27 sec past EOD) |
| Broker positions at EOD | 0 |
| Broker open orders at EOD | 0 |
| 403 errors on exit orders | None (api_errors=0) |
| daily_pnl vs sum of fills | MATCH (+$2.00 vs +$13−$3−$8 = +$2.00) |
| Every fill in ledger | PASS (3 closed trades, all present) |
| Bug E fix verification | PASS — loop exited immediately after EOD, no excess cycles |
| Bug D fix verification | N/A — no reconciler restores occurred; no stale exit scenario triggered |

---

## Trade Journal

| # | Symbol | Contract | Strategy | Entry | Exit | Fill (entry) | Fill (exit) | P&L (actual) | Exit Reason | Hold | Quality | Score | Age (min) | DTE | Entry Spr% | Exit Spr% | Universe | Delta | IV |
|---|--------|----------|----------|-------|------|--------------|-------------|--------------|-------------|------|---------|-------|-----------|-----|-----------|-----------|----------|-------|----|
| 396 | MSTR | MSTR260605P00119000 | orb | 11:04:06 | 11:20:25 | $1.18 | $1.31 | **+$13.00** | trailing_stop | 15.8 min | 2 | 52.0 | 64.1 | 0 | 8.8% | 12.9%* | liquid_growth | N/A | 0.0 |
| 397 | COIN | COIN260605P00152500 | orb | 11:09:13 | — | — | — | $0 | stale_cancelled | — | 2 | 40.0 | 74.2 | 0 | 3.5% | — | liquid_growth | N/A | 0.0 |
| 398 | QQQ | QQQ260605P00708000 | vwap_reclaim | 11:24:27 | 11:25:32 | $0.10 | $0.07 | **−$3.00** | trailing_stop | 0.5 min | 2 | 62.0 | 64.5 | 0 | 8.7% | 13.3%* | core_etfs | N/A | 0.0 |
| 432 | IWM | IWM260605P00285000 | vwap_reclaim | 11:40:53 | 11:43:56 | $0.56 | $0.48 | **−$8.00** | trailing_stop | 2.5 min | 2 | 55.0 | 40.9 | 0 | 1.7% | 4.1% | core_etfs | N/A | 0.0 |

*Exit spread exceeded max_spread_pct=10% — exits still executed per marketable_limit mode.  
Delta=N/A and IV=0.0: Alpaca paper broker does not provide greeks or IV.  
COIN (id 397) submitted but stale_cancelled before fill; not counted in closed trades.

**Session P&L (actual broker fills):** +$2.00  
**Session P&L (journal):** +$2.00  
**Discrepancy:** $0.00

### Entry slippage
- QQQ: limit $0.12, filled $0.10 → −$0.02 slippage
- IWM: limit $0.58, filled $0.56 → −$0.02 slippage
- MSTR: limit $1.18, filled $1.18 → $0.00 slippage

---

## Signal Bridge Summary

| Decision | Count | Top Block Reason |
|----------|-------|-----------------|
| traded | 4 | — |
| blocked | 30 | risk: Max entries per day reached: 3/3 (29); liquidity_filter_no_contract (1) |
| skipped | 723 | rsi_trend_diagnostic_only (690); cooldown_after_loss (32); pending_order_exists (1) |

**ORB slot:** Not applicable this session (all 3 entry slots consumed before ORB reserve triggered)  
**Reconciler:** 6 runs across session, 0 flagged, 0 repaired (reconciliation_warnings=[])  
**Bridge total entries:** 757

---

## Trades by Quality Score

| Quality | N | Wins | P&L |
|---------|---|------|-----|
| 2 | 3 | 1 | +$2.00 |
| 3 | 0 | — | — |
| 4 | 0 | — | — |

All three trades this session were quality=2. No quality=3 or quality=4 signals produced.  
Quality dimension still below minimum observations threshold (need ≥3 per level).

## Trades by DTE

| DTE | N | Wins | P&L |
|-----|---|------|-----|
| 0 | 3 | 1 | +$2.00 |

All trades expired same day. No multi-day expirations this session.

## Trades by Universe Group

| Group | N | Wins | P&L |
|-------|---|------|-----|
| liquid_growth | 1 | 1 | +$13.00 |
| core_etfs | 2 | 0 | −$11.00 |
| mega_cap | 0 | — | — |

## Trades by Strategy

| Strategy | N | Wins | P&L |
|----------|---|------|-----|
| orb | 1 | 1 | +$13.00 |
| vwap_reclaim | 2 | 0 | −$11.00 |

First ORB completion in the evaluation dataset. Strategy breakdown now:
- ORB: 1 trade, 1 win (100%)  
- VWAP: 2 trades, 0 wins (0%)

---

## Infrastructure Anomalies

### 1. ETF yfinance 404 errors (recurring, pre-existing)
9 ETF symbols return HTTP 404 from yfinance "No fundamentals data found" during universe scan: XLK, XLE, XLY, XLF, DIA, SPY, QQQ, IWM, SMH. Scanner handles gracefully by scoring fundamentals=0. Trading not affected; ETFs still approved via Alpaca liquidity confirmer. First observed this session; same behavior expected in future sessions. Not a blocker.

### 2. XLK LiquidityFilter: no qualifying contracts
XLK signal was rejected by `liquidity_filter_no_contract` (1 occurrence). With the deep-ITM cost cap fix active, XLK's deep-ITM contracts with `delta=None` and `ask×100 > max_contract_cost` are correctly filtered. No ghost-rejection cycle observed (previous XLK noise from Bug E session is now silenced).

### 3. Exit spread warnings (2 occurrences)
- MSTR exit spread 12.9% > 10% threshold
- QQQ exit spread 13.3% > 10% threshold  
Both exits executed per `marketable_limit` mode. Exit spread > entry spread suggests bid/ask widened after entry (consistent with end-of-day 0-DTE behavior). Noted; not a defect.

### 4. QQQ hold = 31 seconds (very brief)
QQQ trailing_stop triggered in 31 seconds after fill. Possible cause: deep OTM put (strike 708 vs spot 726.1 = 2.5% OTM), tight absolute price ($0.07 exit vs $0.10 entry), trailing stop hit almost immediately on a small bid move. Loss limited to $3.00. No defect.

### 5. Ledger `data_clean` deserialization fix
The `LedgerEntry` dataclass did not accept `data_clean` as a field. Added `data_clean: bool = False` to the dataclass to fix deserialization of historical entries. All 9 prior session entries now load correctly alongside new 2026-06-05 clean session entry.

---

## Acceptance Criteria (Section 9 of protocol)

- [x] `LIVE_TRADING_ENABLED=false` confirmed in log
- [x] Session terminated at 12:30:27 ET (≤12:35 ET)
- [x] Broker positions at EOD = 0
- [x] Broker open orders at EOD = 0
- [x] No 403 Forbidden on exit orders
- [x] daily_pnl matches sum of fills (±$0, well within $5 tolerance)
- [x] Every closed trade has verified fill price from Alpaca
- [x] Journal exit_price matches Alpaca filled_avg_price for all exits
- [x] Post-session report filed
- [x] Ledger updated with data_clean=true

**Classification: data_clean = TRUE** — all acceptance criteria pass.

---

## Clean Session Dataset Status

| Metric | Value |
|--------|-------|
| Clean sessions completed | 1 |
| Clean sessions required for analysis | 5 |
| Sessions remaining | 4 |
| Clean session trades | 3 |
| Clean session P&L | +$2.00 |
| Clean session wins | 1 |
| Clean session win rate | 33.3% |

---

## Issues for Next Session

1. **No quality=3/4 signals observed (2 sessions running).** All 3 fills were quality=2. Monitor whether quality=3/4 signals are being generated but blocked, or simply not scoring that high under current conditions.

2. **All trades 0-DTE.** In-session exits mean no holding overnight. 0-DTE biases toward larger spreads at exit (observed 12.9% and 13.3%). No fix needed; just note for analysis.

3. **COIN stale_cancel (recurring).** COIN was submitted then stale_cancelled again (same as prior sessions). Watch for pattern — if COIN consistently stale_cancels, it may indicate a timing issue between order submission and fill polling for volatile symbols.

4. **rsi_trend diagnostic mode (690 skips).** AVGO, TSLA, NVDA and others continue generating signals but are processed in `rsi_trend_diagnostic_only` mode. No action needed per protocol.

5. **Exit spread widening at 0-DTE close.** QQQ and MSTR exits both had spread > 10% at exit. Consider whether 0-DTE positions should have a dedicated wider exit spread tolerance parameter. Do not change until ≥5 clean sessions accumulated.
