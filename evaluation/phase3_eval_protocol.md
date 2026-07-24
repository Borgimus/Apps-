# Phase 3 Evaluation Protocol

**Created:** 2026-07-12  
**Supersedes:** `post_fix_eval_protocol.md` (Phase 2)  
**Status:** ACTIVE  
**Phase 3 boundary:** First session on or after 2026-07-12 (all P1–P7 fixes applied)  
**Review after:** 5 completed Phase 3 sessions

---

## 1. Why Phase 3 Starts Here

### Complete defect-fix history (P1–P7)

| Priority | Fix | Effect on prior session data |
|----------|-----|------------------------------|
| P1 | Removed `.env` and `export_session_*.json` from git history | Credential hygiene — no effect on session data |
| P2 | `EXIT_PENDING` state machine in `OpenPosition` | All pre-P7 sessions: exit orders could be placed while another exit was in flight; `should_exit()` could trigger on a position with an outstanding exit order; realized P&L and hold duration unreliable for any trade where exit monitoring and order poll interleaved |
| P3 | Kill-switch no longer gates exits or EOD liquidation | All pre-P7 sessions: if the kill-switch was activated while positions were open, exits and EOD liquidation were blocked; positions could be stranded indefinitely |
| P4 | Paper endpoint validation at startup (URL + credential probe) | Infrastructure change — no effect on existing data; prevents future live-vs-paper confusion |
| P5 | Deleted `paper_trader.py` and `paper_loop.py`; single submission path | All pre-P7 sessions: two additional order-submission code paths existed; any session run with these paths is uncontrolled |
| P6 | `RiskManager` daily counters restored from DB on restart; `DBPosition` synced each cycle; exit order ID persisted to `trade_journal` on placement | All pre-P7 sessions: any intraday restart zeroed `_entries_today`, `_pending_entries`, and `_daily_pnl`; daily trade limit and daily loss limit checks were unreliable after a restart |
| P7 | CI with locked dependencies (`requirements.lock`, `.github/workflows/ci.yml`) | Reproducible builds enforced; syntax gate added |

### Previously established contamination (Phase 1/2 boundary)

Phase 2's `post_fix_eval_protocol.md` established a boundary at 2026-06-05 based on Bugs A–E:

| Bug | Fix commit | Contamination |
|-----|-----------|---------------|
| A | `d2935c9` | `pending_entries` leaked on stale-cancel |
| B | `d2935c9` | fill silently dropped on 422 race |
| C | `79d8681` | exit P&L recorded at midpoint, not bid |
| D | `79d8681` | duplicate exit 403 corrupted `daily_pnl` |
| E | `79d8681` | session ran past EOD |

**Phase 2 sessions (2026-06-05 through 2026-07-11) were believed clean under the Phase 2 boundary but carry Phase 3 contamination flags from P2–P6.**

### What this means for existing data

| Session range | Phase | Use |
|---------------|-------|-----|
| 2026-05-11 – 2026-06-04 | pre_phase3 | Engineering evidence only. Bugs A–E + P2–P6 contamination. |
| 2026-06-05 – 2026-07-11 | pre_phase3 | Engineering evidence only. P2–P6 contamination even if Phase 2 `data_clean=True`. |
| 2026-07-12 onward | phase3 | Phase 3 clean cohort. Eligible for strategy-performance analysis after acceptance criteria met. |

All 23 sessions in `evaluation/ledger.json` are tagged `phase: "pre_phase3"` with explicit `contamination_flags`. Their `data_clean` flag reflects Phase 2 criteria only and must not be read as Phase 3 clean.

---

## 2. Session Configuration (frozen)

Run every Phase 3 session with **exactly** these settings. Any change invalidates cross-session comparability.

### Environment (.env)

```
LIVE_TRADING_ENABLED=false
PAPER_EVALUATION_MODE=true
PAPER_EVAL_PERMISSIVE_ENTRY_MODE=true
UNIVERSE_SCAN_INTERVAL_MINUTES=5
RISK_MAX_TRADES_PER_DAY=3
UNIVERSE_MAX_ACTIVE_POSITIONS=2
UNIVERSE_MAX_SYMBOLS_TRADED_PER_DAY=3
UNIVERSE_MAX_CONTRACTS_PER_POSITION=1
ORB_SLOT_RESERVE_UNTIL=11:30
UNIVERSE_GROUPS_ENABLED=core_etfs,mega_cap,liquid_growth
UNIVERSE_ALLOW_CLI_FALLBACK_WHEN_SCANNER_REJECTS=false
POSITION_EOD_EXIT_TIME=12:30
OPTIONS_ENTRY_LIMIT_PRICE_MODE=marketable_limit
OPTIONS_EXIT_LIMIT_PRICE_MODE=marketable_limit
```

### Launch command

```bash
python scripts/session_runner.py \
  --poll 30 \
  --reconcile-interval 10
```

### What must not change between sessions

- Strategy thresholds (no tuning between sessions)
- Risk limits (`max_risk_per_trade`, `max_spread_pct`, delta targets)
- Universe groups
- ORB slot reserve time
- EOD exit time (12:30 ET)
- Scanner score floor or any scoring weights
- Python version and dependency set (`requirements.lock`)

---

## 2b. Runtime Fingerprint (Phase 3 boundary constraint)

A session does not count toward the Phase 3 clean cohort if the runtime
commit or configuration differs from the frozen baseline without a declared
protocol amendment.

### Frozen baseline identifiers

Recorded in `evaluation/phase3_tracking.json::phase3_fingerprint` at the
Phase 3 readiness commit. The baseline is:

| Field | Meaning |
|-------|---------|
| `commit_sha` | Git SHA of the Phase 3 readiness commit |
| `config_hash_sha256_16` | First 16 hex chars of SHA-256 of `config.yaml` |
| `requirements_lock_hash_sha256_16` | First 16 hex chars of SHA-256 of `requirements.lock` |
| `ticker_universe_hash_sha256_16` | First 16 hex chars of SHA-256 of `config/ticker_universe.yaml` |
| `broker_adapter_hashes_sha256_8` | Per-file 8-char SHA-256 of each broker adapter module |
| `paper_account_identifier` | Alpaca paper account ID (record from dashboard before first session) |
| `db_schema_note` | Human-readable schema version label |
| `first_eligible_session_date` | Earliest date a session counts as Phase 3 |

### Fingerprint verification

Run before each session:

```bash
python scripts/capture_session_fingerprint.py --verify
```

Exit code 0 = fingerprint matches baseline (session is eligible).  
Exit code 1 = fingerprint diverges (session must not count without amendment).

To capture a fresh fingerprint without comparing:

```bash
python scripts/capture_session_fingerprint.py
```

### Protocol amendment procedure

If a controlled change is required (e.g. dependency update, config change):

1. Make the change and commit it.
2. Run `python scripts/capture_session_fingerprint.py` and copy the output.
3. Update `phase3_fingerprint` in `evaluation/phase3_tracking.json` with the new hashes.
4. Add a `protocol_amendments` entry explaining what changed and why.
5. Sessions before and after the amendment are separate comparison groups.

**No change to strategy thresholds, risk limits, or universe groups is permitted between Phase 3 sessions.**  Changes to those fields invalidate cross-session comparability and require a new protocol, not just an amendment.

---

## 3. Pre-Session Checklist

Before starting each Phase 3 session:

- [ ] Run `python scripts/capture_session_fingerprint.py --verify` — must exit 0
- [ ] Confirm `LIVE_TRADING_ENABLED=false` in environment (`echo $LIVE_TRADING_ENABLED`)
- [ ] Confirm `PAPER_EVALUATION_MODE=true`
- [ ] Confirm Alpaca base URL is paper endpoint (`paper-api.alpaca.markets`)
- [ ] Confirm no open positions or pending orders from prior session
- [ ] Confirm `.env` matches Section 2 exactly (diff against last known-good snapshot)
- [ ] Confirm CI is green on current commit (`main` branch protected by P7 gate)
- [ ] Note starting equity from broker account page
- [ ] Confirm `requirements.lock` matches installed packages (`pip freeze | diff - requirements.lock`)

---

## 4. Post-Session Checklist and Acceptance Criteria

A session is accepted into the Phase 3 clean cohort if and only if ALL of the following pass:

### Infrastructure verification (run after session terminates)

- [ ] `LIVE_TRADING_ENABLED=false` confirmed in session log (search for "LIVE_TRADING_ENABLED")
- [ ] Session terminated at or before 12:35 ET (EOD exit time 12:30 + 5 min tolerance)
- [ ] Broker option positions at EOD = 0 (verify in Alpaca dashboard)
- [ ] Broker open orders at EOD = 0
- [ ] No duplicate exit orders: for each trade, broker order history shows exactly one exit order filled
- [ ] No 403 Forbidden responses on exit order placement (search session log for "403")
- [ ] `daily_pnl` from RiskManager matches sum of individual fills (±$5 rounding tolerance)

### P2 exit state machine verification (new in Phase 3)

- [ ] For every closed trade: verify `exit_order_id` in `trade_journal` matches the single filled exit order in broker history
- [ ] No `status=open` rows in `trade_journal` with `exit_order_id` set at session end (these indicate exits that did not confirm)
- [ ] Session log shows no "duplicate exit" or "EXIT_PENDING" warnings for the same position

### P6 counter integrity verification (new in Phase 3)

- [ ] If the runner was restarted intraday: verify session log shows "counters restored from DB" message from `RiskManager`
- [ ] `entries_today` at session end matches count of `status IN ('open','closed')` rows in `trade_journal` for the session date

### Fill verification

- [ ] Every `status=closed` trade in `trade_journal` has `exit_price` matching Alpaca `filled_avg_price` for the exit order (or discrepancy is explained and ledger is corrected)
- [ ] Every `status=closed` trade has `fill_price` matching Alpaca `filled_avg_price` for the entry order

### Records

- [ ] Post-session report filed at `logs/post_session_report_<DATE>.md`
- [ ] Session added to `evaluation/phase3_tracking.json` with `validity` set appropriately
- [ ] Ledger updated: `evaluation/ledger.json` gains a new entry with `phase: "phase3"` and `contamination_flags: []`

**A session that fails any criterion is flagged as `validity: "invalid"` in `phase3_tracking.json`. Its trades are excluded from performance analysis but retained in logs for infrastructure debugging.**

---

## 5. Per-Trade Data to Capture

For every filled trade, record the following. Most fields are captured automatically; the manual-verification fields are marked.

### From broker order history (manual verification required)

| Field | Source | Notes |
|-------|--------|-------|
| `actual_entry_fill` | Alpaca order `filled_avg_price` | Must match `fill_price` in `trade_journal`; flag any discrepancy |
| `actual_exit_fill` | Alpaca order `filled_avg_price` | Must match `exit_price` in `trade_journal`; flag any discrepancy |
| `exit_order_id` | Alpaca order history | Exactly one exit order per position |
| `exit_fill_time` | Alpaca order history | Compare to `exit_time` in `trade_journal` |

### From `signal_bridge` table (automatic)

- `signal_quality_score` (1–4)
- `scanner_score`
- `signal_age_seconds` → convert to minutes
- `confluence_count`
- `universe_group`
- `strategy_id`
- `rvol` at signal time
- `entry_spread_pct`
- `final_decision` and `exact_block_reason`

### From `trade_journal` table (automatic)

- `delta` at entry
- `iv` at entry
- `expiration` → DTE = `expiration - session_date`
- `exit_reason`
- `exit_spread_pct`
- `hold_duration_secs`
- `realized_pnl` (verify against actual fill prices)
- `exit_order_id` (P2 verification: must be set)

### Signal bridge blocking stats (every session)

```sql
SELECT final_decision, exact_block_reason, COUNT(*) as n
FROM signal_bridge
WHERE session_date = '<DATE>'
  AND date(scanned_at) NOT IN ('2026-06-24', '2026-06-25')
GROUP BY final_decision, exact_block_reason
ORDER BY n DESC;
```

---

## 6. Ledger Integrity Requirements

After every Phase 3 session:

1. **Actual fill prices only.** `realized_pnl` must equal `(actual_exit_fill - actual_entry_fill) × 100 × quantity`. Correct the ledger manually if journal prices diverge from broker `filled_avg_price`.

2. **Every filled trade present.** `SELECT COUNT(*) FROM trade_journal WHERE session_date = '<DATE>' AND status = 'closed'` must equal the ledger `total_trades` for that session.

3. **Phase tag set.** New session entries must have `phase: "phase3"` and `contamination_flags: []`.

4. **`phase3_cumulative` is authoritative.** The `cumulative` section includes all sessions (including pre-phase3). The `phase3_cumulative` section is the source of truth for strategy-performance conclusions.

**Phase 3 clean-session query (after 5+ Phase 3 sessions):**

```python
import json
with open('evaluation/ledger.json') as f:
    d = json.load(f)

p3 = [s for s in d['sessions'] if s.get('phase') == 'phase3']
total = sum(s['realized_pnl'] for s in p3)
trades = sum(s['total_trades'] for s in p3)
wins = sum(s['wins'] for s in p3)
print(f"Phase 3 sessions: {len(p3)}, trades: {trades}, wins: {wins}, pnl: {total:.2f}")
```

---

## 7. Minimum Observations Before Performance Conclusions

All phase-1/2 observations reset to zero for Phase 3. No performance conclusions, no parameter changes, no threshold adjustments until:

| Dimension | Minimum Phase 3 observations |
|-----------|------------------------------|
| Quality score | ≥3 trades at each quality level being compared |
| DTE | ≥3 trades at each DTE bucket being compared |
| Universe group | ≥3 trades per group |
| Signal age | ≥3 trades per age bucket |
| Strategy (ORB vs VWAP) | ≥3 completed trades per strategy |
| IV buckets | ≥3 trades per IV quartile |

**Current Phase 3 status (as of 2026-07-12):** No Phase 3 sessions have run yet. All observation counts are zero.

---

## 8. Phase 3 Session Record Format

Add each session to `evaluation/phase3_tracking.json` in this format:

```json
{
  "session": "S<N>",
  "date": "YYYY-MM-DD",
  "validity": "valid | invalid | partial",
  "validity_reason": "if not valid, explain why",
  "pnl_journal": 0.00,
  "pnl_confirmed": 0.00,
  "pnl_note": "any discrepancy explanation",
  "trades": 0,
  "wins": 0,
  "losses": 0,
  "breakeven": 0,
  "orb_trades": 0,
  "vwap_trades": 0,
  "recovered_fills": 0,
  "direct_fills": 0,
  "data_clean": true,
  "data_clean_reason": "all acceptance criteria passed",
  "exit_state_machine_verified": true,
  "risk_counter_verified": true,
  "signal_bridge_available": true,
  "infra_notes": [],
  "trades_detail": []
}
```

---

## 9. Post-Session Report Template

Filename: `logs/post_session_report_<YYYY-MM-DD>.md`

```markdown
# Post-Session Report — <DATE>

**Phase:** 3  
**Session:** Paper evaluation | PAPER_EVALUATION_MODE=true | LIVE_TRADING_ENABLED=false  
**Duration:** HH:MM – HH:MM ET  
**Equity start:** $XX,XXX.XX  
**Cycles:** N (30-second poll)  
**Reconciler interval:** 10 min  

---

## Infrastructure Verification

| Check | Result |
|-------|--------|
| Session terminated ≤12:35 ET | PASS/FAIL (actual: HH:MM) |
| Broker positions at EOD | 0 (or list) |
| Broker open orders at EOD | 0 (or list) |
| Duplicate exit orders | None / (order_ids) |
| 403 errors on exit orders | None / (order_ids) |
| daily_pnl vs sum of fills | MATCH / MISMATCH ($X diff) |
| Every fill in ledger | PASS/FAIL |
| RiskManager counters restored (if restarted) | N/A / PASS / FAIL |

---

## Trade Journal

| # | Symbol | Contract | Strategy | Entry | Exit | Actual Broker Fill | P&L (actual) | Exit Reason | Hold | Quality | Score | Age (min) | DTE | Entry Spr% | Exit Spr% | Universe | IV% |
|---|--------|----------|----------|-------|------|--------------------|--------------|-------------|------|---------|-------|-----------|-----|-----------|-----------|----------|-----|

**Session P&L (actual broker fills):** $XX.XX  
**Session P&L (journal):** $XX.XX  
**Discrepancy:** $X.XX (explain if > $5)  

---

## Signal Bridge Summary

| Decision | Count | Top Block Reason |
|----------|-------|-----------------|
| traded | N | — |
| blocked | N | reason |
| skipped | N | reason |
| rejected | N | reason |

---

## Phase 3 Verification

| Check | Result |
|-------|--------|
| exit_order_id set for every closed trade | PASS/FAIL |
| Broker shows exactly one exit order per trade | PASS/FAIL |
| No EXIT_PENDING warnings at session end | PASS/FAIL |
| RiskManager entry count matches trade_journal | PASS/FAIL |

---

## Infrastructure Anomalies

List any unexpected behavior.

---

## Issues for Next Session

Priority-ordered list.
```

---

## 10. Open Research Questions for Phase 3

These questions carry over from Phase 2 with zero Phase 3 observations. They are listed as questions to continue monitoring, not as conclusions.

| Question | Phase 2 observation (engineering evidence only) | Phase 3 obs needed |
|----------|-------------------------------------------------|--------------------|
| Q1: ORB vs VWAP P&L | No ORB completions in valid sessions | 3 ORB completions |
| Q2: Signal age 120+ min | 0W/2, avg −$224.75 — pre_phase3 data | 3 per age bucket |
| Q3: DTE 0-1 vs 2-3 | All 0-1 DTE losses in pre_phase3 data | 3 per DTE bucket |
| Q4: ETF vs single stock | Only 1 ETF trade in pre_phase3 data | 3 per asset class |
| Q5: Quality scores 4 vs 2-3 | 1 quality=4 win in pre_phase3 data | 3 per quality level |
| Q6: Scanner score predictiveness | No positive correlation in pre_phase3 data | 3 per score bucket |
| Q7: Direct vs recovered fills | Recovered fills: 15/27 in pre_phase3 | 3 per fill path |

**Phase 2 observations count as directional signals, not conclusions. Phase 3 data must independently confirm or contradict before any finding is treated as actionable.**

---

## 11. Pre-Phase-3 Data Guidance

The 23 pre-phase3 sessions (2026-05-11 through 2026-07-11) remain in `evaluation/ledger.json` for engineering debugging. They may be used for:

- Infrastructure failure mode analysis (fill recovery rates, order status transitions, rejection patterns)
- Scanner and signal bridge behavior (not P&L analysis)
- Process timing analysis (session duration, EOD exit timing)

They must **not** be used for:

- Win rate, expectancy, profit factor, or P&L-based conclusions
- Strategy threshold tuning
- Comparisons between strategies or universe groups
- Any claim about the system's trading performance

---

*This protocol is frozen until the 5-session minimum is reached. Changes to the protocol itself require a separate commit with rationale.*
