# Pre-Phase-3 Engineering Evidence Catalog

**Created:** 2026-07-12  
**Covers:** All sessions 2026-05-11 through 2026-07-11 (23 sessions, 33 trades)  
**Purpose:** Formal separation of infrastructure findings from strategy-performance claims.

> Pre-phase3 sessions ran on code with defects corrected in P1–P7. Their P&L and win-rate
> statistics cannot be used as strategy-performance evidence. They are preserved here as
> engineering evidence: observations about how the system behaves, what infrastructure
> defects looked like in practice, and what conditions the system has and has not encountered.
>
> For the Phase 3 clean cohort and strategy-performance analysis, see
> `evaluation/phase3_eval_protocol.md` and `evaluation/phase3_tracking.json`.

---

## How to Use This Document

Each finding below is categorized as one of:

- **Infrastructure behavior** — how the system mechanically processes signals, orders, and fills
- **Defect manifestation** — observed effects of a known bug that has since been fixed
- **Data dependency** — external data (yfinance, broker) behavior observed in the wild
- **Operational pattern** — how session execution unfolded under real conditions

These findings are appropriate inputs for:
- Infrastructure debugging and root-cause analysis
- Session tooling and recovery logic design
- Runbook / incident response procedures
- Test case design (what failure modes to cover)

These findings are **not** appropriate inputs for:
- Strategy threshold tuning
- Win rate or expectancy claims
- Comparative performance conclusions (ORB vs. VWAP, DTE, quality score, etc.)

---

## Engineering Finding 1 — Pattern A Fill Recovery Was the Dominant Fill Path

**Category:** Infrastructure behavior  
**Sessions:** S1–S5 (2026-06-05 – 2026-06-12)  
**Evidence type:** OBSERVED  

All 14 fills in S1–S5 arrived via Pattern A: FillTracker issued a stale-cancel on the entry
order (because the 422 race caused it to miss the fill confirmation), then the periodic
reconciler detected the resulting broker position and re-opened it. No `status=closed` rows
exist in `trade_journal` for S1–S5 — the journal exit path was bypassed by reconciler
recovery.

**Engineering implication:** The P6 fix (writing `exit_order_id` to `trade_journal` at
placement time) and the P2 EXIT_PENDING state machine ensure that exit orders are
recoverable after a restart without requiring the reconciler to re-discover them.

**What this does not tell us:** Whether trades filled through Pattern A or Pattern B
(direct fill) perform differently as a strategy. The Q7 fill-path comparison in
`phase2_tracking.json` conflates fill path with session phase and cannot be used as
strategy evidence.

---

## Engineering Finding 2 — STANDBY Persisted Through the Entire Viable Trading Window in Every Pre-Phase3 Session

**Category:** Infrastructure behavior / operational pattern  
**Sessions:** All 23 pre-phase3 sessions  
**Evidence type:** OBSERVED  

Every session with any trading activity entered STANDBY immediately after market open and
remained there for 60–140 minutes due to rvol < 0.5 on all scanned symbols. The system
correctly withheld from trading during low-volume conditions. No trades were placed in the
first 60 minutes of any session.

**Engineering implication:** STANDBY gate is functioning as designed. The 0.5 rvol
threshold is active and effective. Sessions S9/S10/S13 entered permanent STANDBY due to
yfinance data failures (empty DataFrames), not true low-volume market conditions —
these are data-dependency failures, not trading decisions.

**What this does not tell us:** Whether the strategy performs differently in sessions
without STANDBY (none have been observed). All pre-phase3 trade data is from
post-STANDBY conditions with signal ages of 60–140 minutes.

---

## Engineering Finding 3 — Scanner Cycle (30 min) Is the Effective Evaluation Clock

**Category:** Infrastructure behavior  
**Sessions:** 2026-05-29, 2026-06-02 (signal_bridge analysis)  
**Evidence type:** DERIVED / INFERRED  

Bridge entries cluster within 2 seconds of the scanner cycle boundary. The 30-second poll
cycle does not produce independent signal evaluations — it only submits orders. Initial
signal age at first evaluation is bounded below by the scanner interval (~30 min) because
signals cannot be evaluated before the next scanner run.

**Engineering implication:** Reducing the poll interval below 30 min provides no latency
benefit for signal evaluation. To reduce signal age at first evaluation, the scanner
interval itself would need to change. At 5-min cadence, yfinance daily data call volume
reaches ~6,300/day with unknown throttling risk.

**What this does not tell us:** Whether faster scanning improves trade outcomes (no
sessions with <30 min scanner interval have been run).

---

## Engineering Finding 4 — STANDBY Duration Is the Primary Driver of Initial Signal Age

**Category:** Infrastructure behavior  
**Sessions:** 2026-05-29, 2026-06-02  
**Evidence type:** INFERRED  

For 7 of 11 unique signals in these sessions, initial age at first evaluation equaled or
exceeded the STANDBY duration (61 min on 2026-05-29; 91 min on 2026-06-02). The
bar-to-scanner latency ranged from −15.9 to +91.3 min — the dominant source of variance.
Scanner-to-first-eval was constant at 30.2–30.3 min for all signals.

**Engineering implication:** STANDBY holdback is not a signal-quality filter — it is a
volume filter. Signals generated during STANDBY accumulate age independently of their
quality score or scanner score. An age-based entry filter would reject signals primarily
on the basis of STANDBY duration, not on the basis of whether the signal is stale.

**What this does not tell us:** Whether signals generated outside of STANDBY (shorter
age) perform differently (no such sessions in pre-phase3 data).

---

## Engineering Finding 5 — Trade Counter Bug Limited Effective Entries to ⌊max_trades/2⌋

**Category:** Defect manifestation (fixed before Phase 2)  
**Sessions:** 2026-05-11 – 2026-05-29  
**Evidence type:** OBSERVED / DERIVED  

`record_trade()` was called for both entry placements and exit closures. With
`RISK_MAX_TRADES_PER_DAY=3`, each round-trip consumed 2 counter slots, limiting
effective entries to 1 per session. All 103 ORB evaluations in the 2026-05-29 session
were blocked by the exhausted counter — not by signal quality, spread, or liquidity.

**Engineering implication:** This defect was fixed in Phase 2 (P2's separate
`record_entry_pending` / `record_entry_filled` / `record_exit` lifecycle). ORB signals
in Phase 3 sessions will not be blocked by this artifact.

**What this does not tell us:** ORB strategy performance (ORB was never executed during
the period this bug was active).

---

## Engineering Finding 6 — Exit Spreads Are Consistently Wider Than Entry Spreads on Adverse Exits

**Category:** Infrastructure behavior / operational pattern  
**Sessions:** Multiple pre-phase3 sessions  
**Evidence type:** DERIVED  

Across all observed adverse-exit trades, exit spreads were 1.3–3.5× wider than entry
spreads. SNOW: exit 29.0% vs entry 8.3%. MSFT: exit 11.8% vs entry 8.0%. META: exit
spread elevated on 1-DTE expiry day.

**Engineering implication:** Exit limit price should be set at bid (not midpoint) for
any option position in an adverse move. The `OPTIONS_EXIT_LIMIT_PRICE_MODE=marketable_limit`
setting already implements this. Exit spread cost should be budgeted when computing
net P&L expectations.

**What this does not tell us:** Whether exit spread width varies by instrument class,
DTE, or strategy in a way that affects strategy selection (sample too small, all
contaminated sessions).

---

## Engineering Finding 7 — S9 and S10 Were Data-Failure Sessions, Not True STANDBY

**Category:** Data dependency  
**Sessions:** S9 (2026-06-24), S10 (2026-06-25) — permanently excluded per protocol  
**Evidence type:** INFERRED  

The yfinance daily data endpoint returned empty DataFrames for all 27 symbols on both
sessions. The scanner correctly rejected all candidates (no valid scores), producing
zero-trade sessions that superficially resemble STANDBY but are not: the rejection was
data-driven, not market-condition-driven. S9 and S10 are consecutive, suggesting a
sustained yfinance endpoint change rather than a transient outage.

**Engineering implication:** The system should distinguish between (a) STANDBY triggered
by genuine low-volume conditions and (b) STANDBY triggered by data failures. A data
integrity check (non-empty scan results before entering STANDBY) would surface this
distinction. S9/S10 are excluded from all analyses per protocol.

**What this does not tell us:** How many future sessions will encounter this failure mode
or whether the yfinance endpoint issue is resolved.

---

## Engineering Finding 8 — Kill Switch Blocked Exits (Fixed in P3)

**Category:** Defect manifestation (fixed in P3)  
**Sessions:** Any pre-P3 session where kill switch was activated  
**Evidence type:** OBSERVED (code inspection)  

Before P3, `monitor_positions()`, `eod_liquidate()`, and `_poll_pending_exit()` all
checked the kill-switch flag and returned early if active. A kill-switch activation with
open positions would strand those positions until the flag was cleared or the process was
restarted.

**Engineering implication:** P3 fix ensures exits always run regardless of kill-switch
state. Only new entries are blocked. Sessions where the kill switch was activated before
P3 may have had positions stranded; their exit prices and P&L are unreliable.

---

## Engineering Finding 9 — RiskManager Counters Reset to Zero on Any Intraday Restart (Fixed in P6)

**Category:** Defect manifestation (fixed in P6)  
**Sessions:** Any pre-P6 session where the runner was restarted intraday  
**Evidence type:** OBSERVED (code inspection)  

Before P6, `start_session()` unconditionally zeroed `_entries_today`, `_pending_entries`,
and `_daily_pnl` regardless of whether a restart occurred on the same trading day. An
intraday restart allowed the daily trade limit and daily loss limit to reset, potentially
permitting more trades than the configured maximum.

**Engineering implication:** P6 fix restores counters from `trade_journal` on restart.
Sessions where the runner was restarted intraday before P6 have unreliable daily-limit
enforcement. The `restore_daily_counters` log message now provides a verifiable audit
trail on every startup.

---

## Engineering Finding 10 — ORB Executed Successfully on S17 (First Confirmed ORB Completion)

**Category:** Infrastructure behavior  
**Session:** S17 (2026-07-09) — pre-phase3 (carries P2/P6 contamination flags)  
**Evidence type:** OBSERVED  

S17 produced the first confirmed ORB strategy execution: META 1-DTE call, entered at
11:29 ET at option price $0.66, exited at $1.33 (100% take_profit). The ORB selection
pipeline — scanner rescan at 11:29, ORB breakout detection, signal_bridge evaluation,
order placement, fill confirmation, position monitoring, take_profit trigger — all
executed without any reported infrastructure failures.

**Engineering implication:** The full ORB execution path is operational. Prior sessions'
zero ORB completions were explained by the trade counter bug (pre-Phase 2) and by
STANDBY holdbacks (all phases). S17 confirms ORB can complete a round-trip. The specific
outcome (+$434) is not a performance conclusion — it is an infrastructure confirmation
that the ORB path works end-to-end.

---

## Engineering Finding 11 — All Pre-Phase3 P&L Statistics Are Defect-Contaminated

**Category:** Defect manifestation summary  
**Sessions:** All 23 pre-phase3 sessions  
**Evidence type:** DERIVED  

The `evaluation/ledger.json` cumulative statistics (33 trades, −$602.50 total P&L,
14.3% win rate across clean sessions) cannot be used as strategy-performance evidence
because:

1. **S1–S5 P&L (Bug C):** Exit prices recorded at midpoint, not bid; overstated losses
   by an estimated $4–14/session.
2. **S6–S11 and later:** Exit state machine absent (P2); duplicate exits possible; hold
   durations unreliable.
3. **All sessions:** RiskManager counters could reset on restart (P6); daily loss and
   entry limits unreliable after any intraday restart.
4. **S1–S5 specifically:** All fills via Pattern A (reconciler recovery); `trade_journal`
   has no `status=closed` rows — `realized_pnl` in the journal is null for these trades.

The correct interpretation: these statistics describe the behavior of a system running
with known defects. They are not a measurement of what the strategy would produce on
correct infrastructure.

---

## Summary Table

| Finding | Category | Actionable for Phase 3? |
|---------|----------|------------------------|
| 1. Pattern A fill recovery was dominant | Infrastructure behavior | No — P2/P6 fix should eliminate Pattern A as dominant path |
| 2. STANDBY in every session | Infrastructure behavior | Monitor — STANDBY is expected; watch for data-failure STANDBY |
| 3. Scanner cycle = evaluation clock | Infrastructure behavior | Yes — informs scanner interval decisions |
| 4. STANDBY drives signal age | Infrastructure behavior | Yes — informs any age-based entry filter design |
| 5. Trade counter bug blocked ORB | Defect (fixed) | No — fixed before Phase 2 |
| 6. Exit spreads wider on adverse exits | Operational pattern | Yes — budget exit spread cost; use bid-side limit |
| 7. S9/S10 data-failure STANDBY | Data dependency | Yes — add data integrity check to distinguish types |
| 8. Kill switch blocked exits | Defect (fixed P3) | No — fixed in P3 |
| 9. RiskManager counter reset | Defect (fixed P6) | No — fixed in P6; verify via restore log on restart |
| 10. ORB execution path confirmed working | Infrastructure behavior | Yes — ORB round-trip is operational |
| 11. All pre-phase3 P&L is defect-contaminated | Defect summary | Yes — use only `phase3_cumulative` for performance claims |
