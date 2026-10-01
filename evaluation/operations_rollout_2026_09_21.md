# Session operations rollout

Status: release authorized for rollout. VPS installation must be confirmed by
`DEPLOYED_PREFLIGHT_READY`; publishing this release alone does not install it.
Updated to include deployed-branch snapshot `306212783a4761b69c0548994dfc133cc12a2b8b`
and its September 21 session evidence. The initial release preceded that daily
artifact commit, so the installer correctly stopped at its ancestry check.
That early stop did not pause cron or install code. Failure messages now
distinguish pre-maintenance stops from failures after cron was paused.
This document does not authorize live trading or change strategy permissions.

Validation after incorporating September 21 evidence: **853 passed, 2 skipped**
on Python 3.12 using the installed locked dependencies. The four existing
quarantined files were excluded exactly as in CI; quarantine is not claimed
fixed. Python compilation, shell syntax and diff whitespace checks passed.
Configuration, broker adapters and the cohort baseline were verified unchanged.
Tests used local bare Git remotes and injected notification senders. Actual VPS
cron execution, external notification delivery and broker connectivity were
not exercised by this validation. CI still runs its configured Python 3.11.
Close-script tests cover natural exit, graceful SIGTERM, a wedged runner with
both clean and unverified broker snapshots, final-check failure, report failure,
publication failure, and blocking a new start/preflight during close-out.
Regressions confirm migration retains the newest backed-up events after Git
restores older tracked runtime files, and a stale release stops before cron or
code changes. Installer tests verify cron stays paused on migration, artifact
publication, code push and preflight failures. No server deployment is claimed
from these local tests.

## Changes

- The code checkout no longer publishes evidence commits. An isolated checkout
  publishes immutable, checksummed daily snapshots on `session-artifacts`.
  Code HEAD, index and remote code branch are untouched by publication.
- Reports, ledgers, shadow events and market-data comparisons move to
  `/root/trader-evidence`. Historical evidence is copied before switching paths.
  The publisher retains a local archive before attempting any network operation.
- The daily upload is an allowlist of reports, ledgers, dated session logs,
  dated shadow events and quote comparisons. It excludes rotated trading logs,
  credentials, the database, and arbitrary runtime files.
- Eleven rotated `logs/trading.jsonl*` files leave Git tracking. `.gitignore`
  already excludes logs. Existing Git history is not rewritten.
- A separate watchdog checks the runner's dated heartbeat, completed cycle and
  timestamp. On scheduled market days it alerts at/after 09:35 ET if no fresh
  heartbeat exists, and monitors until 12:30. Three minutes is the staleness limit.
- Preflight, launcher and publication failures send bounded ntfy notifications.
  Unsent notifications remain on disk for watchdog retries; delivered alerts
  are deduplicated by code and ET date. Repeated same-day incidents of the same
  type remain visible in logs but do not generate repeated pages.
- Preflight obtains the session lock before touching the kill switch or arming
  marker. It retains strict code equality and all existing safety checks.

No automatic pull, merge, rebase, force-push, or strategy activation is added.
If a new code commit appears remotely, a deliberate deployment is still required.
The independent alert makes that failure visible. A publisher conflict affects
the artifact branch only and retains the local snapshot for manual recovery.

## Guarded installer

`scripts/ops/deploy.py` performs the steps below as one maintenance operation.
Run it from a temporary file extracted from the fetched release, using the exact
release SHA. It must run on the VPS as root, outside 09:00-13:00 ET on weekdays.
The deployment source branch is `agent/session-operations-reliability`.

The installer requires a stopped runner, a verified flat paper account, no
staged changes, no source edits and equality between the local and remote code
branch. The release must contain the current deployed commit. Divergence stops
deployment for review instead of rebasing or overwriting source automatically.

It verifies backups of logs and evaluation data before restoring any dirty
tracked runtime files. Migration reads the backup so uncommitted session
evidence is retained. Source files are never discarded. It backs up root
scripts and cron, pauses only the managed session cron block, merges the release
with `--ff-only`, installs all three root wrappers and preserves the kill switch.

It then checks the operations configuration and full paper account, submits a
test notification, verifies historical artifact publication, pushes the code
branch without force, and runs check-only preflight. Only after all steps pass
does it restore the schedule with watchdog and publication jobs. The final
success marker is `DEPLOYED_PREFLIGHT_READY`. Trading remains disarmed until the
next scheduled preflight. Confirm the test notification on the receiving device.

Notification values are read from the VPS configuration or prompted without
echo in Termius. They are written only to the mode-600 operations environment
file. No notification credentials belong in a Git commit or chat message.

If any step fails after maintenance starts, the schedule stays paused and local
backups remain in the printed backup directory. Do not rerun blindly after
migration has completed; inspect the output and resume the failed step. The
installer deliberately refuses to overwrite an existing migrated evidence set.

## Close-script integration

The user supplied the VPS `auto_close_session.sh` for review. Its replacement
is `scripts/ops/close.sh`. The 120-second natural grace and subsequent SIGTERM
with 180-second grace are preserved. Closing orders and positions remains the
runner's responsibility; the shell adds no order-submission or liquidation path.

Changes to the supplied shutdown logic:

- Match both absolute and relative `scripts/session_runner.py` arguments. The
  old pattern required a slash before `scripts`, missing the relative launcher.
- Activate the entry kill switch and remove the arming marker before shutdown.
- Verify the paper identity, then query raw Alpaca position and open-order
  responses. Every position, including equities, blocks flat confirmation.
  Malformed responses and API failures also block confirmation. Adapter parsing
  cannot silently discard an unparseable working order.
- Repeat that verification before declaring EOD success. A stuck runner remains
  a failure even if a snapshot reports zero exposure. Preserve its failure
  marker and alert. **Automatic SIGKILL is removed** because an active process
  can change broker exposure after a snapshot.
- Preflight and launch check the close-out lock. After the runner exits,
  close-out obtains the session lock before final verification and reporting.
- Generate shadow analysis with the same external evidence paths as the
  launcher. Report failures are visible and return a nonzero exit code.
- Release the session lock before calling the isolated publisher, while retaining
  the EOD lock through final cleanup to block a racing new launch. Preserve
  and attempt to upload raw evidence even when the final broker check or
  analysis failed, provided the runner has stopped. If the runner remains
  alive, retain local evidence and skip publication until it can be archived
  under the session lock.

**Install all three root entrypoints together.** Keeping the old close script's
`git add -f logs/ evaluation/` and code-branch commit/push tail recreates the
original divergence problem. The replacement contains no Git mutation command.

## Back up before merging the eventual approved commit

Do this outside an active session. Verify no runner is running and the broker
is flat using the existing checks. Keep the kill switch active and marker absent.
Before deploying the Git deletions, copy the VPS logs outside the repository:
Git may remove its tracked log files when the new commit is checked out.

```bash
(
set -euo pipefail
cd /root/trader
if pgrep -af '[s]cripts/session_runner.py'; then exit 1; fi
ops_backup_dir="/root/trader-backups/operations-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ops_backup_dir"
chmod 700 "$ops_backup_dir"
cp -a logs evaluation "$ops_backup_dir/"
cp -p /root/preflight_session.sh /root/start_session.sh /root/auto_close_session.sh "$ops_backup_dir/"
crontab -l > "$ops_backup_dir/crontab.txt"
git diff --binary > "$ops_backup_dir/working-tree.patch"
git diff --cached --binary > "$ops_backup_dir/index.patch"
git bundle create "$ops_backup_dir/repository.bundle" --all
printf 'Backup: %s\n' "$ops_backup_dir"
)
```

Review and preserve any local-only commits before deploying. Never use a hard
reset or force push to hide source/artifact divergence. The code branch must
end on the reviewed commit, equal to its remote, before preflight can pass.

## Configure and migrate evidence

Create `/root/trader-ops.env` with mode 600, using simple shell-compatible
`KEY=value` assignments. Keep broker credentials in the existing `.env`.

```text
TRADER_EVIDENCE_DIR=/root/trader-evidence
TRADER_ARTIFACT_CHECKOUT=/root/trader-artifacts
OPS_NTFY_URL=https://YOUR_NTFY_HOST/YOUR_PRIVATE_TOPIC
OPS_NTFY_TOKEN=YOUR_TOPIC_ACCESS_TOKEN
```

Use an existing authenticated private ntfy topic. No topic or external service
is provisioned by this change. Confirm the receiving device subscribes to it.
Do not paste these secret values into chat or commit the file.

After code deployment, and before any new session:

```bash
cd /root/trader
./.venv/bin/python scripts/session_ops.py migrate
./.venv/bin/python scripts/session_ops.py check-config
./.venv/bin/python scripts/session_ops.py test-alert --message "Trader operations delivery test"
```

Migration copies historical ledgers, reports and event streams. If the same
destination exists with different bytes, it stops instead of overwriting data.
Review that conflict manually. Do not rerun migration over a populated newer
runtime history. Confirm actual alert receipt; command success alone does not
prove the phone displayed it. Failed deliveries are retained for retries.

The shell wrappers set `EVALUATION_OUTPUT_DIR`, `EVALUATION_LEDGER_FILE` and
`MARKET_DATA_COMPARISON_FILE`. Any manual report job or dashboard requiring
current evidence must use those same paths. Historical files on the code branch
are preserved as historical records, not continually updated session artifacts.

## Install as one reviewed maintenance operation

After committing, pushing and deploying the approved code, install all three
root entrypoints while the runner is stopped:

```bash
printf '%s\n' '#!/usr/bin/env bash' 'exec bash /root/trader/scripts/ops/preflight.sh "$@"' > /root/preflight_session.sh
printf '%s\n' '#!/usr/bin/env bash' 'exec bash /root/trader/scripts/ops/start.sh "$@"' > /root/start_session.sh
printf '%s\n' '#!/usr/bin/env bash' 'exec bash /root/trader/scripts/ops/close.sh "$@"' > /root/auto_close_session.sh
chmod 700 /root/preflight_session.sh /root/start_session.sh /root/auto_close_session.sh
```

Retain the existing three cron entries and add these to the same ET schedule:

```cron
* * * * * /bin/bash /root/trader/scripts/ops/watchdog.sh
40,50 12 * * 1-5 /bin/bash /root/trader/scripts/ops/publish.sh
```

The watchdog runs independently of the trading runner. Outside its session
window it only retries undelivered notifications. Weekends and broker-calendar
holidays do not trigger missing-session alerts. Calendar failure sends an
explicit monitoring-unverified alert instead of treating the day as a holiday.
The 12:50 publication is an idempotent retry when evidence has not changed.
An unfinished close-out holding the session lock blocks publication safely.

This local watchdog cannot detect a powered-off VPS or stopped cron daemon.
That requires an external heartbeat monitor, which is not configured here.

Run `bash /root/preflight_session.sh --check-only` while stopped. Require a zero
exit code, kill switch present and no arming marker. Test publication for a
completed date, then verify the code branch HEAD and index did not change and
the artifact commit exists only on `session-artifacts`. Do not run preflight
against an active session as a general health check.

## Repository policy

For this rollout, `agent/paper-scaled-sizing-250-10` remains the explicitly named
deployment trunk. Feature changes require review into that branch followed by
deliberate deployment; runtime evidence must never be committed there.
`session-artifacts` is evidence storage and must not be merged into code.

PR #18 and the default branch are not retargeted by this patch. Reconcile the
`src/` versus `app/` histories in a separate reviewed migration, then establish
one default branch, protected review target and deployment source. Avoid adding
this large legacy artifact history to main merely to fix branch naming.

## Recovery

Publication failures retain `/root/trader-evidence/archives/DATE/DIGEST` with a
manifest and file hashes. Retrying uses the same snapshot if evidence is
unchanged. Dirty or diverged artifact checkouts stop for review; do not discard
their local commits or snapshots. Code preflight remains independent of them.

If rolling back the scripts, preserve the external evidence and redirect all
report writers consistently before restarting. Do not resume writing to older
in-repository ledgers or event streams without reconciling the new records.
