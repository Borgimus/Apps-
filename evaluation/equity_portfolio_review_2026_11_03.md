# Frozen portfolio review contract

Declared November 3, 2026. Five full sessions and the October 26 partial session were already known. The analysis of those records is retrospective. Collection remains the unchanged `iex_equity_signal_screen_v1_2026_10_24` experiment.

## Collection and provenance

The ORB and VWAP parameters, strategy files, observer implementation and 38-symbol universe are fingerprinted in `equity_research_baseline_2026_11_03.json`. Research preflight refuses drift. New daily reports carry the baseline identity. The two previously deployed source commits are explicitly mapped to the same strategy parameters and universe after source-hash verification. Unknown or changed identities cannot silently join this cohort.

The offline review reads daily JSON reports and append-only raw events. It verifies source/date/protocol identity, event ordering, completed bars, first-observed prices, signal discovery and eligibility, actual bar availability, final symbol freshness, and every reported forward outcome. Input SHA-256 hashes are recorded. Missing values remain unavailable. Malformed evidence is labelled invalid and blocks a positive readiness screen.

A full valid session starts between 09:30 and the first completed-bar boundary at 09:35 Eastern, finishes at/after 12:30 with `scheduled_end`, has zero recorded request or strategy errors, and passes raw-evidence validation. SPY and at least 80% of symbols must be fresh at completion and at all 35 five-minute checkpoints from 09:40 through 12:30, using only bars received by each checkpoint. A final backfill cannot hide an earlier collection gap. Late/early/interrupted runs remain separate partial sessions. Running sessions do not advance completed counts.

## Primary portfolio proxy

The exact separately versioned contract is `equity_portfolio_policy_2026_11_03.json`. Its checksum is checked before analysis.

- Initial hypothetical equity: $50,000. Maximum position notional: $1,000. Whole shares only, with no leverage.
- Long signals only. Historical short availability is unverified, so short signals remain in all-signal diagnostics but are excluded from this primary replay.
- One reserved position slot across both strategies; at most three entries each day.
- Candidate order is entry timestamp, actual discovery timestamp, existing universe order, strategy ID and signal ID. Future winners never determine admission.
- Entry is the first five-minute bar open at/after discovery. Exit is the close of the bar ending 30 minutes after entry. Neither is an actual fill. No stop/target or intrabar ordering is invented.
- A slot stays reserved until all required exit evidence has actually arrived. A candidate discovered before that evidence is available cannot use the eventual result or free the slot early.
- A $30 daily realized-loss entry gate blocks subsequent entries after already observed losses. This is an analytical assumption and does not guarantee a maximum position or daily loss.
- Missing selected entry/holding/exit bars create an unresolved proxy. The total remains unavailable and subsequent replay exposure is blocked until that evidence can resolve the selection. Such selections are never discarded or scored as zero.
- Independent 5, 10 and 20 bps assumed round-trip cost scenarios are shown. Cost is charged against entry notional. This is sensitivity analysis, not a bid/ask, latency, market-impact, fee or borrow model. Ten bps is the primary scenario; 20 bps is the stress scenario.

The report separates independent-signal averages from the constrained portfolio proxies. It includes daily results, leave-one-day-out signal means, removal of the largest signal/trade/day, positive-day fractions, overlap exclusions, unresolved selections and realized-proxy drawdown. Drawdown excludes unrealized intratrade losses.

## Checkpoints and validation

Five sessions is the initial reliability review. Ten sessions is the next descriptive review. Neither checkpoint changes strategy thresholds or order permissions.

The first 20 full valid sessions form development. A preliminary research screen requires all of:

- At least 20 full sessions and 30 closed primary portfolio proxies.
- No unresolved selections in the primary or stress scenario.
- Positive total at the assumed 10 bps cost and a nonnegative total at 20 bps.
- Positive primary results on at least 60% of full sessions.
- Positive primary results after removing the single best day.

If development passes, the next 10 full sessions are a disjoint holdout with the same frozen rules. Its screen uses the same conditions, with at least 10 sessions and 15 closed proxies. Development results cannot be recomputed using holdout records. Both screens passing makes the cohort a candidate for a manual paper-trading review only. It never arms a broker, removes a kill switch or establishes profitability.

These thresholds are declared research screens, not statistical confidence guarantees. Overlapping signals are correlated; market days are the primary units of review. A strategy or policy change requires a new version/cohort and a newly declared future validation period. Previously viewed outcomes cannot be relabelled as unseen validation.

## Operations

The collector remains read-only. No paper/live order methods or new subscriptions are introduced.

`scripts/research_review.py --evidence /root/trader-evidence` rebuilds the review offline. The review files live under `equity_research/review/latest.json` and `latest.md` in the external evidence directory. The timestamp follows the latest input report so unchanged inputs and policy produce identical outputs on retries.

The installer rebuilds existing evidence once. Each end-of-day `finish` rebuilds the cumulative review before publishing. A cumulative-analysis failure does not prevent daily raw evidence from being archived. Only a review whose `as_of_session` matches the archived date accompanies that date's snapshot.

The read-only page is `/equity-research/review`; JSON is `/api/equity-research/review`. The daily page links to it. Checkpoints, development/holdout gates, cost scenarios, session exclusions and limitations remain visible. No controls on these pages can enable orders.

The existing scheduled collection remains 09:30-12:30 Eastern with preflight at 09:20 and publication attempts at 12:35/12:40/12:50. Install the pinned update after 13:00 when the observer is stopped. A successful installation is separate from verification of the next scheduled review/publication on the VPS.
