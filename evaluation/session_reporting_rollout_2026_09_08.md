# Quote timing and session reporting repair

Effective: the first session started with this repair after normal preflight passes.

The September 8 session used Tradier options data and Alpaca paper execution. Its
recorded IWM quote was dated 11:00:16 ET, while the caller supplied the earlier
11:00:15.360783 polling-cycle timestamp. That made valid data appear to be from
the future and deferred its simulated fill until the next cycle.

## Changes

- Shadow model 3 samples the clock when recording a signal and after every quote
  request. It checks freshness and pending-entry deadlines against receipt time.
  Provider timestamps remain the source of quote age. Actual stale/future quotes
  remain invalid. Requests completing after the entry deadline cannot validate a fill.
- Runtime calls use the observation clock. An explicit `now` argument remains a
  frozen clock for deterministic tests and replay.
- Model 2 state is preserved in a `.legacy-v2` archive. Earlier model 1 archives
  are retained. No older open shadow position is resumed under model 3.
- Distinct entry-signal counts use strategy, symbol, direction and the original
  signal timestamp reconstructed from observation time minus signal age. Raw
  polling observations and diagnostic-only rows remain available separately.
  ORB quality and forward-return summaries use the first observation per signal.
- A `session_context` journal event captures provider, adapter hash, declared
  cohort, shadow model, start delay and strategy permissions at startup. Reports
  use this event even if the environment later changes. Unrecorded history stays
  explicitly unrecorded; conflicting restart contexts are marked mixed.
- A start more than 60 seconds after the configured market open is labelled
  `late_start`. The exact delay is recorded even within this scheduling allowance.
- Provider/cohort ledger files and each ledger entry carry the captured context.
  Original ledgers and September 8 raw artifacts are preserved. The supplementary
  review describes September 8's first Tradier session and late start.

Broker entries remain subject to the existing strategy permissions and risk gates.
The current configuration allows ORB/VWAP shadow observations and RSI diagnostics.
It has no strategy permitted to submit broker entries. This repair changes no
broker endpoint, strategy permission, entry threshold or position-sizing setting.

## Validation

- Required test suite: 724 passed, 2 skipped using the existing four CI quarantine exclusions.
- Regression coverage includes the September 8 IWM quote, sequential quote request
  timing, late request expiry, actual stale/future rejection, exit timestamps,
  model-2 state preservation, repeated RSI/ORB/VWAP rows, persisted cohort identity,
  mixed restart contexts, scheduled starts and provider-specific ledger round trips.
- The new counter reconstructs September 8's raw records as 1,365 observations,
  1,330 diagnostic rows and four distinct entry signals.

## Deployment

Use the existing `agent/paper-scaled-sizing-250-10` VPS checkout after the runner
has stopped. Fetch and merge the tested repair commit, push the operational
branch, then run `/root/preflight_session.sh --check-only`.

Preflight's local/remote commit equality check requires the merge to be pushed
before the check. `--check-only` leaves the kill switch active and the session
unarmed. The regular weekday cron can run normal preflight and start afterward.
Do not run preflight during an active session because it activates the kill switch.

No `.env` or frozen fingerprint hash change is required. The existing declared
Tradier provider and adapter hash still apply. Data from model 3 is analyzed
separately from the September 8 model 2 shadow results.

To inspect the preserved September 8 simulator results:

```bash
./.venv/bin/python scripts/shadow_report.py --date 2026-09-08 --model-version 2
```

New reports default to model 3. Future reports should show the captured Tradier
cohort, model 3, distinct entry signals, raw diagnostic counts and the start label.
No simulated P&L qualifies as a broker-fill or portfolio-replay sample.
