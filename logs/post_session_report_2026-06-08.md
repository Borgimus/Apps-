# Post-Session Report — 2026-06-08

**Session:** Paper evaluation | PAPER_EVALUATION_MODE=true | LIVE_TRADING_ENABLED=false  
**Duration:** 09:31 – 12:30 ET (179 min)  
**Equity start:** $99,197.55 (paper account)  
**Cycles:** 352 (30-second poll)  
**Reconciler interval:** 10 min  
**Clean session #:** 2 of 5 required  
**Protocol version:** evaluation/post_fix_eval_protocol.md (frozen)

---

## Infrastructure Verification

| Check | Result |
|-------|--------|
| Session terminated ≤12:35 ET | PASS (actual: 12:30:25 ET, 25 sec past EOD) |
| Broker positions at EOD | 0 |
| Broker open orders at EOD | 0 |
| 403 errors on exit orders | None (api_errors=0) |
| daily_pnl vs sum of fills | MATCH (−$9.00 vs −$5.00 − $4.00 = −$9.00) |
| Every fill in ledger | PASS (2 closed trades, both present) |
| Bug E fix verification | PASS — loop exited cleanly at 12:30:25 ET |
| Bug D fix verification | N/A — no reconciler restores or stale exit scenario |
| Reconciler runs | 6 runs, 0 flagged, 0 repaired |
| Stale cancels | 0 |
| Duplicate exits | 0 |

---

## Pre-Session State

| Check | Result |
|-------|--------|
| LIVE_TRADING_ENABLED=false | PASS |
| PAPER_EVALUATION_MODE=true | PASS |
| Alpaca base URL | paper-api.alpaca.markets ✓ |
| entries_today at launch | 0 |
| pending_entries at launch | 0 |
| Open positions at launch | 0 |
| Open orders at launch | 0 |
| Prior session log | None (clean start) |

---

## Session Narrative

Market opened in a broad `low_volume_chop` regime. All 27 universe symbols rejected by scanner for the first **100 minutes** (9:31–10:31 ET, 9 consecutive 5-minute scan cycles). First scanner approval occurred at 10:31 ET (SMH, score=50, rsi_trend diagnostic). Market slowly developed uptrend; scanner began passing symbols at 11:07 ET with `orb_breakout + trend_up` reasons.

Both trades were directionally LONG (calls) — opposite of both prior clean sessions (which were SHORT/puts). This is the first uptrend session in the evaluation dataset.

---

## Trade Journal

| # | Symbol | Contract | Strategy | Entry | Exit | Fill (entry) | Fill (exit) | P&L | Exit Reason | Hold | Quality | Score | Age (min) | DTE | Entry Spr% | Exit Spr% | Universe | RVOL | Delta | IV |
|---|--------|----------|----------|-------|------|--------------|-------------|-----|-------------|------|---------|-------|-----------|-----|-----------|-----------|----------|------|-------|----|
| 416 | QQQ | QQQ260608C00730000 | vwap_reclaim | 11:12:22 | 11:22:47 | $0.20 | $0.15 | **−$5.00** | trailing_stop | 9.9 min | **3** | 70.0 | 92.3 | 0 | 4.7% | 6.5% | core_etfs | 0.501 | N/A | 0.0 |
| 597 | IWM | IWM260608C00287000 | vwap_reclaim | 11:59:00 | 12:02:02 | $0.20 | $0.16 | **−$4.00** | trailing_stop | 2.5 min | **3** | 58.0 | 113.9 | 0 | 4.7% | 6.1% | core_etfs | 0.503 | N/A | 0.0 |

Delta=N/A and IV=0.0: Alpaca paper broker does not provide greeks or IV.  
Both entry fills came in $0.02 below limit price (identical slippage pattern as prior sessions).

**Session P&L (actual broker fills):** −$9.00  
**Session P&L (journal):** −$9.00  
**Discrepancy:** $0.00

---

## Signal Bridge Summary

| Decision | Count | Top Block Reason |
|----------|-------|-----------------|
| traded | 2 | — |
| blocked | 190 | liquidity_filter_no_contract (190) |
| skipped | 345 | rsi_trend_diagnostic_only (232); cooldown_after_loss (112); pending_order_exists (1) |

**Bridge total entries:** 537  
**STANDBY period:** 9:31–10:31 ET (9 consecutive full-universe rejections — `low_volume_chop`)  
**ORB slot:** released at 11:30 ET; no ORB trades placed (all passing signals went via vwap_reclaim bridge)  
**Reconciler:** 6 runs, 0 clean/flagged, 0 repaired

---

## Trades by Quality Score

| Quality | N | Wins | P&L |
|---------|---|------|-----|
| 2 | 0 | — | — |
| 3 | 2 | 0 | −$9.00 |
| 4 | 0 | — | — |

**First quality=3 trades in the clean dataset.** Both were losses. Total clean-session quality=3 observations: 2 (minimum for conclusion: 3 — not yet reached).

## Trades by DTE

| DTE | N | Wins | P&L |
|-----|---|------|-----|
| 0 | 2 | 0 | −$9.00 |

All trades same-day expiry. No multi-day expirations.

## Trades by Universe Group

| Group | N | Wins | P&L |
|-------|---|------|-----|
| core_etfs | 2 | 0 | −$9.00 |
| mega_cap | 0 | — | — |
| liquid_growth | 0 | — | — |

## Trades by Strategy

| Strategy | N | Wins | P&L |
|----------|---|------|-----|
| orb | 0 | — | — |
| vwap_reclaim | 2 | 0 | −$9.00 |

No ORB trades this session — all passing signals routed through vwap_reclaim bridge.

## Trades by Direction

| Direction | N | Wins | P&L |
|-----------|---|------|-----|
| LONG (calls) | 2 | 0 | −$9.00 |
| SHORT (puts) | 0 | — | — |

**Note:** Sessions 1 and prior (all contaminated) were SHORT (puts). This is the first session with LONG (call) trades — market was in an uptrend per scanner reasons (`trend_up, orb_breakout`).

---

## Infrastructure Anomalies

### 1. ETF yfinance 404 errors (recurring, pre-existing)
Same 9 ETF symbols returning HTTP 404 from yfinance. Recurring across all sessions; not a blocker.

### 2. XLK persistent `liquidity_filter_no_contract` (~190 blocked events)
XLK passes the AlpacaConfirmer at scan time (e.g., `XLK260612C00115000, spread=1.76%`) but then fails `liquidity_filter_no_contract` when the bridge evaluates it (85 total_candidates checked, 0 pass). This discrepancy is persistent across Sessions 1 and 2. The Confirmer and the bridge-level LiquidityFilter appear to be filtering against different chains or strike/delta ranges. No action required under frozen protocol; logging as recurring infrastructure observation.

### 3. 9 consecutive STANDBY scans (100 min)
All 27 symbols rejected as `low_volume_chop` from 9:31 through 10:31 ET. Normal behavior for a low-conviction Monday open. Scanner cleared itself without intervention. No trades were missed — the signals that eventually passed appeared after momentum developed.

### 4. First LONG (call) session
Both trades were `signal_direction=long`, consistent with scanner `trend_up + orb_breakout`. This is the first time the evaluation dataset has LONG trades. Worth noting that even in an uptrend context, both calls lost via trailing_stop. Could indicate the trailing_stop parameter is too tight for low-priced 0-DTE contracts (entry at $0.20), or that the vwap_reclaim signal fired after most of the move already occurred (signal age 92 and 114 min respectively).

### 5. Signal age: 92 and 114 minutes
Both trades had signal age > 90 min. Per protocol Section 10, signal age >120 min is an open variable under observation. Trade 2 (IWM, 113.9 min) approaches the 120-min threshold. The prior contaminated-session observation was: "Signal age 120+ min: 0W/2, avg −$224.75." This session adds two more age>90 observations, both losses. Minimum N not yet reached; no conclusion drawn.

---

## Acceptance Criteria (Section 9 of protocol)

- [x] `LIVE_TRADING_ENABLED=false` confirmed in log
- [x] Session terminated at 12:30:25 ET (≤12:35 ET)
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

| Metric | Session 1 (2026-06-05) | Session 2 (2026-06-08) | Cumulative |
|--------|----------------------|----------------------|------------|
| Trades | 3 | 2 | 5 |
| Wins | 1 | 0 | 1 |
| Losses | 2 | 2 | 4 |
| Win rate | 33.3% | 0.0% | 20.0% |
| P&L | +$2.00 | −$9.00 | **−$7.00** |
| Strategy | ORB (1), VWAP (2) | VWAP (2) | ORB (1), VWAP (4) |
| Direction | SHORT (3) | LONG (2) | SHORT (3), LONG (2) |

**Clean sessions completed: 2/5. Need 3 more to trigger historical analysis.**

---

## Minimum Observations Progress (Clean Sessions Only)

| Dimension | Current clean obs | Min required | Status |
|-----------|------------------|--------------|--------|
| quality=2 | 3 (Session 1) | 3 | Reached (0 wins, no quality=3/4 to compare) |
| quality=3 | 2 (Session 2) | 3 | Not reached |
| quality=4 | 0 | 3 | Not reached |
| 0-DTE | 5 | 3 | Reached (1W/5, but no other DTE to compare) |
| core_etfs | 4 | 3 | Reached (1W/4) |
| liquid_growth | 1 | 3 | Not reached |
| ORB completed | 1 | 3 | Not reached |
| VWAP completed | 4 | 3 | Reached (1W/4) |
| Signal age <60 min | 1 | 3 | Not reached |
| Signal age 60–120 min | 4 | 3 | Reached (1W/4) |
| Signal age >120 min | 0 | 3 | Not reached |

No dimension has sufficient data for conclusions. Minimum ≥3 per bucket per compared dimension remains unmet across all key variables.

---

## Issues for Next Session

1. **XLK persistent `liquidity_filter_no_contract` mismatch.** The AlpacaConfirmer passes XLK but the bridge LiquidityFilter rejects all 85 candidates every cycle. Investigate whether the Confirmer's confirming contract is not in the bridge-level chain fetch, or whether delta range filtering is the cause. If this is a defect, document and fix. Do not change filter parameters.

2. **quality=3 observations: 2/3 minimum.** One more quality=3 trade needed before any quality dimension comparison is possible.

3. **No ORB trades in Session 2.** All qualified signals routed to vwap_reclaim. ORB strategy needs 3 more completions before comparison with VWAP.

4. **Both losses on LONG trades in uptrend.** Worth watching whether LONG (call) trades consistently underperform in this strategy setup. Not actionable until ≥3 LONG observations.

5. **Trailing stop on cheap 0-DTE contracts.** Both QQQ ($0.20 entry → $0.15 exit) and IWM ($0.20 entry → $0.16 exit) lost small amounts quickly. A $0.04–$0.05 adverse move triggered exit on a $0.20 contract (20–25% adverse). Could indicate trailing stop % is too tight for low-priced contracts, OR that the entry timing was poor (signals aged 92–114 min). Record for analysis at 5-session mark; no parameter changes.
