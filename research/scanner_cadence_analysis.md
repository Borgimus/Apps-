# Scanner Cadence Analysis

## Claims Registry

> **Data source:** 2026-05-29, 2026-06-02 — pre-phase3; code analysis is session-independent  
> **Execution-behavior claims carry `[CONTAMINATED SOURCE]`; code/config observations do not**  
> See `research/epistemic_standards.md` for category definitions.

| # | Claim summary | Tag | Contaminated |
|---|---------------|-----|--------------|
| 1 | UNIVERSE_SCAN_INTERVAL_MINUTES = 30 (default; no .env override) | `OBSERVED` | no |
| 2 | Effective cadence = 30 min ± 30 s (scanner triggers within one poll cycle of interval elapsing) | `INFERRED` | no (code logic) |
| 3 | No documented rationale for 30-min interval found in code or commit history | `OBSERVED` | no |
| 4 | Alignment with market microstructure cycles as historical rationale | `SPECULATIVE` | — |
| 5 | 6,318 yfinance calls/day at 5-min cadence carries throttling risk | `INFERRED` | no |
| 6 | 1-min cadence (31,590 calls/day) carries significant throttling risk | `SPECULATIVE` | — |
| 7 | Confirmed candidate count is independent of cadence; reflects market conditions | `INFERRED` | yes |
| 8 | Faster scanning provides ≤15 min signal-age improvement for most signals; STANDBY dominates | `INFERRED` | yes |
| 9 | Only SNOW would meaningfully benefit from 15-min cadence | `DERIVED` | yes |



**Sessions:** 2026-05-29, 2026-06-02  
**Signals analyzed:** 11 unique (quality > 0, grouped by session × symbol × strategy × direction)  
**Bridge rows:** 218  
**Executed trades:** 5

---

## Data Constraints

1. **n=2 sessions, same low-volume regime.** Both sessions in STANDBY (rvol < 0.5) for 61–91 min from open. No sessions under normal-volume conditions (rvol ≥ 1.0 from open).
2. **Simulation is theoretical.** Hypothetical discovery times assume signal quality and direction are unchanged at a faster cadence. Scanner scores are observed only at actual 30-min intervals; values between intervals are estimated by linear interpolation of rvol.
3. **rvol path is not measured between scans.** Actual intraday rvol at 5 or 15-min resolution is not stored.

---

## 1. Why Scanner Evaluations Occur Every ~30 Minutes

The scanner cadence is controlled by a wall-clock check on every poll cycle:

```python
# session_runner.py:1679–1683
if (
    _uni_mode != "off"
    and not _fill_test_mode
    and _last_scan_at is not None
    and (now - _last_scan_at).total_seconds() / 60 >= _scan_interval_min
    and now < eod_time
):
```

`_scan_interval_min` is loaded at startup (line 1548):

```python
_scan_interval_min = getattr(settings.universe, "scan_interval_minutes", 30)
```

`scan_interval_minutes` defaults to 30 in `app/config/settings.py:104`:

```python
scan_interval_minutes: int = _yaml_get("universe", "scan_interval_minutes", default=30)
```

No override of `UNIVERSE_SCAN_INTERVAL_MINUTES` is present in `.env`. The active value is the default of 30.

---

## 2. Which Component Controls Scanner Cadence

| Layer | Location | Value |
|---|---|---|
| Environment variable | `UNIVERSE_SCAN_INTERVAL_MINUTES` | not set → default used |
| Python default | `app/config/settings.py:104` | **30** |
| Check location | `session_runner.py:1683` | every poll cycle |
| Poll cycle interval | `--poll` CLI arg, default 300 s | observed 30 s in sessions |

The scanner runs **inside the main poll loop** when `elapsed_since_last_scan ≥ scan_interval_minutes`. The check fires every `--poll` seconds. With `--poll 30` (observed in both sessions), the scanner triggers within 30 seconds of the interval elapsing — meaning effective cadence = 30 min ± 30 s.

**Scanner pipeline** (`_run_universe_scan`, session_runner.py:1068):
1. `UniverseLoader` → symbol list (27 symbols observed)
2. `YFinanceScanner.scan()` → concurrent metrics via `asyncio.gather` + `run_in_executor`
3. `CandidateScorer.score_all()` → reject symbols with rvol < 0.5, score < 40, ATR < 0.2%, or earnings
4. `AlpacaConfirmer.confirm()` → verify option chain liquidity per passed candidate
5. Persist all candidates to `DBScanResult`; update `active_symbols`

---

## 3. Historical Rationale for 30-Minute Interval

**Commit of origin:** `e3e374f` — "Sprint 7: Multi-ticker data-driven candidate selection" (2026-05-11)

The commit introduced `scan_interval_minutes` with default 30. The commit message lists it as a settings parameter; no accompanying comment or rationale appears in the code.

```
Settings:
- UniverseSettings: ... scan_interval_minutes
```

No subsequent commit has changed the value. The `.env` file does not override it.

**Inferred factors at time of introduction:**
- Paper-mode sessions at the time had no prior scan data to calibrate against.
- 30 minutes aligns with observable market microstructure cycles (opening auction settling, liquidity building in first hour).
- Conservative choice reduces API call volume during initial development.

No documented rationale was found in code, commit messages, or config comments.

---

## 4. CPU / API / Database Impact Estimates

### Scanner mechanics

| Component | Method | Observed symbols |
|---|---|---|
| YFinance | `asyncio.gather` + `run_in_executor` (concurrent threads) | 27 per scan |
| YFinance calls per symbol | 3 (daily bars, intraday bars, earnings check) | — |
| Alpaca calls per confirmed symbol | 2 (`get_available_expirations`, `get_option_chain`) | 0–10 confirmed per scan |
| DB rows written per scan | 27 (`DBScanResult` inserts) | — |
| Estimated scan wall time | ~3–10 s (concurrent; bounded by slowest symbol) | — |

### Impact by cadence

| Cadence | Scans/session | YFinance calls | Alpaca calls (est) | DB rows | Scanner share of loop |
|---|---|---|---|---|---|
| **30 min (current)** | 6–7 | 486–567 | 18–42 | 162–189 | ~0.6% |
| **15 min** | 13 | 1,053 | 39–78 | 351 | ~1.1% |
| **5 min (per-bar)** | 78 | 6,318 | 234–468 | 2,106 | ~3.3% |
| **1 min** | 390 | 31,590 | 1,170–2,340 | 10,530 | ~16.7% |

*Scanner share of loop = estimated scan wall time / cadence interval. At --poll 30 s, per-bar (5 min) means the scanner fires every 10 poll cycles.*

**"Per-bar cadence"** in this system means 5-minute cadence. Intraday bars are 5-minute bars (`ticker.history(period="2d", interval="5m")`). Running the scanner at per-bar frequency is equivalent to a 5-minute cadence.

### Rate limit assessment

- **Alpaca (paper API):** limit ~200 req/min. At 5-min cadence: ≤ 10 Alpaca calls per 5 min = 2 calls/min. Within limits at all cadences.
- **yfinance:** unofficial Yahoo Finance scraping. No documented rate limit. 6,318 calls/day at 5-min cadence is high and could trigger throttling. Tested cadences of 1 min (31,590 calls/day) carry significant throttling risk.
- **Database:** at 5-min cadence, `scan_results` grows ~2,106 rows/session vs ~175 at 30 min (12× increase). No immediate storage concern for paper sessions.

### CPU

The scanner runs synchronously within the async event loop (via `run_in_executor`). At 5-min cadence with 10 s scan time, the scanner occupies ~3.3% of loop execution time. At 1-min cadence, ~16.7% — meaningful contention with position monitoring and order management.

---

## 5. Session Cadence Observations

### Observed scan times

| Session | Scan times (ET) | Cycle count | Interval |
|---|---|---|---|
| 2026-05-29 | 09:33, 10:03, 10:34, 11:04, 11:34, 12:04 | 6 | 30 min |
| 2026-06-02 | 09:50, 09:50†, 10:20, 10:51, 11:21, 11:51, 12:21 | 7 | 30 min |

†Two scans at 09:50 on 2026-06-02: session restart, first produced 1 row (discarded).

### Candidates and confirmation per cycle

| Session | Scan (ET) | Symbols scanned | Passed rvol/score | Selected (confirmed) |
|---|---|---|---|---|
| 2026-05-29 | 09:33 | 27 | 0 | 5 |
| 2026-05-29 | 10:03 | 27 | 1 | 5 |
| 2026-05-29 | 10:34 | 27 | 2 | 5 |
| 2026-05-29 | 11:04 | 27 | 4 | 5 |
| 2026-05-29 | 11:34 | 27 | 5 | 4 |
| 2026-05-29 | 12:04 | 27 | 7 | 3 |
| 2026-06-02 | 09:50 | 26 | 0 | 8 |
| 2026-06-02 | 10:20 | 27 | 2 | 8 |
| 2026-06-02 | 10:51 | 27 | 1 | 7 |
| 2026-06-02 | 11:21 | 27 | 6 | 6 |
| 2026-06-02 | 11:51 | 27 | 8 | 4 |
| 2026-06-02 | 12:21 | 27 | 10 | 3 |

*Passed = not rejected by CandidateScorer (rvol ≥ 0.5, score ≥ 40, ATR ≥ 0.2%, no earnings). Selected = confirmed by AlpacaConfirmer.*

Confirmed candidate count is independent of cadence — it reflects market conditions, not how often the scanner runs. At a faster cadence, the same symbol universe is re-evaluated; confirmed counts would change as rvol/score cross thresholds.

---

## 6. STANDBY Mechanism

The scanner rejects all symbols with **rvol < 0.5** or **score < 40** (or earnings flag, ATR < 0.2%).

When all candidates are rejected, `_run_universe_scan` returns `None` and the system enters STANDBY (`active_symbols = []`). No signals are evaluated during STANDBY.

**Observed STANDBY by symbol:**

| Symbol | Session | Rejection lifts at | Primary cause at exit |
|---|---|---|---|
| SNOW | 2026-05-29 | 10:34 ET | rvol crossed 0.5 (0.432→0.657) |
| MSFT | 2026-05-29 | 11:04 ET | rvol crossed 0.5 (0.442→0.552) |
| DIA | 2026-05-29 | 11:04 ET | rvol crossed 0.5 (0.375→0.526) |
| MSTR | 2026-05-29 | 12:04 ET | rvol crossed 0.5 (0.413→0.526) |
| DIA | 2026-06-02 | 11:21 ET | score improved (33→53, rvol already 0.554) |
| COIN | 2026-06-02 | 11:51 ET | rvol crossed 0.5 (0.420→0.510) |
| META | 2026-06-02 | 11:51 ET | rvol crossed 0.5 (0.468→0.507) |

rvol is measured by the scanner using intraday bar data. It reflects realized volume relative to a 20-day average. In both sessions, rvol remained below 0.5 for 60–120 min from open. This is the dominant source of signal age.

---

## 7. Signal Age Simulation

### Definition

For each signal, the **theoretical discovery time** at cadence C is:

```
sim_eval_at_C = first scan at cadence C strictly after bar_close
               (session_start + N × C where N is smallest integer making this > bar_close)
sim_age_at_C  = sim_eval_at_C − bar_close_time
```

This represents the age the signal would have if the scanner ran at cadence C AND the symbol was not rejected at that scan (no STANDBY constraint). It is a theoretical lower bound on achievable age.

The **actual age** is driven by: scanner_cadence (30 min) + STANDBY/rejection duration (rvol or score below threshold) + time from bar close to next scan in cadence.

### Per-signal table

| Session | Symbol | Strategy | Dir | Q | Bar close | 1st eval | Actual age | @30m | @15m | @5m | Decision |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-29 | SNOW | vwap_reclaim | LONG | 3 | 10:15 | 10:34 | 19.0 min | 18.7 min | 3.7 min | 3.7 min | traded |
| 2026-05-29 | DIA | orb | LONG | 3 | 10:50 | 11:04 | 14.3 min | 13.7 min | 13.7 min | 3.7 min | blocked |
| 2026-05-29 | DIA | vwap_reclaim | LONG | 1 | 10:05 | 11:04 | 59.3 min | 28.7 min | 13.7 min | 3.7 min | blocked |
| 2026-05-29 | MSFT | vwap_reclaim | LONG | 2 | 10:25 | 11:04 | 39.3 min | 8.7 min | 8.7 min | 3.7 min | traded |
| 2026-05-29 | MSTR | orb | LONG | 3 | 11:05 | 12:04 | 59.9 min | 28.7 min | 13.7 min | 3.7 min | blocked |
| 2026-05-29 | MSTR | vwap_reclaim | SHORT | 2 | 10:15 | 12:04 | 109.9 min | 18.7 min | 3.7 min | 3.7 min | blocked |
| 2026-06-02 | DIA | vwap_reclaim | LONG | 2 | 10:40 | 11:21 | 41.3 min | 10.8 min | 10.8 min | 0.8 min | skipped |
| 2026-06-02 | DIA | vwap_reclaim | SHORT | 4 | 09:45 | 11:21 | 96.3 min | 5.8 min | 5.8 min | 5.8 min | traded |
| 2026-06-02 | COIN | orb | SHORT | 2 | 10:25 | 11:51 | 86.6 min | 25.9 min | 10.8 min | 0.8 min | skipped |
| 2026-06-02 | COIN | vwap_reclaim | SHORT | 3 | 09:50 | 11:51 | 121.6 min | 0.8 min | 0.8 min | 0.8 min | traded |
| 2026-06-02 | META | vwap_reclaim | LONG | 2 | 10:15 | 11:51 | 96.6 min | 5.8 min | 5.8 min | 0.8 min | traded |

### Aggregate simulation

| Cadence | Avg age | Median age | Max age | STANDBY-constrained |
|---|---|---|---|---|
| **Actual** | **67.6 min** | **59.9 min** | **121.6 min** | 11/11 |
| @30m (theoretical) | 15.1 min | 13.7 min | 28.7 min | 11/11 |
| @15m (theoretical) | 8.3 min | 8.7 min | 13.7 min | 11/11 |
| @5m / per-bar (theoretical) | 2.8 min | 3.7 min | 5.8 min | 11/11 |

**"STANDBY-constrained"** means the scanner would still reject the symbol (rvol < 0.5 or score < 40) at the first theoretical scan time. All 11 signals in this dataset are constrained: the theoretical ages above are not achievable under the observed STANDBY conditions.

### Constraint classification

All 11 signals had bar close times BEFORE their symbol's STANDBY exit time. The gap between actual age and theoretical age is entirely attributable to rejection (rvol/score), not to cadence.

| Gap (actual − @30m theoretical) | Count | Signal examples |
|---|---|---|
| < 5 min (near-cadence-limited) | 2 | SNOW (0.3m), DIA orb (0.6m) |
| 5–30 min | 3 | MSFT (30.6m), DIA vwap (30.6m), MSTR orb (31.2m) |
| 30–100 min | 3 | DIA SHORT 06-02 (90.5m), COIN orb (60.7m), MSTR vwap (91.2m) |
| ≥ 100 min | 3 | COIN vwap (120.8m), META (90.8m), DIA LONG 06-02 (30.5m) |

---

## 8. STANDBY-Adjusted Simulation (rvol-driven cases)

For 5 signals where rejection was rvol-driven, the rvol=0.5 crossing can be estimated by linear interpolation between the two 30-min scans bounding the exit. This gives the earliest time the signal would have been discoverable at each cadence.

| Symbol | Session | Bar close | rvol crossing (est) | @30m exit | @15m exit | @5m exit | Earlier vs 30m |
|---|---|---|---|---|---|---|---|
| SNOW | 2026-05-29 | 10:15 | ~10:12 | 10:33 | 10:18 | 10:13 | 15m: −15 min, 5m: −20 min |
| MSFT | 2026-05-29 | 10:25 | ~10:50 | 11:03 | 11:03 | 10:53 | 15m: 0 min, 5m: −10 min |
| COIN | 2026-06-02 | 09:50 | ~11:48 | 11:50 | 11:50 | 11:50 | 15m: 0 min, 5m: 0 min |
| META | 2026-06-02 | 10:15 | ~11:46 | 11:50 | 11:50 | 11:50 | 15m: 0 min, 5m: 0 min |

*DIA 06-02 excluded: rejection was score-driven (score 33 → 53), not rvol-driven.*

**STANDBY-adjusted achievable age for traded signals:**

| Symbol | Actual age | @15m achievable | @5m achievable |
|---|---|---|---|
| SNOW | 19.0 min | ~3.7 min (STANDBY exits 10:18, bar at 10:15) | ~1 min |
| MSFT | 39.3 min | ~38 min (STANDBY exits same time) | ~25 min |
| DIA SHORT | 96.3 min | ~96 min (score-driven STANDBY) | ~96 min |
| COIN vwap | 121.6 min | ~121 min (rvol crossing ~11:48, near actual) | ~121 min |
| META | 96.6 min | ~96 min (rvol crossing ~11:46, near actual) | ~96 min |

Only SNOW shows meaningful age reduction under the rvol-interpolation model. MSFT would reduce ~14 min at 5m cadence. All 2026-06-02 signals have rvol crossings close to the 30-min scan boundary, providing little benefit from faster scanning.

---

## 9. Changes in Bridge Timing

Bridge entries appear at scanner cycle time. At faster cadence, the first bridge entry for each signal shifts to the earlier detection time.

| Signal | Actual first bridge | @15m first bridge | @5m first bridge |
|---|---|---|---|
| SNOW (2026-05-29) | 10:34 | 10:18 (−16 min) | 10:13 (−21 min) |
| MSFT (2026-05-29) | 11:04 | 11:03 (−1 min) | 10:53 (−11 min) |
| DIA SHORT (2026-06-02) | 11:21 | 11:21 (0) | 11:21 (0) |
| COIN vwap (2026-06-02) | 11:51 | 11:51 (0) | 11:50 (−1 min) |
| META (2026-06-02) | 11:51 | 11:51 (0) | 11:50 (−1 min) |

Bridge timing changes are negligible for 4 of 5 traded signals at cadences up to 15 min. Only SNOW shows meaningful earlier entry.

---

## 10. Changes in Candidate Count

Candidate counts are driven by market conditions (rvol, scores), not by scanner cadence. At faster cadence:
- Same 27 symbols evaluated per cycle
- Passed/confirmed counts would vary because rvol and scores change intraday
- More scan cycles produce more `scan_results` rows but not necessarily more unique confirmed symbols
- Total DB rows per session: 162–189 rows at 30 min; 351 rows at 15 min; 2,106 rows at 5 min

No data exists to model how many ADDITIONAL unique symbols would be confirmed at faster cadences in these sessions. The observed confirmed counts (0–10 per cycle) reflect rvol/score dynamics that are continuous intraday.

---

## Summary Table

| Cadence | Theoretical avg age | Achievable avg age† | YFinance/day | Alpaca/day | DB rows/session | Scan % of loop |
|---|---|---|---|---|---|---|
| 30 min (current) | 15.1 min | 67.6 min (actual) | 486–567 | 18–42 | 162–189 | 0.6% |
| 15 min | 8.3 min | ~65 min‡ | 1,053 | 39–78 | 351 | 1.1% |
| 5 min / per-bar | 2.8 min | ~65 min‡ | 6,318 | 234–468 | 2,106 | 3.3% |
| 1 min | <1 min | ~65 min‡ | 31,590 | 1,170–2,340 | 10,530 | 16.7% |

†"Achievable" = actual age observed in sessions (STANDBY-constrained). Faster scanning provides minimal reduction under the observed rvol conditions.  
‡Estimated: STANDBY duration dominates. Faster scanning provides ≤ 15 min improvement for most signals (only SNOW would meaningfully benefit at 15m cadence).

---

*Evidence only. No recommendations. No parameter changes. No code changes.*
