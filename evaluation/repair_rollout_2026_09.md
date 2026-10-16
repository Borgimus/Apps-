# September evaluation repairs

This amendment repairs evaluation defects found in the September 6 audit.
It does not establish a profitable strategy. The ORB/VWAP broker-entry
suspensions, QQQ block, position limit, daily entry cap and loss limits remain
in place. No VPS deployment or broker-account action is performed by this PR.

## Changes ready for review

- Broker positions and shadow positions call one shared exit policy. A 25%
  trailing stop arms after the existing 25% gain threshold. Environment
  overrides apply to both. Duration begins at fill and EOD uses Eastern time.
- Shadow model 2 requires an explicit OPRA feed, a positive uncrossed quote,
  and an exchange timestamp at most 60 seconds old to validate an entry or
  mark a position. Missing timestamps remain missing. Entry timeout matches
  the runner setting. Unfilled orders and stale final marks have no realized
  simulated P&L. Extrema and activation survive restarts.
- Each signal records affordability and entry-filter eligibility separately
  from diagnostics. An initially unpriceable episode can start later without
  becoming a second opportunity. Partial variants sell whole contracts and
  require at least two affordable contracts. Normalized and sized P&L are
  separate fields. Entry sizing uses the greater of ask and submitted limit
  so a price offset cannot exceed the premium budget.
- Ledger version 3 derives strategy trade counts from closed journal rows,
  including flat trades. Incomplete or legacy coverage makes trade count,
  win rate, expectancy and profit factor unavailable. Reported historical
  counts and P&L remain labelled for reconciliation. Health reports distinguish
  losses, flats and missing P&L.
- CI covers `agent/**` pushes and pull requests. Order lifecycle, exit
  pricing, stale-cancel recovery, accounting and standby tests are blocking.
  Four unrelated fixture suites remain in the visible, nonblocking quarantine.

Model 1 history is unchanged and must not be pooled with model 2. The default
shadow report uses model 2. To inspect the old diagnostics explicitly:

```bash
python scripts/shadow_report.py --model-version 1
```

The Alpaca adapter accepts `ALPACA_OPTIONS_FEED=opra` or `indicative`. Leaving
it unset preserves provider selection and records the feed as unverified.
The code does not purchase a subscription or silently fall back after an OPRA
authorization error. Alpaca documents the distinction in its
[snapshot API](https://docs.alpaca.markets/us/reference/optionsnapshots).

## Work still needed before performance conclusions

1. Run the repaired code through a fresh paper observation period. Old quote
   paths are insufficient to recompute corrected outcomes, so this PR makes
   no claim that the exit repair recovers any historical loss.
2. Obtain the VPS SQLite backup and broker fill export for all audited dates.
   Rebuild each historical cohort separately, verify entry/exit order IDs,
   quantities and prices against broker fills, then review any discrepancies.
3. Add a chronological portfolio replay before comparing strategy eligibility
   or capacity changes. The current diagnostic book does not enforce a separate
   simulated portfolio's position limits, daily entries, cooldown, daily loss
   limits or reconciliation state. `portfolio_validated` is always false.
   Entry-filter-qualified diagnostics cannot authorize reactivation.
4. Confirm the market-data entitlement and inspect fresh feed/timestamp/delta
   coverage. The July comparison sample does not verify current market data.

## Server rollout sequence

Use the existing Termius connection to the Debian VPS. No additional GitHub
administrator grant or shared passwords are required. Run this during a
stopped session, after the PR has passed CI and its commit has been reviewed.

1. Open Termius, tap the Debian server, and open its terminal.
2. Go to `/root/trader`. Keep `KILL_SWITCH` present and the existing
   `/root/.session_armed` marker absent. Verify there is no active
   `session_runner.py` process. Do not interrupt management of open positions.
3. Use the existing paper preflight/broker checks to confirm zero open orders
   and zero positions. Do not arm the session during this maintenance.
4. Back up the database with SQLite's backup API, plus the existing `evaluation`
   directory and `logs/shadow_book_state.json`. Keep backups outside the git
   checkout. Record the current commit for rollback.
5. Fetch the reviewed repair commit and fast-forward the operational branch
   after merging the PR. Do not use a hard reset or discard local changes.
   Activate the existing `.venv` and run the required CI test command.
6. Run `python scripts/capture_session_fingerprint.py --verify --check-broker`.
   The declared adapter hashes in `phase3_tracking.json` must match. Preserve
   the kill switch and leave the session unarmed after verification.
7. Check `ALPACA_OPTIONS_FEED` against the account's entitlement. Explicit OPRA
   is required for new shadow fill evidence. Do not treat an indicative or
   unreported feed as a verified fill source.

If verification fails, leave entries blocked and preserve the full error.
Rollback requires the previously recorded code commit and its matching
fingerprint. Version-2 shadow state must be kept separate from the old
simulator if rolling back. Do not restore a database over newer trading data.

## Read-only ledger rebuild

After creating a consistent SQLite backup, run the following from the checkout
with its existing virtual environment active. These example paths assume the
backup is `/root/trader-backups/trading.sqlite` and the output does not exist.

```bash
python scripts/rebuild_evaluation_ledger.py \
  --database /root/trader-backups/trading.sqlite \
  --ledger evaluation/ledger.json \
  --output /root/trader-backups/ledger-rebuilt.json
```

The command opens the backup read-only and refuses to overwrite an existing
output. It retains source journal rows and broker order IDs in the manifest,
marks sessions absent from a partial backup incomplete, and flags invalid fill
arithmetic or duplicate entry order IDs. Cohort and contamination annotations
are preserved. Rebuilding does not set `broker_fills_reconciled` to true and
does not replace the operational ledger. The output contains trading records,
so keep it in your private server backup location.
