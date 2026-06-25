# Project Handoff Document
**Generated:** 2026-06-25  
**Branch:** `claude/options-trading-research-system-TIU0p`  
**Last commit:** `8fa8755` — Session 9 (2026-06-24): zero-trade STANDBY, data_clean=TRUE

---

## What This Project Is

An intraday options trading research system operating under a **frozen evaluation protocol (Phase 2)**. The system scans equities for momentum setups, generates option entry signals, manages positions with trailing stops, and records all fills and P&L. It is currently running in paper evaluation mode only — no live capital at risk.

**Permanent constraints (never change these):**
- `LIVE_TRADING_ENABLED=false`
- `PAPER_EVALUATION_MODE=true`
- Do not place trades, change strategy logic, change thresholds, or enable live trading
- Broker-reported fills are authoritative if any fill-price discrepancy occurs
- Phase 2 code changes: defect fixes only (incorrect accounting, trade recording, position management, risk enforcement, data corruption) — no optimization, no parameter changes

---

## Phase 2 Evaluation Protocol

**Goal:** Collect 10 clean sessions (S6–S15) to answer 7 primary research questions (Q1–Q7) about signal quality, fill path, DTE, spread, scanner score, and strategy attribution.

**Status:** 4 of 10 sessions complete. 6 remaining.

| Session | Date | P&L | Trades | Wins | data_clean | Running P&L |
|---------|------|-----|--------|------|------------|-------------|
| S6 | 2026-06-15 | +$35.00 | 3 | 2 | FALSE | +$35.00 |
| S7 | 2026-06-17 | $0.00 | 0 | — | TRUE | +$35.00 |
| S8 | 2026-06-18 | -$7.00 | 3 | 1 | FALSE | +$28.00 |
| S9 | 2026-06-24 | $0.00 | 0 | — | TRUE | +$28.00 |

**Phase 1 baseline (S1–S5, carry-forward):** -$276.00, 14 trades, 2 wins  
**Combined P&L (all sessions):** -$248.00, 20 trades, 5 wins, 25.0% win rate

**Midpoint review triggers after S10 completes** (next session).

**Protocol document:** `evaluation/phase2_eval_protocol.md`  
**Tracking file:** `evaluation/phase2_tracking.json`

---

## Next Immediate Task: Session 10 (S10)

### Launch Command
```bash
tmux new-session -d -s s10 -x 220 -y 50
tmux send-keys -t s10 "python -u scripts/session_runner.py --poll 30 --reconcile-interval 10 2>&1 | tee logs/session_YYYY-MM-DD.log" Enter
```
Replace `YYYY-MM-DD` with the actual session date.

### Why tmux (critical)
All prior `&` and `nohup` launch attempts were killed by the remote container's process manager (SIGKILL) during conversation inactivity gaps. tmux server processes survive shell recycling entirely. S9 (2026-06-24) was the first successful full-window session using tmux.

### Pre-Session Checklist
1. Verify market is open and not a half-day
2. Confirm no scheduled broker maintenance
3. Check `LIVE_TRADING_ENABLED=false` in env
4. Run pre-session scan: `python scripts/pre_session_scan.py` (runs ~3 min for 27 symbols)
5. Launch via tmux before 09:30 ET

### Required Health Checks
Run at: 09:35, 09:45, 10:00, 11:00, 12:00 ET
```bash
tmux list-sessions        # verify s10 alive
tmux capture-pane -t s10 -p | tail -20    # see recent log lines
```
Verify: session alive, cycle count incrementing, positions=0 or tracked, entries=known

### Session Validity Rules
- Must run full window (09:30–12:30 ET)
- If killed before 12:30 ET with 0 positions: VOIDED (exclude from Phase 2)
- If killed before 12:30 ET with open positions: requires broker reconciliation
- data_clean=TRUE only if: all fills verified to broker, no unresolved reconciler flags

### EOD Shutdown
Session auto-terminates at 12:30 ET. After completion:
1. Run EOD check: `python scripts/eod_check.py`
2. Verify broker: 0 positions, 0 open orders
3. Update `evaluation/phase2_tracking.json` with session entry
4. Write post-session report to `logs/post_session_report_YYYY-MM-DD.md`
5. Commit all: `git add -f logs/ evaluation/` then `git commit` then `git push`

---

## Post-Phase 2 Code Changes (Deferred — Do Not Implement During Phase 2)

Five defects identified during S9 postmortem, frozen under protocol:

1. **score=0 on data failure** (`app/scanning/candidate_scorer.py`)  
   When `m.errors` is non-empty, suppress all scoring → `score=0`.  
   Current bug: `rsi=50.0` sentinel earns 10 RSI points unconditionally → score=10 artifact.

2. **data_fetch_error suppresses downstream rejections** (`candidate_scorer.py`)  
   When `data_fetch_error` present in rejections, list it alone — suppress `low_volume_chop` and `atr_too_small` (they are cascading artifacts of the data failure, not independent findings).

3. **INVALID_DATA session status** (session runner)  
   Add session status `INVALID_DATA` distinct from `STANDBY`.  
   STANDBY = scanner operated, market not qualifying.  
   INVALID_DATA = scanner input missing/corrupted, true market state unknown.

4. **Entry halt on widespread data failure** (session runner)  
   If data failure affects >50% of symbols, halt entry attempts until valid data resumes.

5. **`data_valid` flag on scan_results** (schema + scanner)  
   Add `data_valid: bool` column to scan_results. Set `False` for sentinel rows (errors non-empty or `is_data_stale=True`).

---

## Permanent Data Exclusion Rule

**S9 scan_results (2026-06-24) are PERMANENTLY EXCLUDED from all diagnostics.**

All 864 rows contain sentinel values: `rvol=0.0`, `atr_pct=0.0`, `score=10.0`, `trend=unknown`. These are not real scanner observations — they are zero-sentinel fallback values caused by a total yfinance daily data failure.

Any DB query on `scan_results` must filter:
```sql
WHERE date(scanned_at) != '2026-06-24'
-- or, once data_valid flag is added:
WHERE data_valid = TRUE
```

---

## Key File Locations

| File | Purpose |
|------|---------|
| `scripts/session_runner.py` | Main trading loop |
| `scripts/eod_check.py` | EOD broker reconciliation |
| `evaluation/phase2_tracking.json` | Phase 2 P&L and session registry |
| `evaluation/phase2_eval_protocol.md` | Full protocol document |
| `evaluation/ledger.json` | All-sessions P&L ledger |
| `evaluation/reports/YYYY-MM-DD.json` | Auto-generated session reports |
| `logs/session_YYYY-MM-DD.log` | Session log (cycle-by-cycle) |
| `logs/post_session_report_YYYY-MM-DD.md` | Human-readable post-session report |
| `app/scanning/yfinance_scanner.py` | Data fetch + SymbolMetrics builder |
| `app/scanning/candidate_scorer.py` | Scoring + signal direction logic |
| `research/clean_session_analysis_2026-06-12.md` | Phase 1 baseline analysis |

---

## Known Infrastructure Issues

**yfinance daily data failures:** Recurring transient issue where `ticker.history()` returns empty DataFrame for some or all symbols. S9 was worst-case (all 27 symbols affected). When this happens:
- Scores collapse to 10.0 (artifact)
- All symbols rejected via `data_fetch_error`
- Session produces 0 trades but is still classified STANDBY
- Does not affect session validity if scanner operated correctly

**Commit workflow for logs/reports:** `.gitignore` blocks `logs/` and `evaluation/reports/`. Use `git add -f` to force-add these files when committing post-session documentation.

---

## Voided Attempts (Excluded from All Statistics)

| Date | Attempt | Death | Cause | Positions |
|------|---------|-------|-------|-----------|
| 2026-06-22 | S9 attempt 1 | Cycle 22 (09:40:39 ET) | bash shell recycled (`&` backgrounding) | 0 |
| 2026-06-23 | S9 attempt 2 | Cycle 73 (10:05:22 ET) | SIGKILL from container resource manager (`nohup` only blocks SIGHUP) | 0 |

---

## Cumulative Trade Detail (Phase 2 sessions with trades)

**S6 (2026-06-15) — 3 trades, +$35.00, data_clean=FALSE**
- XLE LONG (vwap_reclaim): recovered fill, loss
- PLTR SHORT (vwap_reclaim): recovered fill, win
- DIA SHORT (orb): recovered fill, win + DIA carryover resolved EOD

**S8 (2026-06-18) — 3 trades, -$7.00, data_clean=FALSE**
- AAPL LONG (vwap_reclaim): direct fill, DTE=0, loss (-$12 actual, -$14 journal)
- PLTR SHORT (vwap_reclaim): direct fill, DTE=0, win (+$25 actual, +$22 journal)
- DIA SHORT (orb): direct fill, DTE=0, loss (-$20 actual, -$22 journal)
- All exits via trailing_stop. All fills better than limit (marketable_limit mode).

**Notable pattern:** All 6 Phase 2 trades were DTE=0. All S8 fills were direct (not recovered). Direct fills outperform recovered significantly in current data (+$6.40 vs -$18.67 avg per trade through S8).
