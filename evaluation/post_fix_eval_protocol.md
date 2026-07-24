# Post-Fix Evaluation Protocol

**Created:** 2026-06-05  
**Status:** ACTIVE  
**Review after:** 5 completed clean sessions

---

## 1. Data Quality Boundary

### Infrastructure correction history

| Commit | Fix | Effect on prior data |
|--------|-----|----------------------|
| `d2935c9` | FillTracker: pass `risk` to `_handle_dead`; re-check fill on 422 | Sessions before this date may have leaked `pending_entries` on stale cancel (Bug A) and silently dropped fills on 422 race (Bug B). P&L counts in those sessions are unreliable. |
| `7796b6d` | Replay tests for Bug A/B; confirmed fix coverage | Last commit before clean-session baseline. |
| `79d8681` | Bug C (bid-side P&L), Bug D (duplicate exit 403), Bug E (EOD loop), LiquidityFilter delta=N/A cap | Sessions before this commit may have midpoint exit P&L (Bug C), corrupted `daily_pnl` from duplicate `pm.close()` (Bug D), and XLK ghost-rejection noise. |

### Clean-session boundary

**First session eligible for performance conclusions: the first session run after commit `79d8681`.**

All sessions dated 2026-06-04 and earlier are contaminated for performance conclusions:

| Session | Contamination |
|---------|--------------|
| 2026-05-11 through 2026-05-29 | Pre-Bug-A/B fixes; no completed trades in most sessions |
| 2026-06-02 | Exit P&L may use midpoint (Bug C not yet fixed); eod_exit prices unverified |
| 2026-06-03 | Bug C confirmed active: META exit recorded at midpoint $0.39, actual bid $0.33; AMZN fill lost to FillTracker stale-cancel race |
| 2026-06-04 | Bug D active: META exit loop corrupted `daily_pnl`; actual fill ($1.54) differs from journal ($3.58); session ran 18 min past EOD (Bug E) |

The six trades from 2026-06-02 through 2026-06-04 are retained as **directional observations only**, with corrections noted where known. They must not be used to set thresholds or draw performance conclusions.

---

## 2. Session Configuration (frozen)

Run every evaluation session with **exactly** these settings. Do not change them between sessions. Changes invalidate cross-session comparability.

### Environment (.env / shell overrides)

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

- Strategy thresholds (no tuning)
- Risk limits (`max_risk_per_trade`, `max_spread_pct`, delta targets)
- Universe groups
- ORB slot reserve time
- EOD exit time (12:30 ET)
- Scanner score floor or any scoring weights

---

## 3. Session Execution Checklist

Run before each session:

- [ ] Confirm `LIVE_TRADING_ENABLED=false` in environment
- [ ] Confirm `PAPER_EVALUATION_MODE=true`
- [ ] Confirm Alpaca base URL is paper endpoint (`paper-api.alpaca.markets`)
- [ ] Confirm no open positions or pending orders from prior session
- [ ] Confirm `.env` matches Section 2 exactly
- [ ] Note starting equity from broker account page

Run after each session:

- [ ] Verify session terminated at or shortly after 12:30 ET (Bug E now fixed; flag if it runs past 12:35)
- [ ] Verify broker shows zero open positions
- [ ] Verify broker shows zero open orders
- [ ] Check session log for any 403 Forbidden responses on exit orders (Bug D indicator)
- [ ] Check `RiskManager daily_pnl` against sum of individual fills — if they differ by more than $5, investigate before logging results
- [ ] Verify every filled trade appears in `evaluation/ledger.json` after `post_session.py` runs
- [ ] Record actual broker fill prices from Alpaca order history, not journal-recorded prices, for every exit

---

## 4. Per-Trade Data to Capture

For every filled trade, record the following in the post-session report. The session runner and post_session module capture most fields automatically, but these require manual verification:

### From broker order history (verify against journal)

| Field | Source | Notes |
|-------|--------|-------|
| `actual_entry_fill` | Alpaca order `filled_avg_price` | Must match `fill_price` in trade_journal |
| `actual_exit_fill` | Alpaca order `filled_avg_price` | Journal exit_price should match; flag any discrepancy |
| `exit_order_id` | Alpaca order history | Verify single fill, no duplicate orders |
| `exit_fill_time` | Alpaca order history | Compare to `exit_time` in journal |

### From signal_bridge table (automatic)

- `signal_quality_score` (1–4)
- `scanner_score`
- `signal_age_seconds` (convert to minutes)
- `confluence_count`
- `universe_group`
- `strategy_id`
- `rvol` at signal time
- `entry_spread_pct`
- `final_decision` and `exact_block_reason`

### From trade_journal table (automatic)

- `delta` at entry
- `iv` at entry
- `expiration` → compute DTE = `expiration - session_date`
- `exit_reason`
- `exit_spread_pct`
- `hold_duration_secs`
- `realized_pnl` (verify against actual fill prices)

### Signal bridge blocking stats (every session)

Count entries in `signal_bridge` for the session date by `final_decision`:

```sql
SELECT final_decision, exact_block_reason, COUNT(*) as n
FROM signal_bridge
WHERE session_date = '<DATE>'
GROUP BY final_decision, exact_block_reason
ORDER BY n DESC;
```

---

## 5. Post-Session Report Template

Filename: `logs/post_session_report_<YYYY-MM-DD>.md`

```markdown
# Post-Session Report — <DATE>

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
| 403 errors on exit orders | None / (order_ids) |
| daily_pnl vs sum of fills | MATCH / MISMATCH ($X diff) |
| Every fill in ledger | PASS/FAIL |

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

**ORB slot:** reserved until HH:MM, released HH:MM
**Reconciler:** N runs, N clean, N flagged

---

## Trades by Quality Score

| Quality | N | Wins | P&L |
|---------|---|------|-----|

## Trades by DTE

| DTE | N | Wins | P&L |
|-----|---|------|-----|

## Trades by Universe Group

| Group | N | Wins | P&L |
|-------|---|------|-----|

## Trades by Strategy

| Strategy | N | Wins | P&L |
|----------|---|------|-----|

---

## Infrastructure Anomalies

List any unexpected behavior not covered by the checklist above.

---

## Issues for Next Session

Priority-ordered list of bugs or anomalies to investigate before next run.
```

---

## 6. Ledger Integrity Requirements

After every session, `evaluation/ledger.json` must satisfy:

1. **Actual fill prices only.** `realized_pnl` for every closed trade must equal `(actual_exit_fill - actual_entry_fill) × 100 × quantity`. If the journal exit_price differs from the Alpaca `filled_avg_price`, correct the ledger entry manually before adding to cumulative stats.

2. **Every filled trade present.** Count `SELECT COUNT(*) FROM trade_journal WHERE session_date = '<DATE>' AND status = 'closed'` and verify it matches the ledger `total_trades` for that session.

3. **No contaminated data in cumulative.** The cumulative section includes all sessions from `2026-05-11` onward. The `total_pnl` field in cumulative is the running total across all sessions including contaminated ones. Performance conclusions must use a separate clean-sessions subset query, not the cumulative section.

4. **Clean-session P&L query** (run this after accumulating 5+ clean sessions):

```python
import json
with open('evaluation/ledger.json') as f:
    d = json.load(f)

# First clean session is the first after 2026-06-04
CLEAN_FROM = '2026-06-05'
clean = [s for s in d['sessions'] if s['date'] >= CLEAN_FROM]
total = sum(s['realized_pnl'] for s in clean)
trades = sum(s['total_trades'] for s in clean)
wins = sum(s['wins'] for s in clean)
print(f"Clean sessions: {len(clean)}, trades: {trades}, wins: {wins}, total_pnl: {total:.2f}")
```

---

## 7. Minimum Observations Before Performance Conclusions

No performance conclusions, no parameter changes, no threshold adjustments until:

| Dimension | Minimum observations |
|-----------|---------------------|
| Quality score | ≥3 trades at each quality level being compared |
| DTE | ≥3 trades at each DTE bucket being compared |
| Universe group | ≥3 trades per group |
| Signal age | ≥3 trades per age bucket |
| Strategy (ORB vs VWAP) | ≥3 completed trades per strategy |
| IV buckets | ≥3 trades per IV quartile |

The current state as of 2026-06-05 (6 total clean-boundary trades, all from contaminated sessions):

| Dimension | Current observation count | Minimum reached? |
|-----------|--------------------------|-----------------|
| quality=4 | 1 | No |
| quality=2-3 | 5 | No (5 at one level, not 3 per level) |
| 1-DTE | 3 | No (only losses, no wins to compare) |
| 3-DTE | 2 | No |
| core_etfs | 1 | No |
| mega_cap | 3 | Marginal (no wins) |
| ORB completed | 0 | No |
| VWAP completed | 6 | No (no ORB to compare against) |

**Estimated sessions needed to reach minimums:** 5–8 sessions assuming 2–3 fills per session.

---

## 8. Historical Analysis Trigger

After completing **5 clean sessions** (first clean session date ≥ 2026-06-05):

1. Run the multi-session analysis:
   ```bash
   python3 -c "
   import sqlite3
   # Query signal_bridge joined to trade_journal for clean sessions only
   # Clean sessions: session_date >= '2026-06-05'
   "
   ```

2. Compute the same dimensions as Section 4: win rate and P&L by quality score, DTE, universe group, signal age, IV, entry spread, strategy.

3. Apply the minimum observation thresholds from Section 7 before drawing any conclusion about a dimension.

4. Document findings in `research/clean_session_analysis_<DATE>.md` with:
   - Sample size per dimension bucket
   - Observed win rate and average P&L per bucket
   - Whether the minimum threshold was reached
   - Whether any pattern survives confound checks (e.g., is quality=4 winning because of DTE, not quality?)

5. Do not feed findings back into strategy parameters until the analysis document is reviewed and the sample sizes are sufficient.

---

## 9. Acceptance Criteria (per session)

A session is accepted into the clean dataset if and only if:

- [ ] `LIVE_TRADING_ENABLED=false` confirmed in log
- [ ] Session terminated at or before 12:35 ET
- [ ] Broker positions at EOD = 0
- [ ] Broker open orders at EOD = 0
- [ ] No 403 Forbidden responses on exit order placement
- [ ] `daily_pnl` from RiskManager matches sum of individual fills (±$5 tolerance for rounding)
- [ ] Every `status=closed` trade in trade_journal has a verified `filled_avg_price` from Alpaca order history
- [ ] Journal `exit_price` matches Alpaca `filled_avg_price` for exit order (or discrepancy is explained and ledger is corrected)
- [ ] Post-session report filed at `logs/post_session_report_<DATE>.md`
- [ ] Ledger updated with actual fill prices

A session that fails any criterion is **flagged** in the ledger. Its trades are excluded from performance analysis but retained in logs for infrastructure debugging.

---

## 10. Open Variables Under Observation

These are the variables identified from the 2026-06-02 through 2026-06-04 data as potentially directional. They are listed here as observations to continue monitoring, not as conclusions.

| Variable | Observation so far | Confounds | Minimum N to check |
|----------|-------------------|-----------|-------------------|
| Quality score (4 vs 2-3) | Only win was quality=4; all losses were quality≤3 | Quality=4 trade was also ETF, 3-DTE, low IV | 3 per level |
| DTE (0-1 vs 2-3) | All 0-1 DTE losses (4/4); 3-DTE includes only win | 3-DTE trades were earlier session; ETF vs single stock | 3 per bucket |
| Universe group (core_etfs vs mega_cap) | core_etfs 1W/1; mega_cap 0W/3 | Only 1 ETF trade; all ETF/DTE/quality confounded | 3 per group |
| Signal age 120+ min | 0W/2, avg −$224.75 | Both trades were different sessions and different stocks | 3 trades |
| IV at entry | Low IV (14.2%) = win; high IV = losses | Low IV = ETF instrument class | Separate IV from asset class first |
| Scanner score | No positive correlation observed; score=67 = largest loss | Too few trades at each bucket | 3 per bucket |
| Confluence count | No signal; range 1–7, all losses in that range too | | 3 per level |
| ORB vs VWAP | No ORB completions yet | | 3 ORB completions |

---

*This protocol is frozen until the 5-session minimum is reached. Changes to the protocol itself require a separate commit with rationale.*
