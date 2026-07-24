# Post-Session Report: 2026-06-03 (Wednesday)
## Research Focus: 5-Minute Scanner Cadence vs 30-Minute Baseline

---

## Session Configuration

| Parameter | Value |
|---|---|
| Mode | PAPER_EVALUATION (non-live) |
| PAPER_EVAL_PERMISSIVE_ENTRY_MODE | true |
| LIVE_TRADING_ENABLED | false |
| UNIVERSE_SCAN_INTERVAL_MINUTES | **5 (under test)** |
| UNIVERSE_GROUPS_ENABLED | core_etfs, mega_cap, liquid_growth |
| RISK_MAX_TRADES_PER_DAY | 3 |
| MAX_ACTIVE_POSITIONS | 2 |
| MAX_SYMBOLS_TRADED_PER_DAY | 3 |
| MAX_CONTRACTS_PER_POSITION | 1 |
| OPTIONS_ENTRY_LIMIT_PRICE_MODE | marketable_limit (+1% above ask) |
| POSITION_EOD_EXIT_TIME | 12:30 ET |
| Poll interval | 30s |
| Reconcile interval | 30 min |
| Session start | ~10:30 ET |
| Session end | 12:30 ET (EOD) + cleanup |

---

## Scanner Cadence Analysis

### Overview

| Metric | Value |
|---|---|
| Total scan cycles | 25 |
| STANDBY scans (0 passed) | 4 (10:31–10:46 ET) |
| Active scans | 21 (10:51–12:28 ET) |
| STANDBY duration | 10:31–10:51 ET = **20 minutes** |
| Market open to first ACTIVE scan | 81 minutes (9:30 open → 10:51 ET) |
| Scan cadence | every 5 minutes |

### Scan Pass Progression (All 25 Cycles)

| ET Time | Passed | / Total | Key Event |
|---|---|---|---|
| 10:31 | 0 | 27 | STANDBY — all rvol < 0.5 or score < 40 |
| 10:36 | 0 | 27 | STANDBY |
| 10:41 | 0 | 27 | STANDBY |
| 10:46 | 0 | 27 | STANDBY (META rvol=0.496, just under threshold) |
| 10:51 | 1 | 27 | **META first pass** (rvol=0.511) |
| 10:56 | 1 | 27 | META only |
| 11:01 | 2 | 27 | **GOOGL first pass** (rvol=0.705, score=40) |
| 11:06 | 2 | 27 | META + GOOGL |
| 11:11 | 2 | 27 | META + GOOGL |
| 11:16 | 2 | 27 | META + GOOGL |
| 11:21 | 2 | 27 | META + GOOGL |
| 11:26 | 3 | 27 | **SMH first pass** (rvol=0.502); vwap_reclaim → order attempt |
| 11:32 | 3 | 27 | META + GOOGL + SMH |
| 11:37 | 4 | 27 | **AMZN first pass** (rvol=0.501); ORB → order placed + filled |
| 11:42 | 5 | 27 | META dropped (score 67→45); AMZN + GOOGL + SMH |
| 11:47 | 4 | 27 | META back (score 67); META + AMZN + GOOGL |
| 11:52 | 6 | 27 | |
| 11:57 | 6 | 27 | |
| 12:02 | 7 | 27 | **MSTR first pass**; reconcile detects AMZN fill; AMZN trailing stop |
| 12:07 | 7 | 27 | Cooldown active |
| 12:12 | 7 | 27 | Cooldown active |
| 12:17 | 9 | 27 | Cooldown expired; **META vwap_reclaim** → order placed |
| 12:23 | 11 | 27 | **COIN first pass**; blocked (max entries) |
| 12:28 | 12 | 27 | **NVDA** (score=62) and MSFT (55) pass for first time |

### STANDBY Exit: 5-Minute Cadence Benefit

**With 5-minute cadence:**
- META rvol crossed 0.5 threshold between 10:46 (0.496) and 10:51 (0.511)
- STANDBY exited at **10:51 ET** — detected within 5 minutes of threshold crossing

**With 30-minute cadence (hypothetical):**
- At 10:31 ET: META rvol=0.442, STANDBY
- Next 30-min scan would be at 11:01 ET
- META would not have been detected until **11:01 ET** (10 minutes later)

**5-minute cadence benefit: ~10 minutes earlier STANDBY exit**

### Signal Age Distribution (Bridge Entries)

| Strategy | Symbol | Age at First Detection | Significance |
|---|---|---|---|
| rsi_trend (diag) | META | 81 min (at first active scan 10:51) | Signal from ~9:30 ET (market open) |
| rsi_trend (diag) | META | 81–170 min (136 stubs) | Stale throughout; Q=0.0; never tradeable |
| rsi_trend (diag) | GOOGL | Active from 11:01, no bridge stubs | rsi_trend never fired for GOOGL (no fresh RSI cross) |
| vwap_reclaim | SMH | 87 min | SMH VWAP event at ~10:00 ET (pre-market?) |
| orb | AMZN | **82 min** | ORB breakdown at ~10:15 ET; detected fresh at 11:37 ET |
| vwap_reclaim | AMZN | 112 min | VWAP reclaim detected simultaneously |
| vwap_reclaim | META | **10 min** | Fresh VWAP breakdown at ~12:10 ET; detected 12:20 ET |
| orb | COIN | 153–158 min | Stale ORB; blocked by risk limit |
| vwap_reclaim | NVDA | 33–35 min | Near-fresh; blocked by risk limit |
| rsi_trend (diag) | MSFT | 2948–2965 min | ~49 hour old signal (yesterday/pre-market) |

### Age Bucket Summary

| Age Bucket | Count | % | Note |
|---|---|---|---|
| < 15 min (fresh) | 2 | ~1% | META vwap_reclaim (traded), NVDA vwap_reclaim (blocked) |
| 15–30 min | 0 | 0% | — |
| 30–60 min | 6 | ~4% | NVDA vwap_reclaim entries |
| 60–120 min | 6 | ~4% | AMZN orb+vwap, SMH vwap, COIN orb (partial) |
| > 120 min (stale) | 151 | ~91% | META rsi_trend (136), MSFT rsi_trend (8), COIN orb (7+) |

**Interpretation:** The vast majority of bridge entries (91%) are >120 min stale — primarily rsi_trend diagnostic stubs from open-bar signals. Only 2 entries were "fresh" (<15 min): META vwap at 12:20 ET (traded) and NVDA at 12:28 ET (blocked).

### Comparison: 5-min vs 30-min Cadence

| Dimension | 5-min (today) | 30-min (hypothetical) |
|---|---|---|
| STANDBY exit | 10:51 ET | 11:01 ET |
| Benefit | 10 min earlier | baseline |
| First AMZN detection | 11:37 (rvol 0.501) | 11:31 or 11:37 (same or 1 scan gap) |
| First MSTR detection | 12:02 ET | 12:01 ET (similar) |
| GOOGL delay | score-driven (cadence irrelevant) | same |
| SMH detection | 11:26 ET | 11:31 ET (~5 min later) |
| Extra scan data | 15 scans vs ~5 in same window | 5 scans |

**Conclusion:** 5-minute cadence provides meaningful benefit for rvol-gated STANDBY exit (~10 min), minor benefit for marginal rvol symbols (SMH: ~5 min), and no benefit for score-gated symbols (GOOGL: delay was score=22-32, cadence irrelevant).

---

## Delay Cause Classification

| Symbol | Primary Delay Cause | Mechanism | 5-min Cadence Helps? |
|---|---|---|---|
| META | Market-driven (rvol) | rvol 0.442→0.511 over 20 min | YES — ~10 min benefit |
| GOOGL | Scanner-driven (score) | Score 22-32 for 6 scans despite rvol=0.616-0.679 | NO — score gated |
| SMH | Market-driven (rvol) | rvol 0.425→0.502 over 55 min | YES — ~5 min benefit |
| AMZN | Market-driven (rvol) | rvol 0.274→0.501 over 66 min | YES — detected at first eligible scan |
| TSLA | Market-driven (rvol) | rvol never crossed 0.418 by EOD | N/A — never qualified |

### GOOGL Score Progression (Score-Driven Delay)

GOOGL had rvol ≥ 0.5 from scan 1 (rvol=0.616) but score blocked entry for 6 scans:

| Scan | Score | Rejection Reason |
|---|---|---|
| 10:31 | 32 | score_below_threshold (32 < 40) |
| 10:36 | 32 | score_below_threshold |
| 10:41 | 22 | score_below_threshold |
| 10:46 | 32 | score_below_threshold |
| 10:51 | 32 | score_below_threshold |
| 10:56 | 32 | score_below_threshold |
| **11:01** | **40** | **PASS** |

GOOGL RSI was 22-26 throughout (extreme oversold). Despite valid rvol and strong directional signal (SHORT), the composite score only reached 40 at 11:01 ET. This is a scanner-driven delay independent of cadence.

---

## Trade Journal: Complete Round Trips

### SMH vwap_reclaim (Long) — 2 attempts, 0 fills

| | Attempt 1 | Attempt 2 |
|---|---|---|
| Time | 11:26:50 ET | 11:27:24 ET |
| Contract | SMH260605C00637500 | SMH260605C00640000 |
| Signal age | 87 min | 87 min |
| Signal quality | Q=2.0 | Q=2.0 |
| Scanner score | 40.0 | 40.0 |
| Spread | 8.7% | 6.3% |
| Limit price | — | $9.72 |
| Outcome | Risk rejected ($1,106 > $997 max) | Stale-cancelled at 11:29:26 |
| Reason | Trade cost exceeded max risk | Not filled within 2-minute window |

### AMZN ORB Breakdown (Short PUT) — 1 fill, 1 loss

| Event | Time | Detail |
|---|---|---|
| Signal generated | ~9:35 ET | ORB breakdown (82 min before detection) |
| First eligible scan | 11:37 ET | AMZN rvol first crossed 0.5 |
| Scanner score | 52.0 | orb_breakdown + wide-range + trend_sideways |
| Signal quality | Q=3.0 | ORB signals maintain full quality (no age decay) |
| Contract | AMZN260603P00247500 (today exp) | Spread=10%, OI=122, vol=3,317 |
| Limit price | $0.21 | Marketable_limit (+1% above ask) |
| Order placed | 11:37:07 ET | Risk approved: $21 |
| **Alpaca fill** | **11:39:02 ET** | **Filled at $0.21 (1 min 55 sec)** |
| FillTracker stale cancel | 11:39:09 ET | Cancel fired 7 seconds after fill |
| Cancel result | HTTP 422 | Order already filled — cancel rejected |
| Session journal status | cancelled | FillTracker bug: fill not re-checked after 422 |
| Reconcile discovery | 12:01:34 ET | Reconciler found orphaned position, repaired |
| Exit via trailing stop | 12:02:04 ET | Spread=32%, bid=$0.13, ask=$0.18 |
| Exit fill | 12:02 ET | $0.15 (better than $0.13 limit) |
| Realized P&L | **-$6.00** | ($0.15-$0.21) × 100 |
| Journal P&L | not recorded | Fill never captured in journal (FillTracker bug) |

### META VWAP Reclaim (Short PUT) — 1 fill, 1 loss

| Event | Time | Detail |
|---|---|---|
| Signal generated | ~12:10 ET | VWAP breakdown (10 min before detection) |
| Signal trigger | Scan 22 (12:17 ET) | META changed LONG→SHORT; trend_down detected |
| Signal quality | Q=2.0 | Fresh vwap_reclaim signal |
| Signal age | 10 min | Freshest signal of the session |
| Scanner score | 55.0 | trend_down + wide-range + rsi=48 |
| Contract | META260603P00605000 (today exp) | Spread=5.8%, OI=1,832, vol=5,511 |
| Limit price | $0.53 | Marketable_limit |
| Order placed | 12:20:27 ET | Risk approved: $53 |
| **Fill** | **12:20:57 ET** | **$0.51 (31 seconds — proper FillTracker detection)** |
| Peak value | $0.51 | At entry (position moved immediately against) |
| Exit via trailing stop | 12:29:44 ET | Spread=31%, bid=$0.33, ask=$0.45 |
| Exit fill | 12:29:44 ET | $0.35 (better than $0.33 limit) |
| Hold duration | **527 seconds (8 min 47 sec)** | |
| Realized P&L | **-$16.00** | ($0.35-$0.51) × 100 |
| Journal P&L | -$12.00 | Using midpoint $0.39 (not actual fill) |

---

## Signal Bridge Summary

| Symbol | Strategy | Count | Age Range | Q | Final Decision |
|---|---|---|---|---|---|
| META | rsi_trend | 136 | 81–170 min | 0.0 | skipped (diagnostic) |
| META | vwap_reclaim | 1 | 10 min | 2.0 | **traded** |
| AMZN | orb | 1 | 82 min | 3.0 | **traded** |
| AMZN | vwap_reclaim | 1 | 112 min | 3.0 | skipped (pending_order_exists) |
| SMH | rsi_trend | 2 | 1307 min | 0.0 | skipped (diagnostic) |
| SMH | vwap_reclaim | 1 | 87 min | 2.0 | blocked (risk: cost > max) |
| SMH | vwap_reclaim | 1 | 87 min | 2.0 | traded (stale-cancelled) |
| COIN | orb | 10 | 153–158 min | 2.0 | blocked (max entries) |
| NVDA | vwap_reclaim | 4 | 33–35 min | 2.0 | blocked / skipped (max entries + cooldown) |
| MSFT | rsi_trend | 8 | 2948–2965 min | 0.0 | skipped (diagnostic) |
| **Total** | | **165** | | | |

---

## rvol Progression: Key Symbols

All rvol values per 5-minute scan cycle. "+" = PASS (rvol ≥ 0.5 AND score ≥ 40).

| ET    | META    | GOOGL   | SMH     | AMZN    | TSLA    |
|-------|---------|---------|---------|---------|---------|
| 10:31 | 0.442   | 0.616\* | 0.425   | 0.274   | 0.275   |
| 10:36 | 0.459   | 0.637\* | 0.436   | 0.284   | 0.287   |
| 10:41 | 0.475   | 0.650\* | 0.446   | 0.297   | 0.297   |
| 10:46 | 0.496   | 0.662\* | 0.453   | 0.309   | 0.306   |
| 10:51 | 0.511+  | 0.679\* | 0.462   | 0.328   | 0.315   |
| 10:56 | 0.521+  | 0.690\* | 0.468   | 0.349   | 0.333   |
| 11:01 | 0.530+  | 0.705+  | 0.473   | 0.364   | 0.346   |
| 11:06 | 0.541+  | 0.719+  | 0.476   | 0.379   | 0.356   |
| 11:11 | 0.553+  | 0.743+  | 0.481   | 0.406   | 0.368   |
| 11:16 | 0.566+  | 0.766+  | 0.488   | 0.423   | 0.377   |
| 11:21 | 0.573+  | 0.792+  | 0.492   | 0.436   | 0.385   |
| 11:26 | 0.588+  | 0.808+  | 0.502+  | 0.451   | 0.393   |
| 11:32 | 0.597+  | 0.821+  | 0.515+  | 0.470   | 0.404   |
| 11:37 | 0.607+  | 0.832+  | 0.521+  | 0.501+  | 0.418   |
| 11:42 | 0.614+  | 0.844+  | 0.546+  | 0.517+  | 0.430   |

\* = rvol ≥ 0.5 but score < 40 (GOOGL: score was 22-32 until 11:01)

TSLA: score=70 throughout but rvol never crossed 0.418 — blocked by low_volume_chop all session.

---

## Strategy Signal Behavior

### rsi_trend (Diagnostic Only in Permissive Mode)
- META: 136 stubs, all Q=0.0. Signal from ~9:30 ET (age 81 min at first detection). Stale immediately.
- GOOGL: Bridge log showed "rsi_trend ready: sufficient bars" but ZERO bridge stubs. Interpretation: `get_readiness_info()` returns ready=True (has enough bars), but no actual signal generated. GOOGL RSI was 22-26 (extreme oversold, deepening), suggesting rsi_trend requires RSI rising through a threshold (no fresh upward cross for GOOGL).
- AMZN: "rsi_trend ready" logged once at 11:37 (first detection) then disappeared. RSI=27-29 throughout.
- MSFT: 8 stubs with age 2948-2965 min (signal from yesterday's pre-market).
- rsi_trend readiness ≠ rsi_trend signal: strategy logs readiness (has enough bars) independently of whether signal conditions are met.

### ORB (One-Shot per Session)
- ORB signals fire once when the breakout/breakdown event first occurs.
- AMZN: ORB breakdown at ~9:35 ET (81 min before first eligible scan). Detected and traded at 11:37 ET. **Q=3.0 — ORB signals do NOT decay with age (unlike rsi_trend).**
- COIN: ORB fired at 12:22 ET with age=153-158 min. Blocked by max entries. Fired every 30 seconds for 5 minutes (10 bridge entries) — demonstrating that ORB re-evaluates each cycle for blocked positions.
- META/GOOGL: Scanner showed orb_breakout/orb_breakdown as scoring reasons, but the ORB strategy did NOT generate signals. This indicates the scanner's ORB detection (scoring heuristic) differs from the strategy's ORB condition (requires specific price action that may not have occurred during strategy evaluation).

### vwap_reclaim (Age-Indifferent, Q=2.0 standard)
- SMH: age=87 min, Q=2.0 — attempted twice, blocked once (risk), stale-cancelled once.
- AMZN: age=112 min, Q=2.0 — skipped (pending_order_exists from ORB order).
- META: age=10 min, Q=2.0 — the freshest vwap signal of the session. Fired after META switched SHORT at 12:17 ET. Traded and filled in 31 seconds.
- NVDA: age=33-35 min, Q=2.0 — blocked by max entries at 12:28-12:29 ET.

---

## Bug Report: FillTracker 422 Handling

**Description:** When FillTracker attempts to cancel a stale order and receives HTTP 422 (order already in final state), it proceeds to call `_handle_dead()` with "stale_cancelled" reason without re-checking the actual order status. If the order was filled (not just cancelled), this causes the fill to be silently dropped.

**Evidence:**
- AMZN order placed: 11:37:07 ET
- Alpaca fill: 11:39:02 ET (1 min 55 sec fill time)
- FillTracker stale cancel: 11:39:09 ET (7 seconds AFTER fill)
- Cancel response: HTTP 422 (order already filled)
- Session action: `_handle_dead("stale_cancelled")` — position NOT opened in PM
- Journal #307: status=cancelled, fill_price=None (fill lost)

**Fix Location:** `app/trading/fill_tracker.py` lines 186-191. After catching cancel exception (or receiving 422), re-fetch order status. If `filled`, call `_handle_fill()` instead of `_handle_dead()`.

**System Resilience:** The 30-minute Reconciler detected and repaired the orphaned position at 12:01:34 ET. Position was then managed (trailing stop exit at 12:02 ET). No position leak occurred, but the 22-minute window between fill and discovery meant the exit was based on stale pricing.

---

## System Behavior Observations

### Max Entries Counter (3/3)
- The RiskManager hit "Max entries per day reached: 3/3" at 12:23 ET.
- Counted entries: SMH #306 (placed, stale-cancelled) + AMZN #307 (placed, misclassified as cancelled) + META #308 (filled) = 3.
- The counter includes **placed** orders (not just fills). This prevents gaming the limit via rapid cancel-and-retry.
- COIN's ORB signal fired 10 times in a row (blocked each time), generating journal entries #309-#318.
- NVDA fired at 12:28-12:29, generating #319-#322.

### Trailing Stop Behavior
Both filled positions were exited by trailing stop, not EOD:
- AMZN: exit at 12:02 ET, 23 min after fill (spread=32%, bid=0.13, ask=0.18)
- META: exit at 12:29:44 ET, 9 min after fill (spread=31%, bid=0.33, ask=0.45)

Both exits experienced **extreme spread widening** (30-32%) as the expiring-day options approached worthlessness. The exit spread warning was logged but exits proceeded at bid. This is correct behavior but resulted in execution at unfavorable prices.

### Max Symbols Traded Per Day
At 12:20:27 ET when the META order was placed: "Max symbols traded per day (3) reached." This cap (SMH + AMZN + META = 3) was enforced correctly — subsequent scans confirmed COIN and NVDA as candidates but neither could be entered even if risk slots were available.

### Exit Liquidity Degradation
Today-expiry options lose all time value rapidly in the final hour. Both AMZN and META exits had bid/ask spreads > 30%, making marketable exits very costly. The session correctly logged spread warnings but had no mechanism to defer exit when spreads are extreme.

---

## Session P&L Summary (Paper)

| Position | Strategy | Entry | Exit | Hold | P&L (realized) | P&L (journal) |
|---|---|---|---|---|---|---|
| SMH vwap_reclaim | Long | — | — | — | $0 (not filled) | rejected |
| AMZN orb | Short PUT | $0.21 | $0.15 | 23 min | **-$6.00** | not recorded\* |
| META vwap_reclaim | Short PUT | $0.51 | $0.35 | 8.8 min | **-$16.00** | -$12.00\*\* |
| **Total** | | | | | **-$22.00** | -$17.50 |

\* FillTracker bug: fill not recorded in journal; reconciler adopted position.
\*\* Journal uses midpoint ($0.39) rather than fill price ($0.35) for P&L.

---

## Key Research Findings

### 1. 5-Minute Cadence Benefit is Real but Limited
- STANDBY exit was ~10 minutes earlier than 30-min would have been (META rvol threshold crossed between scans 4 and 5)
- For SMH: ~5 minutes earlier detection (rvol crossed between scans 11 and 12)
- For GOOGL: **zero benefit** — delay was score-driven (22-32 for 6 scans), not rvol-driven
- For AMZN: ~5 minutes benefit (rvol crossed between scans 13 and 14)
- Overall: 5-min cadence helps rvol-gated symbols but has no effect on score-gated symbols

### 2. ORB Signal Age Does Not Degrade Quality
- AMZN's ORB signal was 82 minutes old but still Q=3.0 at time of trade
- This is by design: the ORB level remains valid all day as a reference price
- Implication: ORB signals from market open (9:30 ET) remain tradeable at 11:30 ET+

### 3. Strategy Signal ≠ Scanner Signal
- Scanner uses composite scoring with ORB conditions as bonus points
- Session strategies evaluate ORB using different logic (e.g., entry bar, confirmation requirements)
- META/GOOGL had "orb_breakout/breakdown" in scanner scores but ORB strategy never fired for them
- AMZN's ORB strategy fired once at first detection and never re-fired
- Scanner ORB detection is a necessary but not sufficient condition for strategy ORB signals

### 4. Late-Session Liquidity Surge
- Pass count grew from 7 (12:02) → 9 (12:17) → 11 (12:23) → 12 (12:28)
- New symbols passed in final 13 minutes: COIN, NVDA, MSFT, SNOW
- Universe was most active in the final 30 minutes of the session
- But MAX_ENTRIES was already exhausted before this window opened
- This suggests the 12:15-12:30 ET window is high-opportunity but the daily limits are typically used up earlier

### 5. FillTracker 2-Minute Window Too Short for Low-Priced Options
- AMZN put ($0.21) needed 1 min 55 sec to fill — barely within the 2-minute window
- The stale cancel fired 7 seconds after the fill (timing was near-miss)
- At lower option prices with wider spreads, fills may consistently take > 2 minutes
- Recommendation: Review entry_order_timeout_secs for low-priced (<$0.50) options

### 6. Today-Expiry Option Exit Spread Problem
- Both positions (AMZN, META) had today's expiry and were exited near EOD
- Exit spreads were 31-32% at the time of trailing stop
- Actual exit fills were at bid (below midpoint), magnifying paper losses
- For evaluation purposes, today-expiry options should be avoided in the 12:00-12:30 ET window
- Or: Use a tighter trailing stop to capture profit before spread widens

---

## Appendix: Bridge and Journal Record Counts

| Category | Count |
|---|---|
| Total signal bridge entries | 165 |
| Tradeable signals (non-diagnostic) | 10 |
| Signals blocked (risk/limits) | 14 |
| Signals traded | 3 (SMH stale, AMZN FillTracker bug, META filled) |
| Actual fills | 2 (AMZN via Alpaca, META via FillTracker) |
| Total journal entries | 18 |
| Journal fills | 1 (#308 META) |
| Journal cancellations | 2 (#306 SMH, #307 AMZN) |
| Journal rejections | 15 (#305 risk, #309-#322 max_entries/cooldown) |

---

*Report generated: 2026-06-03 16:37 UTC*
*Session log: logs/session_2026-06-03.log (1,424 lines)*
*Branch: claude/options-trading-research-system-TIU0p*
