# Repo Investigation & Next-Session Prep — 2026-07-22

## Repository map

`Borgimus/Apps-` contains three applications:

| App | Where | State |
|---|---|---|
| Options trading research system | `app/`, `main.py` on `main`; actively developed on `claude/options-trading-research-system-TIU0p` (PR #3, open) | **Active** — Phase 3 paper evaluation in progress |
| Multi-agent AI workspace | `multi-agent-workspace/` on `main` | Complete MVP, roadmap in `multi-agent-workspace/docs/limitations-roadmap.md` |
| ICT liquidity sweep app | branch `claude/ict-liquidity-sweep-app-a3ep45` (PR #2, open) | Parked since 2026-06-18 |

The **next session** is **Phase 3 Session 7** of the paper-trading evaluation, run from
branch `claude/options-trading-research-system-TIU0p`.

## Where the evaluation stands (from `evaluation/phase3_tracking.json`)

- Phase 3 sessions 1–6 complete and valid (S6: 2026-07-20). Cumulative: 14 trades,
  5W/9L, **net +$83.00**, profit factor 1.51.
- Fix 12 (stale-cancel terminal-state confirmation) validated in S5 and sustained in S6 —
  S6 was the first session with fully clean order accounting (3 submitted / 3 filled /
  0 stale-cancels / 0 API errors).
- Shadow book v2 active from S7 (S6/S7 boundary, observability only): logs capacity-blocked
  ORB/VWAP signals to `evaluation/shadow_book.jsonl`; analysis via `scripts/shadow_report.py`.
- Config freeze holds: no threshold/parameter changes permitted; shadow-book baseline plan
  requires ≥5 more sessions (through ~S11) with configuration unchanged.
- `HANDOFF.md` on the session branch is stale (Phase 2 era, "S19 final session") — Phase 3
  superseded it; `evaluation/phase3_tracking.json` + `evaluation/phase3_eval_protocol.md`
  are current.

## Pre-session checks performed today (06:23–06:30 ET)

| Check | Result |
|---|---|
| Market day | Wed 2026-07-22 — regular full session |
| Dependencies (`pip install -r requirements.lock`, Python 3.11.15) | ✅ installed; `fastapi/uvicorn/sqlalchemy/yfinance/pandas` import clean |
| Fingerprint `python scripts/capture_session_fingerprint.py --verify` | ✅ all four frozen hashes match Phase 3 baseline at commit `f1880ac`; working tree clean |
| Git state on session branch | ✅ clean, 0 unpushed commits |
| CI-gated pytest subset | 429 passed, 2 skipped, **4 failed** — all in `tests/test_multi_trade_guards.py`, stale `MagicMock` fixtures missing `position.eod_exit_time` (predates EOD-cutoff check, commit `2705278`). Known test debt, not a trading defect. |
| `scripts/readiness_check.py` | Ran; all bar fetches **403 Forbidden** — expected, see blocker below |
| `LIVE_TRADING_ENABLED` | not set (defaults false) — paper mode preserved |

## ❌ Single blocker: Alpaca paper credentials

This fresh container has **no `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`** (env or `.env` —
`.env` is gitignored and does not survive container recycling). Without them:

- intraday bars (Alpaca IEX feed since the S11-era fix) return 403,
- the broker probe / paper-endpoint validation cannot run,
- `scripts/session_runner.py` cannot trade.

**Action for the user:** set the Alpaca paper credentials as environment variables in the
Claude Code environment settings (so they survive VM teardown), or provide `.env` at
session start. Account expected: `alpaca-paper-4UBN` per the Phase 3 fingerprint.

## S7 launch runbook (once credentials are present)

1. `git checkout claude/options-trading-research-system-TIU0p` — confirm `git status` clean.
2. `pip install -r requirements.lock` if the VM is fresh; verify
   `python3 -c "import fastapi, uvicorn, sqlalchemy"`.
3. `python scripts/capture_session_fingerprint.py --verify --check-broker` — must pass,
   0 open positions / 0 open orders at broker.
4. Launch at ~09:31 ET (post-open start avoided the standby issue in S6):
   `python -u scripts/session_runner.py --poll 30 --reconcile-interval 10 2>&1 | tee -a logs/session_2026-07-22.log`
   as a background task (`run_in_background`, timeout ≥ 4h) — the running task is the VM keepalive.
5. Within ~60 s confirm `[cycle 1]` in the log; a traceback means relaunch before ending the turn.
6. Session auto-terminates 12:30 ET → `python scripts/eod_check.py`, verify broker 0/0,
   update `evaluation/phase3_tracking.json`, write `logs/post_session_report_2026-07-22.md`,
   `git add -f logs/ evaluation/`, commit, push.

Permanent constraints: `LIVE_TRADING_ENABLED=false`, paper mode only, no threshold or
strategy changes, broker-reported fills authoritative, S9/S10 (2026-06-24/25) scan data
permanently excluded.
