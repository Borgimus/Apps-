# Day trading app: operational audit and recovery

Prepared October 24, 2026 for Bronson Phillips. Times are Eastern.

## Current finding

The operational branch continued to run unrepaired software while four repair PRs stayed in draft. The September 22-October 23 interval contains **24 scheduled sessions and 9,618 options-chain authorization warnings** from the deactivated Tradier provider. Each warning and its following HTTP reference can both contain `401`; counting every occurrence doubles the error count. October 23 alone contains 390 actual chain-authorization warnings.

The October 23 report shows zero broker submissions, zero fills, zero eligible options opportunities, and zero reported API/data-feed errors. It lists no broker-entry strategies. Its shadow report claims 30 completed scheduled observations. With the outage exclusions applied, the same raw history produces **6 accepted scheduled sessions and 6 eligible opportunities**, including the previously reviewed September 11 equity-data interruption. The supplementary subset excluding that reviewed interruption contains 5 sessions and 5 opportunities. The 10-opportunity checkpoint has not been reached.

The successful October 23 artifact publication establishes that the host could publish at that time. It does not establish that its credential still works today. The connected GitHub app has current repository read/write permission and is independent of the manually created fine-grained PATs.

## Scope and evidence

- Repository: `Borgimus/Apps-`, operational head `f2fe2e819cc62d85457d13868520fd9319d85a37`, October 23.
- Compared current operational code and pending PRs #28, #29, #30 and #31. Reviewed configuration layering, option-chain acquisition, scanner confirmation, provider health, session provenance/progress, risk/strategy permissions, preflight, launching, shutdown, evidence publication, dashboard controls and test policy.
- Examined daily raw session/preflight/automation logs and reports through October 23. Recomputed progress from `evaluation/shadow_book.jsonl` with the repaired reporting code. Raw historical session artifacts are preserved.
- Known host: Debian, `/root/trader`, root shell previously accessed through Termius. Previous monitoring address: `https://trader.tail0e7f84.ts.net`. No current SSH route, host credential or broker credential is available in this session.
- This is a source-and-artifact audit with local validation. Host processes, current account state, OPRA subscription, notification receipt, Tailscale authentication and actual deployment remain unverified until the console recovery runs.

## Findings and resulting repairs

| Priority | Failure | Repair in this release |
| --- | --- | --- |
| P0 | Primary options authorization failure was caught by confirmation and omitted from health/progress | Broker-boundary request metering, degraded session verdict, explicit historical outage exclusion, watchdog and bounded authorization retries |
| P0 | A successful expiration lookup could reset the quote-feed breaker because metadata and quotes use different services | Metadata successes no longer reset the quote breaker or consume its recovery probe; interleaved metadata/401 regression test |
| P0 | Switching to Alpaca still left volume unavailable: native code expected `dailyBar` in option snapshots | Read current Eastern-session daily option bars, validate session and volume, paginate and batch at 100 symbols, propagate HTTP/schema failures |
| P1 | Native snapshot failures could be skipped and chain timestamps used naive UTC | Snapshot failures propagate; only fresh valid quotes survive; timestamps are aware UTC; snapshot pagination is bounded |
| P1 | Preflight verified the paper account but did not exercise the options feed | Explicit OPRA request even outside market hours; empty market-hours canary fails; separate deferred freshness status outside market hours |
| P1 | Empty confirmation could silently fall back to SPY and describe recovery | Empty confirmation stays in STANDBY; provider failure remains visible |
| P1 | Daily artifact commits moved the deployed branch and caused synchronization failures | New fixed code branch/check-out, external evidence directory and isolated `session-artifacts` publisher |
| P1 | Expired GitHub tokens could block public source retrieval | Source fetch clears credential helpers/HTTP extra headers and uses the public HTTPS origin; publication credentials are independent |
| P1 | Old rollout required repeated manual synchronization and refused the new provider baseline | New pinned, one-console-command installer with backups, SQLite integrity verification, isolated tests, broker-flat check, canary, alert test, check-only preflight and cron gates |
| P1 | Alpaca adapter changes were not represented in report cohort identity | Adapter hash and explicit recovery baseline/new cohort; original Tradier baseline retained |
| P1 | Unpriced exposure could retain unsafe exit behavior | Pending repair retains last valid mark, honors time exits and forces quote-unavailable liquidation after the defined timeout |
| P1 | Required test policy excluded entire failing files | Integrated fixture repairs and full required suite with no quarantine |
| P2 | EOD UI summed independent exit scenarios | Shows constrained baseline portfolio; unavailable values remain unavailable |
| P2 | Unknown earnings/stale-order state could pass entry checks | Pending fail-closed repairs integrated |
| P2 | Dashboard mutation endpoints lacked authentication | Pending control-auth repair integrated; recovery dashboard binds to loopback |
| P2 | Large runner mixed exit handling with orchestration | PR #31 exit-manager extraction integrated without strategy-rule changes |

The release integrates the work from #28, #29 and #31 on top of the October 23 head. **PR #30 is intentionally excluded**: it changes signal-quality and planned-loss semantics and belongs in a separately declared strategy experiment. No sizing increase, signal-threshold change or strategy reactivation is included.

Official provider contracts checked October 24:

- [Options snapshots](https://docs.alpaca.markets/us/reference/optionsnapshots): latest quote, trade and Greeks; snapshot fields do not supply daily bar volume.
- [Historical option bars](https://docs.alpaca.markets/us/reference/optionbars): actual bar aggregates, 100-symbol request limit and pagination.
- [Option-chain endpoint](https://docs.alpaca.markets/us/reference/optionchain): explicit `opra` feed requires the relevant subscription; `indicative` quotes are modified and do not meet the current fill-evidence policy.

## Exact recovery behavior

The published release contains `scripts/ops/recover.sh` and `recover.py`. Invoke the shell script with the exact release SHA provided in the handoff. Its source download is pinned to that SHA.

1. Check root access, existing repository, Eastern server timezone, maintenance window and absence of an active session. Preserve unrelated cron jobs.
2. Back up environment, wrapper scripts, cron, raw logs, reports and a transactionally copied SQLite database. Verify database integrity. Keep the original checkout in place.
3. Pause the old trading schedule and leave its kill switch active.
4. Install `/root/trader-release` on `agent/operational-recovery-2026-10-24`. Reuse the existing Python environment and existing host credentials. Run tests without host broker credentials or configuration.
5. Keep the existing database at its original absolute path. Migrate evidence into `/root/trader-evidence`; conflicting pre-existing evidence stops migration.
6. Set Alpaca paper execution, Alpaca OPRA options data, Tradier observation/gates off, scaled guardrails on, and ORB/VWAP shadow-only. RSI stays diagnostic-only. Preserve existing research thresholds and sizing limits.
7. Require paper-account identity and a raw full-account flat check. Any working order, position, authorization error or malformed response blocks installation completion. No cleanup orders are placed by the installer.
8. Require real OPRA authorization using the host's key. The installer purchases nothing. Missing subscription or invalid keys stop the installation with cron paused. During market hours, a fresh usable chain is required. Off-hours, freshness remains deferred to the next session.
9. Reuse the existing ntfy topic from environment or legacy wrappers and test server acceptance. If absent, stop with a precise configuration instruction. Device receipt still needs operator confirmation.
10. Install managed wrappers and check-only preflight. It must leave the kill switch active and arming marker absent. Replace only trader-owned dashboard processes, bind the dashboard to loopback, and report the local API and Tailscale service states.
11. Install preflight at 09:20, start at 09:30, close verification at 12:35, minute watchdog and publication retries at 12:40/12:50 on weekdays. The broker calendar handles holidays. The next successful preflight controls arming.
12. Attempt publication independently. A missing write credential preserves local evidence and raises an upload incident. It does not discard observations or make source retrieval depend on a PAT.

A successful installer writes `/root/trader-recovery-status.json`. Its success means host checks passed and the schedule was installed. It does not prove the next market session has collected fresh quotes or any strategy has positive expectancy.

## Host-only blockers and remaining work

- Termius is a client. This session cannot reconnect the phone's Termius app. The same command works in the VPS provider's root web console.
- The host must have valid Alpaca paper keys and actual OPRA entitlement. Those cannot be reconstructed or purchased from the repo. The canary distinguishes HTTP authorization failures without exposing credentials.
- If GitHub write credentials are expired, configure durable repository write access for artifact publication. Public code fetch already works without a PAT. No expired token from prior chat was reused or committed.
- Tailscale service status is checked on the host; service activity alone does not prove tailnet login or client connectivity.
- Existing negative strategy evidence remains unchanged. The new provider cohort starts at zero; broker-entry strategies remain suspended.
- Historical fill/database reconciliation and a first market-hours canary still require host/broker evidence. No profitability or live-readiness claim is made.

## Validation

The pre-existing repairs integrated on current history passed **1,046 tests, 2 skipped** locally. Additional regression tests exercise native snapshot shapes without fabricated `dailyBar`, actual option volume, malformed/forbidden/old-session bar responses, pagination, metadata/quote interleaving, empty market-hours canary, cron preservation, database backup and paused-on-failure deployment behavior. Final local suite: **1,061 passed, 2 skipped in 22.04 seconds**. Shell syntax, Python compilation, whitespace checks and preservation of all historical session artifacts also passed.

The local runtime is Python 3.12.14 with the pinned dependencies from the previous audit environment. GitHub CI targets Python 3.11. Broker methods are mocked in tests; no order, subscription, server deployment or external alert was sent during local validation.
