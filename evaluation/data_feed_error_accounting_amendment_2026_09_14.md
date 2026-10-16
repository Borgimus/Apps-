# Amendment: scanner empty-window guard and data-feed error accounting

Declared: 2026-09-14, after the 09-14 observation session. Effective: the first
session run from the merged change. Cohort `guardrails_v3_tradier_options_2026_09_08`
continues unchanged — none of the observation identity fields (options data
provider and adapter hash, evaluation cohort, shadow model, replay policy hash,
broker entry permissions) are touched, and neither are the four frozen-hash
files, `config.yaml`, the requirements lock, or the ticker universe.

## Defect 1 — scanner crash on an empty intraday window

At 09:30:14 on 2026-09-14, 21 symbols failed in the universe scan with
`'RangeIndex' object has no attribute 'date'`. The intraday fetch requests bars
from `today - 2 days`; on a Monday that window is Saturday, Sunday and the
pre-open minutes, so symbols without early IEX prints return zero bars. An
empty bar list becomes a frame with a RangeIndex, and
`intra_df.index.date == today` raised. The affected symbols received sentinel
metrics for the opening scan cycle only and recovered on the next cycle.
The cohort's earlier sessions (Tue–Fri) never had an empty window, which is
why this first appeared on its first Monday.

Fix: `YFinanceScanner._bars_for_day` treats an empty or non-datetime-indexed
frame as "no bars today", which flows into the existing
`no_intraday_bars_today` fallback. No metric changes when bars exist.

Evidence impact on 2026-09-14: none on eligible anchors. Both anchors (NVDA,
IWM) were selected well after the opening cycle. ORB signals depend on opening
range bars; for the affected symbols the first cycle's ORB inputs were
sentinels, which is the pre-existing failure mode this fix removes.

## Defect 2 — data-feed failures were invisible to the health gate

On 2026-09-11 between 12:16 and 12:28 ET, Alpaca's market-data API returned
504 Gateway Timeout on 18 fetches (SPY regime bars and per-symbol bars),
exhausting all three retries each time. The session's health report showed
`api_errors: 0` because that counter records broker-order failures and
startup-recovery errors only, and the daily report showed `API errors: 0`
because the failures were Python-log lines, not DB session-log rows. The
observation checkpoint therefore counted 09-11 as completed session 2/5
without a review, contrary to the rollout rule that health errors require
review and are excluded.

Fix:

- The session runner counts market-data fetches that fail after retries
  (per-symbol bars, SPY regime bars, universe-scan batch fetches) as
  `data_feed_errors`, separate from `api_errors`.
- The count is written to the health report (`data_feed_errors`), to the
  `shadow_session_end` event, and to a `data_feed_errors` session-log row
  with the failing labels, which the daily report renders under System Health.
- `observation_progress` excludes a session whose end event carries a nonzero
  `data_feed_errors` with reason `data_feed_errors_review_required`. Sessions
  recorded before this field existed carry no key and are not retroactively
  excluded.

## Review record for 2026-09-11 (counted; reviewed; no evidence impact)

The failures occurred after the 12:00 entry cutoff, during the final 15
minutes when only shadow-position marking and the SPY regime refresh were
running. The session's single eligible anchor (MARA 9/18 12C) closed on
`max_hold` with a Tradier exit quote 2.23 seconds old and `outcome_priced`
true. The regime refresh fell back to `neutral` for the affected cycles, which
cannot admit an entry after the cutoff. Conclusion: the 504s did not affect
any eligible outcome, and 09-11 remains counted as a completed scheduled
session. Under the rule introduced here, an equivalent session in future
would be excluded pending an explicit review like this one.

## Rollout

Merge into the `agent/paper-scaled-sizing-250-10` accumulation branch after
CI. Deploy to the VPS between sessions using the existing runbook; the
preflight source check only requires a clean tree at the fetched commit.
