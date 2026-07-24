# Project Handoff Document
**Generated:** 2026-06-30 | **Updated:** 2026-07-10
**Branch:** `claude/options-trading-research-system-TIU0p`  
**Last commit:** S18 EOD documentation

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

**Status:** 9 of 10 sessions complete. 1 remaining (S19 — final session).

| Session | Date | P&L | Trades | Wins | data_clean | Status | Running P&L |
|---------|------|-----|--------|------|------------|--------|-------------|
| S6 | 2026-06-15 | +$35.00 | 3 | 2 | FALSE | complete | +$35.00 |
| S7 | 2026-06-17 | $0.00 | 0 | — | TRUE | complete | +$35.00 |
| S8 | 2026-06-18 | -$7.00 | 3 | 1 | FALSE | complete | +$28.00 |
| S9 | 2026-06-24 | $0.00 | 0 | — | TRUE | complete | +$28.00 |
| S10 | 2026-06-25 | $0.00 | 0 | — | TRUE | complete | +$28.00 |
| S11 | 2026-06-29 | -$7.00 | 2 | 1 | FALSE | complete | +$21.00 |
| S12 | 2026-06-30 | $0.00 | 0 | — | — | **VOIDED** | — |
| S13 | 2026-07-01 | $0.00 | 0 | — | — | **VOIDED** | — |
| S14 | 2026-07-06 | $0.00 | 0 | — | — | **VOIDED** | — |
| S15 | 2026-07-07 | $0.00 | 0 | — | — | **VOIDED** | — |
| S16 | 2026-07-08 | $0.00 | 0 | — | FALSE | complete | +$21.00 |
| S17 | 2026-07-09 | **+$434.00** | 2 | 1 | **TRUE** | complete | **+$455.00** |
| S18 | 2026-07-10 | **-$53.00** | 3 | 0 | FALSE | complete | **+$402.00** |

**Phase 1 baseline (S1–S5, carry-forward):** -$276.00, 14 trades, 2 wins  
**Combined P&L (valid sessions through S17):** +$179.00, 24 trades, 7 wins, 29.2% win rate  
**S18 note:** 66-min late start due to VM teardown; user confirmed valid. MARA EOD exit ($1.39) did not fill — closed post-session at $1.18 (broker confirmed). Corrected P&L: -$53.

**Midpoint analysis completed:** `research/phase2_midpoint_analysis_2026-06-25.md`  
**Protocol document:** `evaluation/phase2_eval_protocol.md`  
**Tracking file:** `evaluation/phase2_tracking.json`

---

## Next Immediate Task: Session 19 (S19) — Final Session

S18 marked valid (user confirmed). S19 is the 10th and final required session.

**S17 recap:** +$434 (META take_profit 100% gain, RIVN breakeven). First data_clean=TRUE session with fills.
**S18 recap:** -$53 (SOFI -$12, SPY -$10, MARA -$31 corrected). 3 trades, 0 wins. 66-min late start (VM teardown, user confirmed valid).

---

### CRITICAL: VM Keepalive — Correct Model

**A successfully running background task keeps the VM alive.** Proven by S17: user messaged at 09:49 ET, did not message again until 11:14 ET (85-minute gap) — VM survived throughout because the background task was running the entire time.

**Actual mechanism:**
- A Bash background task (`run_in_background=True`) holds an active process slot in the Firecracker container
- While that process runs, the VM stays alive regardless of user message gaps
- The VM only dies if: (a) the background task exits, leaving no active process, AND (b) no user message arrives within ~13 minutes

**Why S18 first attempt died:**
1. Fresh VM started with no Python dependencies installed (fastapi, uvicorn, etc. absent)
2. Background task launched: `python -u scripts/session_runner.py ...`
3. Task immediately crashed with `ModuleNotFoundError: No module named 'fastapi'`
4. No background task running → no keepalive → VM died ~13 min after last turn
5. User went idle from 09:01 ET; VM torn down at ~09:14 ET

**The stop hook** (`/root/.claude/stop-hook-git-check.sh`) fires after **every** Claude turn. If there are uncommitted/unpushed changes, it forces another turn (exit 2). This can be the last Claude turn before a long idle — but only matters if the background task then fails to start.

---

### S19 Launch Protocol

#### Step 1 — Pre-launch: clean git state
Commit and push **everything** before the session launch turn. No untracked files, no uncommitted changes.
```bash
git status        # must show "nothing to commit, working tree clean"
git log --oneline origin/HEAD..HEAD   # must show 0 unpushed commits
```
This prevents the stop hook from forcing additional turns after launch.

#### Step 2 — Install dependencies (CRITICAL on fresh VM)
Before launching the background task, verify deps are installed:
```bash
python3 -c "import fastapi, uvicorn, sqlalchemy" 2>&1
```
If any import fails, run:
```bash
pip install -r requirements.txt
```
This is the root fix. If `session_runner.py` can't import its deps, the background task exits immediately and the VM loses its keepalive.

#### Step 3 — Launch background task
```bash
python -u scripts/session_runner.py --poll 30 --reconcile-interval 10 \
  2>&1 | tee -a logs/session_YYYY-MM-DD.log
```
Launched as `Bash(run_in_background=True, timeout=14400000)`.

#### Step 4 — Verify task actually started
Check the log within ~60 seconds of launch:
```bash
tail -5 logs/session_YYYY-MM-DD.log
```
Must show `[cycle 1]` or startup log lines. If you see a Python traceback, the task has already died — fix the error and relaunch before ending the turn.

#### Step 5 — Optional /loop for monitoring
A `/loop` heartbeat is useful for monitoring (cycle count, P&L, position status) but is **not required for VM keepalive** as long as the background task is running. If desired:
```
/loop 15m check session status: tail -5 logs/session_YYYY-MM-DD.log
```

---

### Pre-Session Checklist
1. Verify market is open (not a half-day or holiday)
2. Check `LIVE_TRADING_ENABLED=false` in env
3. Confirm `git status` is clean
4. Confirm all prior session docs committed and pushed
5. Run dependency check (Step 2 above)
6. Launch background task (Step 3 above) before 09:30 ET
7. Verify [cycle 1] appears in log before ending the turn (Step 4)

### Session Validity Rules

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

## Recent Code Changes (S11–S12 Era)

Three changes were explicitly authorized as exceptions to the Phase 2 freeze:

### 0. Intraday bars → Alpaca (commit `ad85192`)
File: `app/data/yfinance_data.py`

**What changed:**  
`YFinanceDataSource.get_intraday_bars()` now fetches bars from the Alpaca Market Data API (IEX feed) instead of yfinance. Yahoo Finance began rate-limiting (HTTP 429) on 2026-06-26; every cycle in S11 window 3 hit the 429 for MSFT/RIVN/PLTR signal-generation bars. The fix adds `_make_alpaca_data_client()` and `_fetch_alpaca_bars_sync()` module-level helpers, and replaces the yfinance call with an Alpaca paginated fetch.

**Effect:** All intraday bars for signal generation now come from Alpaca. No longer dependent on Yahoo Finance for anything in the trading loop.

### 1. yfinance Integration (commit `081cc03`)
File: `app/scanning/yfinance_scanner.py`

**What changed:**
- **Batch fetch**: Two `yf.download()` calls (daily 1d + intraday 5m) replace 54 sequential `Ticker.history()` calls. All symbols fetched in parallel via `threads=True`.
- **Requests backend**: `_make_session()` creates a `requests.Session(verify=CA_BUNDLE)` and passes it to all yfinance calls. This bypasses curl_cffi TLS failures in the proxy-terminated container environment. Uses `REQUESTS_CA_BUNDLE` env var (already set to `/root/.ccr/ca-bundle.crt`).
- **Optional Yahoo auth**: `_configure_yfinance()` calls `yf.Auth(email, password)` if `YF_EMAIL` and `YF_PASSWORD` env vars are set. Auth is optional — scanner works unauthenticated. If auth credentials are wanted, set those vars before session launch.

**What did not change:** All scoring logic, thresholds, signal generation, and strategy logic remain identical.

### 2. Universe Expansion (commit `eceb15c`)
File: `config/ticker_universe.yaml`

Added `high_beta_liquid` to `enabled_groups`. Scanner universe expanded from 27 to 38 symbols.

**Active groups:**
| Group | Count | Symbols |
|-------|-------|---------|
| core_etfs | 9 | SPY, QQQ, IWM, DIA, XLK, XLF, XLE, XLY, SMH |
| mega_cap | 7 | AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA |
| liquid_growth | 11 | AMD, ARM, AVGO, COIN, MSTR, PLTR, SHOP, SNOW, NET, CRWD, DDOG |
| high_beta_liquid | 11 | RIVN, SOFI, HOOD, MARA, RIOT, ROKU, DKNG, UBER, LYFT, SQ, AFRM |

Total: 38 symbols (max_total_symbols cap = 40)

---

## Post-Phase 2 Code Changes (Deferred — Do Not Implement During Phase 2)

Five defects identified, frozen under protocol:

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

**S9 scan_results (2026-06-24) and S10 scan_results (2026-06-25) are PERMANENTLY EXCLUDED from all diagnostics.**

Both sessions had total yfinance daily data failures. All rows contain sentinel values: `rvol=0.0`, `atr_pct=0.0`, `score=10.0`, `trend=unknown`. These are not real scanner observations.

Any DB query on `scan_results` must filter:
```sql
WHERE date(scanned_at) NOT IN ('2026-06-24', '2026-06-25')
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
| `app/scanning/yfinance_scanner.py` | Data fetch + SymbolMetrics builder (batch fetch, requests backend) |
| `app/scanning/candidate_scorer.py` | Scoring + signal direction logic |
| `config/ticker_universe.yaml` | Symbol groups and scan config (38 symbols active) |
| `research/phase2_midpoint_analysis_2026-06-25.md` | Midpoint Q1–Q7 review |
| `research/clean_session_analysis_2026-06-12.md` | Phase 1 baseline analysis |

---

## Known Infrastructure Issues

**Firecracker VM teardown (CRITICAL — affects all sessions):** Each Claude Code conversation turn runs in a Firecracker microVM. After ~13 min of idle time between conversation turns, the entire VM is torn down at the hypervisor level. No process survives — not tmux servers, not orphaned processes with their own PGID. The only reliable solution is `/loop` skill at 5–8 min intervals to keep the VM alive through active turns. S9/S10 succeeded because health checks were performed every 5–10 min during session monitoring. S11/S12 had longer idle gaps and were killed repeatedly.

**yfinance 429 rate limit on intraday bars:** Yahoo Finance rate-limited all yfinance bar fetches starting 2026-06-26. This affected S11 window 3 (cycles 1–5 of 11:29 ET window). **FIXED:** `get_intraday_bars()` in `app/data/yfinance_data.py` now uses Alpaca Market Data API (IEX feed). Active for S12+.

**yfinance daily data failures:** Recurring issue where yfinance returns empty DataFrames for daily OHLCV data. Occurred on S9 (2026-06-24) and S10 (2026-06-25). When this happens:
- Scores collapse to 10.0 (artifact from rsi=50.0 sentinel)
- All symbols rejected via `data_fetch_error`
- Session produces 0 trades but is still classified STANDBY
- The batch fetch + requests backend in `yfinance_scanner.py` may improve reliability

**Commit workflow for logs/reports:** `.gitignore` blocks `logs/` and `evaluation/reports/`. Use `git add -f` to force-add these files when committing post-session documentation.

---

## Voided Attempts (Excluded from All Statistics)

| Date | Attempt | Death | Cause | Positions |
|------|---------|-------|-------|-----------|
| 2026-06-22 | S9 attempt 1 | Cycle 22 (09:40:39 ET) | bash shell recycled (`&` backgrounding) | 0 |
| 2026-06-23 | S9 attempt 2 | Cycle 73 (10:05:22 ET) | SIGKILL from container resource manager (`nohup` only blocks SIGHUP) | 0 |
| 2026-06-30 | S12 | Cycle 26 (10:49 ET, last of 3 windows) | Firecracker VM teardown × 3, killed before 12:30 ET with 0 new positions | 0 new |

**S11 (2026-06-29):** Not voided but validity TBD. Killed at 11:47 ET with RIVN open. 2 broker-confirmed fills. See S11 validity decision required above.

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

**S11 (2026-06-29) — 2 completed trades, -$7.00, data_clean=FALSE, validity TBD**
- MSFT SHORT (vwap_reclaim): direct fill, DTE=0, win (+$1.00, trailing_stop, hold=186s)
  - scanner=70, quality=2, age=100.1 min, rvol=0.535, entry spread=4.4%
- RIVN LONG (orb): direct fill, DTE=3, loss (-$8.00, max_hold carryover, hold=78,656s)
  - scanner=60, quality=2, age=105.1 min, rvol=0.681, entry spread=6.7%, exit spread=41.5%
  - Entry limit $0.31, filled $0.29; exit $0.21 triggered in S12 at startup
- IWM SHORT (orb): DTE=0 day order $0.19, stale_cancelled (never filled before VM kill)

**Notable patterns (through S11):**
- Direct fills outperform recovered: direct N=7 (4 wins, 57%, avg=+$3.57/trade) vs recovered N=15 (2 wins, 13%, avg=-$18.67/trade)
- DTE=0 win rate improved slightly to 27% (3/11) but pnl essentially flat at -$116
- 90–120 min signal age now most populated bucket (N=5), showing 40% win rate (-$63 total)
- Quality=3 average pnl (-$0.80/trade) significantly better than quality=2 (-$11.44/trade)
