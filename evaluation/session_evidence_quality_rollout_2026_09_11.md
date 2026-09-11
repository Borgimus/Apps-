# Session evidence quality repairs, September 11, 2026

This repair follows the September 10 session review. The deployed observation
baseline is `0e7307ade6074033348180bc8d8821db4fe7d08d`, including the recorded
September 10 artifacts. The repair branch is
`agent/session-evidence-quality-fixes`.

## Observation controls

Keep strategy permissions, sizing, entry filters, and exit thresholds fixed
through the existing checkpoint: five completed scheduled sessions and ten
eligible opportunities. Reaching the checkpoint requests a review; it never
activates a strategy. ORB and VWAP remain shadow-only under the deployed
configuration, RSI remains diagnostic, and broker entry strategies remain empty.

No configuration, broker adapter, frozen fingerprint, or historical session
artifact is changed. Execution remains Alpaca paper, with Tradier options data.
The replay policy and model version remain unchanged. New shadow records carry
`evidence_revision: 2` to identify the improved terminal quote handling.

## Repairs

1. **Source-data readiness.** The advisory `data_feed_fresh` check reads an actual
   SPY equity quote and completed regular-session SPY five-minute bars from the
   configured broker. A recent session log, missing history, or failed request
   can no longer produce a fresh result. Quotes require valid bid/ask and source
   age from zero through 60 seconds. Completed bar close age must be no more than
   600 seconds. Still-forming bars do not count. Requests have a five-second total
   deadline. Before the first regular-session bar completes, normally 09:35 ET,
   the check reports `warming_up` and separately reports quote evidence. Missing,
   invalid, stale, or future-dated evidence remains unverified. This is a SPY
   equity advisory, not a claim about the entire scanner universe or Tradier
   options. Existing calendar, scanner, and option-entry gates retain control of
   entry eligibility.

2. **Executable partial-exit reporting.** When every recorded candidate is
   rejected by the partial-exit replay because its allowed size is below two
   contracts, the scenario reports `not_executable` and null portfolio P&L.
   Bookkeeping realized P&L can still be zero, but it is not displayed as an
   alternative-exit result. Scenarios with executable candidates retain their
   normal portfolio results. Missing evidence is still unavailable/incomplete.

3. **Final shadow quote refresh.** Shutdown requests at most one final quote per
   distinct filled shadow contract, shared across its channels and exit variants.
   Each request has a five-second deadline; the whole refresh is bounded at
   30 seconds. Contracts not successfully refreshed are marked failed. Quotes
   must pass the existing price, timestamp, and explicit-feed validation. A
   failed refresh produces an unpriced close even when an older cached mark
   would pass the 60-second age limit. Events record refresh status, source quote
   age, and terminal quote purpose. Replay requires terminal evidence for these
   sessions and leaves unresolved positions incomplete with null portfolio P&L.
   Terminal quotes cannot create new pending-entry fills, including when a quote
   is shared with an already filled diagnostic position. Shadow completion now
   precedes the final DB session summary so the reported end time includes it.

4. **Deduplicated rejection breakdown.** Daily Markdown and JSON summaries count
   original-direction observations once per session and existing 60-minute
   shadow episode. They report initial pass/rejection/unknown status, overlapping
   blockers, disjoint blocker combinations, and later eligible anchors. Repeated
   polls and inverted/exit variants cannot inflate this denominator. Historical
   missing eligibility is explicitly unrecorded. A filter pass alone does not
   prove valid quote evidence, a fill, or portfolio admission.

## Validation

The local required suite passed: **789 passed, 2 skipped**. This is the same
required suite as CI; the four existing quarantined files remain excluded from
that gate. All 91 app/scripts Python modules compiled successfully.

Regression coverage includes opening warm-up, stale/future/missing timestamps,
invalid quotes and bars, request cancellation, both final-refresh deadlines,
shared terminal quotes, pending entries, null partial-exit results, mixed
contract sizes, rejection overlap, and later eligibility. Final-refresh failures
exclude affected eligible outcomes from completed observation progress.

An offline check of the unchanged September 10 raw events produced:

| Measure | Result |
| --- | --- |
| Eligible opportunities | 1 |
| Research baseline replay | Complete, -$36 |
| Current-permissions replay | $0, no broker strategy permission |
| Partial-exit replay | Not executable, null portfolio P&L |
| Original observations / unique shadow setups | 87 / 9 |
| Initially passed / rejected setups | 1 / 8 |
| Initial quality / premium-budget / regime / disabled-symbol blockers | 6 / 6 / 4 / 1 |
| Setups with multiple initial blockers | 7 |
| Became eligible after initial rejection | 0 |
| IWM source quote age at exit | 32.520 seconds, final refresh not requested |
| Completed scheduled sessions / eligible opportunities | 1 of 5 / 1 of 10 |

The blocker counts overlap. Older recorded streams retain their recorded quote
evidence and historical terminal behavior; no terminal quote is invented or
downloaded retroactively. Existing reports and raw artifacts are preserved.

## Deployment

Deploy only while the runner is inactive. Acquire `/root/.session.lock` before
running preflight: the deployed preflight script activates the kill switch before
it checks for a runner. Merge the reviewed repair commit into
`agent/paper-scaled-sizing-250-10`, push that branch, then run
`bash /root/preflight_session.sh --check-only`.

Successful check-only preflight verifies static/broker eligibility and leaves the
kill switch active and session unarmed. The next scheduled preflight and launcher
use the existing automation. Check-only success does not itself start a session,
and the new source-data advisory executes in the runner's startup checklist.

If deployment fails, retain the kill switch and inspect the failure. Roll back by
reverting the repair merge through Git while the runner is inactive, followed by
the same check-only preflight. Never erase runtime records to make a gate pass.

These changes improve evidence quality. They do not establish strategy
profitability or broker fill equivalence; shadow fills still omit queue priority,
fees, and slippage.
