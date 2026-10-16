# First-eligible shadow evaluation and portfolio replay

Declared: 2026-09-09. Effective: the first session using the merged change after
the existing preflight and broker checks pass. Shadow model: **4**.

## Reason for the change

On September 9, IWM's ORB diagnostic episode opened at 10:35:24 ET at a $1.79
limit while the market regime filter failed. The first subsequently eligible
observation was at 10:39:44 ET at $1.82, with quality 4 and one affordable
contract. The earlier diagnostic entry had already consumed the episode's
simulation slot. Its outcome cannot establish the return of the later entry.

## Five changes

1. Each episode has an independent first-eligible entry anchor. A later passing
   observation can create that anchor even if the diagnostic trade has already
   opened or closed. It uses the current selected contract, price, quantity and
   receipt time. Signal age is checked again after contract selection, and fresh,
   positive, uncrossed OPRA quote evidence is required. One eligible anchor is
   recorded per episode; a portfolio rejection does not retry the episode later.
2. Baseline, breakeven and partial-exit simulations open together. They share
   their contract, entry time, limit, quantity, fill evidence and pair identifier.
   Every update retrieves one quote per open contract for all its simulations.
   Partial exits require two or more contracts and sell whole contracts. Matched
   comparisons include only complete pairs with identical entry and fill fields.
3. Daily JSON/Markdown reports and `scripts/shadow_report.py` show first-eligible
   results, matched comparisons and portfolio results before diagnostic setups.
   Rejected and oversized setups remain separately labelled. Independent-trade
   sums and alternative exit policies are not portfolio P&L.
4. Session context freezes the replay policy and its hash, starting equity and
   broker strategy permissions. Candidate and timestamped quote events allow
   deterministic offline replay in observation order. Pending orders reserve
   capacity until they fill, expire or are cancelled. Replay applies the $250
   premium budget, contract cap, equity-based quantity reduction, one-position
   cap, three-entry limit, daily symbol limits, one submission per symbol per
   day, cooldown, account and experiment loss gates, correlated-stop locks,
   ORB reservation and entry time windows. These values come from the captured
   settings rather than a future environment or a hard-coded replacement policy.
5. Reports track completed scheduled observation sessions under the same data
   provider, adapter, model, cohort, replay policy and strategy permissions.
   The review checkpoint is five completed sessions and ten first-eligible
   opportunities. Restarts, late starts, early ends, health errors and unpriced
   filled eligible exits require review and are excluded from that progress count.
   Reaching the checkpoint never activates a strategy.

## Interpretation and evidence limits

The current-permissions replay retains strategy suspensions. With all entry
strategies suspended, it admits zero trades. Each research exit-policy portfolio
explicitly assumes strategy reactivation and an empty starting portfolio while
applying the captured risk rules. Research portfolios can admit different
opportunities when their earlier exits differ. Paired independent trades are the
controlled exit comparison; portfolio differences include those capacity effects.

Fills are modeled at a reachable limit using observed quotes. Queue priority,
commissions, exit slippage and price movements between samples are unmodelled.
This is a replay of sampled observations, not historical broker-fill verification.
Unknown final prices retain unresolved positions and produce no complete portfolio
P&L. A replay never uses a later diagnostic close to invent an earlier fill or exit.

Historical model-3 events do not contain the necessary first-eligible quote
stream. September 9's $79 IWM diagnostic outcome is not backfilled into a model-4
trade. Legacy events and reports remain intact. Model-3 state is archived when
model 4 first loads; it is not resumed under the changed rules. Progress starts
with model-4 sessions, and the checkpoint is an engineering review threshold,
not a statistical proof of profitability or live readiness.

Broker routing, orders, strategy permissions, contract filters, risk settings,
cron times and frozen fingerprint files are unchanged. The existing weekday
09:20 ET preflight, 09:30 start and 12:35 close continue after deployment. No
additional trading session or external scheduler is created by this change.

## Deployment on the VPS

The deployment branch is `agent/eligible-shadow-portfolio-replay`, based on
operational commit `872f1c69d7bc33d2fb2a6778b14950c6132a1738`. Use the exact tested
head commit shown in the PR. Finish the running session before deployment.

1. Fetch the branch and confirm its head against the PR.
2. Acquire `/root/.session.lock` and confirm no `scripts/session_runner.py`
   process is active. Check that the working branch is
   `agent/paper-scaled-sizing-250-10` and the source tree is clean. Preserve
   any uncommitted session artifacts before proceeding.
3. Activate `KILL_SWITCH`, remove `/root/.session_armed`, and merge the tested
   commit with `--no-ff --no-edit`. Push the operational branch so local and
   remote SHAs agree for preflight.
4. Run the required CI test selection in a temporary `git archive` checkout
   without the production environment. It excludes the four existing quarantine
   files listed in `.github/workflows/ci.yml`.
5. Run `bash /root/preflight_session.sh --check-only` while the runner is stopped.
   Require exit code 0, clean source fingerprints, Alpaca paper verification and
   the existing broker position/order checks. Check-only leaves the kill switch
   active and the session unarmed. The next scheduled preflight performs arming.

At the end of the next observation session, the daily report includes the new
summary. The raw shadow file contains `shadow_session_start`, `eligible_entry`,
`shadow_quote` and `shadow_session_end` events with model 4 and a captured policy
hash. Inspection is read-only:

```bash
cd /root/trader
./.venv/bin/python scripts/shadow_report.py --date YYYY-MM-DD --model-version 4
```

## Validation

Regression tests cover the IWM first-eligible transition, exact matched entries,
one quote per contract, whole-contract partial exits, pending-entry reservations,
timeouts, daily entry and symbol limits, cooldowns, correlated stops, loss gates,
equity-based sizing, suspended permissions, incomplete quote evidence, report
persistence and observation progress. The required CI gate also exercises the
existing broker lifecycle, position management and session safety tests.
