# Signal Age Origin Analysis

## Claims Registry

> **Data source:** 2026-05-29, 2026-06-02 — both pre-phase3 sessions with active defects  
> **All claims in this document carry `[CONTAMINATED SOURCE]`**  
> See `research/epistemic_standards.md` for category definitions.

| # | Claim summary | Tag | Contaminated |
|---|---------------|-----|--------------|
| 1 | Bridge entries occur within 2 seconds of scanner cycle; poll cycle does not produce independent entries | `OBSERVED` / `INFERRED` | yes |
| 2 | STANDBY duration is the dominant source of initial signal age | `INFERRED` | yes |
| 3 | Scanner-to-first-eval is consistently 30.2–30.3 min for all 11 signals (effective evaluation clock = scanner, not poll) | `DERIVED` / `INFERRED` | yes |
| 4 | Signal age variance is entirely in bar-to-scanner; scanner-to-eval is constant | `DERIVED` | yes |
| 5 | Queue latency is zero for all 5 traded signals (evaluated once, immediately submitted) | `OBSERVED` | yes |
| 6 | Fill latency contributes <3% of total age for signals with initial age ≥60 min | `DERIVED` | yes |
| 7 | H5 SUPPORTED: initial signal age is bounded below by scanner cycle (~30 min) | `OBSERVED` / `INFERRED` | yes |
| 8 | H6 SUPPORTED: STANDBY duration directly drives initial age for early-session bars | `DERIVED` / `INFERRED` | yes |
| 9 | Without sessions in normal-volume regime, structural age causes cannot be disentangled from signal quality | `INFERRED` | yes |



**Sessions:** 2026-05-29, 2026-06-02  
**Unique signals analyzed:** 11 (quality > 0, grouped by session × symbol × strategy × direction)  
**Bridge rows analyzed:** 218 (each bridge row = one poll-cycle evaluation of one signal)  
**Executed trades:** 5

---

## Data Constraints

1. **n=5 executed trades.** No statistical inference is valid.
2. **2 sessions, same low-volume regime.** Both sessions were in STANDBY from market open. No signal evaluated under normal-volume (rvol ≥ 1.0) conditions.
3. **Signal timestamps are bar-close times.** Signal age is computed as `now_utc – signal.timestamp_utc`. `signal.timestamp` is the bar index time when the strategy condition was first met.
4. **Bridge.timestamp is evaluation time**, not signal-generation time. Each poll cycle re-evaluates active signals and creates a new bridge row.
5. **Scanner_to_first_eval is measured from the previous scanner run**, not the concurrent one. Bridge entries appear at (or within seconds of) scanner cycle time.

---

## Pipeline Architecture

```
Bar closes (signal.timestamp)
     │
     ├─ [bar_to_scanner_latency] ─────────────────────────────────┐
     │   Time from bar close to most recent scanner run             │
     │   before first evaluation.                                   │
     │   Can be negative (scanner ran before bar closed).           │
     │                                                              ▼
     │                                              Scanner approves symbol
     │                                              (scan_results.scanned_at)
     │
     ├─ [scanner_to_first_eval_latency] ──────────────────────────┐
     │   Time from previous scanner run to first bridge entry.      │
     │   = scanner cycle interval (~30 min).                        │
     │                                                              ▼
     │                                              First bridge entry
     │                                              (signal_bridge.timestamp)
     │
     ├─ [queue_latency] ──────────────────────────────────────────┐
     │   Time signal is repeatedly re-evaluated without execution.   │
     │   = 0 for all traded signals in this dataset.                │
     │                                                              ▼
     │                                              Order submitted
     │
     └─ [fill_latency] ───────────────────────────────────────────▶ Fill
         Time from order submit to fill confirmation.
         (trade_journal.time_to_fill_secs)
```

**signal_age_seconds** (stored in bridge) = bar_to_scanner + scanner_to_first_eval  
= total latency from bar close to first bridge entry.

---

## Scanner Cycle Timing

| Session | Scan times (ET) | Interval | First bridge entry |
|---|---|---|---|
| 2026-05-29 | 09:33, 10:03, 10:34, 11:04, 11:34, 12:04 | ~30 min | 10:34:01 ET |
| 2026-06-02 | 09:50, 09:50, 10:20, 10:51, 11:21, 11:51, 12:21 | ~30 min | 11:21:17 ET |

Observed scanner interval: exactly 30 minutes in all cases. The two entries at 09:50 on 2026-06-02 reflect a session restart; only one scan produced results.

**Bridge entries occur at scanner cycle boundaries.** Bridge entries appear within 2 seconds of the corresponding scanner run, not uniformly distributed across the poll interval. The poll cycle (~30 s) runs continuously but signals only reach quality > 0 evaluation at scanner cycle time.

---

## STANDBY Duration

Both sessions opened in STANDBY (low relative volume). The STANDBY regime prevented signal evaluation until conditions changed.

| Session | Session start | First bridge entry | STANDBY duration |
|---|---|---|---|
| 2026-05-29 | 09:33 ET | 10:34 ET | ~61 min |
| 2026-06-02 | 09:50 ET | 11:21 ET | ~91 min |

Signal bars that closed during STANDBY accumulated age equal to the time from bar close to STANDBY exit. This is the dominant source of initial signal age for most signals in this dataset.

---

## Per-Signal Latency Table

| Session | Symbol | Strategy | Dir | Q | Decision | Bar close | Initial age | Bar→Scan | Scan→Eval | Queue | Fill | PnL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-29 | DIA | orb | LONG | 3 | BLOCKED | 10:50 | 14.3 min | −15.9 min | 30.3 min | 29.8 min | — | — |
| 2026-05-29 | DIA | vwap_reclaim | LONG | 1 | BLOCKED | 10:05 | 59.3 min | +29.1 min | 30.3 min | 29.8 min | — | — |
| 2026-05-29 | MSFT | vwap_reclaim | LONG | 2 | TRADED | 10:25 | 39.3 min | +9.1 min | 30.3 min | 0.0 min | 34 s | −$9.00 |
| 2026-05-29 | MSTR | orb | LONG | 3 | BLOCKED | 11:05 | 59.9 min | +29.6 min | 30.2 min | 25.0 min | — | — |
| 2026-05-29 | MSTR | vwap_reclaim | SHORT | 2 | BLOCKED | 10:15 | 109.9 min | +79.6 min | 30.2 min | 25.0 min | — | — |
| 2026-05-29 | SNOW | vwap_reclaim | LONG | 3 | TRADED | 10:15 | 19.0 min | −11.2 min | 30.2 min | 0.0 min | 95 s | −$31.50 |
| 2026-06-02 | COIN | orb | SHORT | 2 | SKIPPED | 10:25 | 86.6 min | +56.3 min | 30.3 min | 0.0 min | — | — |
| 2026-06-02 | COIN | vwap_reclaim | SHORT | 3 | TRADED | 09:50 | 121.6 min | +91.3 min | 30.3 min | 0.0 min | 64 s | −$103.50 |
| 2026-06-02 | DIA | vwap_reclaim | LONG | 2 | SKIPPED† | 10:40 | 41.3 min | +11.2 min | 30.1 min | 0.0 min | — | — |
| 2026-06-02 | DIA | vwap_reclaim | SHORT | 4 | TRADED | 09:45 | 96.3 min | +66.2 min | 30.1 min | 0.0 min | 64 s | +$25.00 |
| 2026-06-02 | META | vwap_reclaim | LONG | 2 | TRADED | 10:15 | 96.6 min | +66.3 min | 30.3 min | 0.0 min | 64 s | −$50.00 |

†DIA LONG was skipped (`pending_order_exists`) because the DIA SHORT order was placed in the same evaluation cycle.

**Negative bar→scan values** (DIA orb −15.9 min, SNOW −11.2 min) indicate the scanner ran before the signal bar closed. The signal was first detected in a bar that closed after the last scanner cycle.

---

## Stage Aggregates

| Stage | Avg | Median | Min | Max |
|---|---|---|---|---|
| Initial age (bar→first eval) | 67.7 min | 59.9 min | 14.3 min | 121.6 min |
| Bar → scanner | +37.4 min | +29.6 min | −15.9 min | +91.3 min |
| Scanner → first eval | 30.3 min | 30.3 min | 30.1 min | 30.3 min |
| Queue latency | 10.0 min | 0.0 min | 0.0 min | 29.8 min |
| Fill latency (n=5 traded) | 64.0 s | 63.9 s | 34.1 s | 94.5 s |

---

## Bridge Evaluation Counts

| Session | Symbol | Strategy | Dir | Eval count | Age range | Decision |
|---|---|---|---|---|---|---|
| 2026-05-29 | DIA | vwap_reclaim | LONG | 59 | 59.3–89.1 min | BLOCKED (all) |
| 2026-05-29 | DIA | orb | LONG | 54 | 14.3–44.1 min | BLOCKED (all) |
| 2026-05-29 | MSTR | vwap_reclaim | SHORT | 49 | 109.9–134.9 min | BLOCKED (all) |
| 2026-05-29 | MSTR | orb | LONG | 49 | 59.9–84.9 min | BLOCKED (all) |
| 2026-06-02 | DIA | vwap_reclaim | SHORT | 1 | 96.3 min | TRADED |
| 2026-06-02 | DIA | vwap_reclaim | LONG | 1 | 41.3 min | SKIPPED |
| 2026-05-29 | MSFT | vwap_reclaim | LONG | 1 | 39.3 min | TRADED |
| 2026-05-29 | SNOW | vwap_reclaim | LONG | 1 | 19.0 min | TRADED |
| 2026-06-02 | COIN | orb | SHORT | 1 | 86.6 min | SKIPPED |
| 2026-06-02 | COIN | vwap_reclaim | SHORT | 1 | 121.6 min | TRADED |
| 2026-06-02 | META | vwap_reclaim | LONG | 1 | 96.6 min | TRADED |

All 2026-05-29 blocked signals (DIA, MSTR) were blocked by `max_trades_per_day`. This reflects the pre-fix entry counter bug (exits consuming entry slots), documented in commit `2d15a18`. The repeated evaluations (49–59 cycles) are a consequence of the bug: the system kept re-evaluating signals that could never execute due to the incorrect counter.

All 2026-06-02 signals were evaluated once only. The session's three trades were placed in a single evaluation cycle (11:21:17 ET).

---

## Structural Findings

**1. Scanner cycle (30 min) is the effective evaluation clock.**  
Scanner_to_first_eval is consistently 30.2–30.3 min across all 11 signals in both sessions. Bridge entries cluster within 2 seconds of scanner run times, not at arbitrary points in the poll interval. Signals are not evaluated continuously on the poll cycle clock; they are evaluated on the scanner cycle clock.

**2. Initial signal age is determined by when the bar closed relative to the STANDBY exit.**  
For bars that closed during STANDBY, age = (STANDBY exit time) − (bar close time). This is the dominant source of age for the highest-aged signals (COIN vwap_reclaim 121.6 min, MSTR vwap_reclaim 109.9 min, META 96.6 min, DIA SHORT 96.3 min).

**3. Bar_to_scanner latency is the primary variable.**  
Scanner_to_first_eval is constant at ~30 min (scanner cycle interval). The variance in initial age is entirely in bar_to_scanner, which ranges from −15.9 to +91.3 min. bar_to_scanner is driven by: (a) STANDBY duration for early-session bars, (b) position of bar close within the 30-min scanner cycle.

**4. Queue latency is zero for all 5 traded signals.**  
Every traded signal was evaluated once and immediately submitted. The 4 blocked signals (DIA orb, DIA vwap_reclaim, MSTR orb, MSTR vwap_reclaim — all 2026-05-29) were re-evaluated 49–59 times due to the entry counter bug and never executed.

**5. Fill latency is the smallest component.**  
Fill latency ranged 34–95 seconds across 5 executed trades. At a typical initial age of 60+ min, fill latency contributes < 3% of total age from bar close to fill.

**6. Two signal bars closed before session start (2026-06-02).**  
COIN vwap_reclaim bar at 09:50 ET and DIA SHORT bar at 09:45 ET on 2026-06-02 closed before or at the session start (09:50 ET). These signals had age ≥ 96 min at first evaluation because they were pre-session bars incorporated into the first evaluation cycle at 11:21 ET.

---

## Hypothesis Evaluation

### H1: Signal age negatively correlates with performance

**Result: INCONCLUSIVE**

No valid inference is possible from n=5 non-randomly selected trades.

From the age-origin perspective: age is structurally driven by STANDBY duration and scanner timing — not by signal quality. The oldest signals (COIN 121.6 min, META 96.6 min) are old because of extended STANDBY (9:50–11:21 ET), not because of intrinsic signal properties. Age is largely a function of when the signal bar closed relative to the STANDBY exit, not of the signal itself.

---

### H2: Signals older than 60 min underperform fresh signals

**Result: NOT SUPPORTED (same conclusion as staleness analysis)**

| Group | Traded | Wins | Avg PnL |
|---|---|---|---|
| Initial age < 60 min | 2 (SNOW 19m, MSFT 39m) | 0 | −$20.25 |
| Initial age ≥ 60 min | 3 (DIA 96m, META 97m, COIN 122m) | 1 | −$42.83 |

Both groups lost money. The "fresh" group had 0% win rate. The "stale" group had the only win. Confounds identical to staleness analysis (same-day expiry, IV differences) prevent any directional conclusion.

---

### H3: Quality score remains predictive after controlling for age

**Result: CONSISTENT WITH DATA — not confirmable from n=5**

Same conclusion as staleness analysis. Quality-4 = only win. This is driven by a single observation.

---

### H4: Scanner score remains predictive after controlling for age

**Result: NOT SUPPORTED**

Same conclusion as staleness analysis. Lowest scanner score (DIA, 53) was the only win; highest (COIN, 62) was the worst loss.

---

### H5: The scanner cycle (~30 min) is the primary clock for signal evaluation timing

**Result: SUPPORTED**

Scanner_to_first_eval is 30.1–30.3 min for all 11 signals regardless of session, strategy, or outcome. Bridge entries appear within seconds of scanner run times. The poll cycle (~30 s) runs between scanner cycles but does not produce quality > 0 bridge entries between scanner runs. Initial signal age is bounded below by the scanner cycle interval (~30 min).

---

### H6: STANDBY mode is the dominant amplifier of signal age for early-session bars

**Result: SUPPORTED**

The 2026-05-29 session exited STANDBY at ~10:34 ET (~61 min from session start). The 2026-06-02 session exited STANDBY at ~11:21 ET (~91 min from session start). Signals from bars that closed during STANDBY — approximately 7 of 11 unique signals — had initial ages equal to or exceeding the STANDBY duration. The correlation between STANDBY duration and signal age is direct for early-session bars.

---

## What Additional Data Would Be Required

| Requirement | Current | Target |
|---|---|---|
| Sessions in active regime (rvol ≥ 1.0) | 0 | ≥ 5 |
| Executed trades | 5 | ≥ 30 |
| Signals evaluated outside STANDBY | ~2 | Mix needed |
| Sessions with STANDBY < 30 min | 0 | ≥ 5 |
| ORB trades with forward data | 0 | ≥ 10 |

Without sessions in normal-volume regime (no STANDBY or short STANDBY), the dominant source of signal age (STANDBY holdback) cannot be disentangled from signal quality and timing effects.

---

*Evidence only. No recommendations. No parameter changes. No strategy changes.*
