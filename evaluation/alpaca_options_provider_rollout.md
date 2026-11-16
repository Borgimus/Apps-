# Replacing Tradier with Alpaca OPRA options data

Status: **prepared, not deployed.** This branch changes `app/brokers/alpaca_broker.py`,
a frozen broker adapter. The operations installer refuses releases that touch
`app/brokers`, and the fingerprint blocks sessions until a new baseline is
declared. Deploy it only together with the cohort declaration below, after a
decision to resume observation.

## Why

Tradier deactivated the account that supplied options quotes and Greeks.
From 2026-09-22 every request returned 401. There is no Tradier path back
without funding that account. Alpaca, already the paper broker, can supply
options data, but only its OPRA feed meets the existing evidence rules:

- `app/trading/quote_evidence.py::fill_evidence_valid` and the shadow book
  accept only `opra` / `tradier_opra` quotes as fill evidence. On the
  `indicative` feed, or with no feed configured, every eligible outcome is
  unpriced and every session is excluded from observation progress.
- Entry filters require delta; Greeks are available on the entitled feed.

Alpaca's documentation describes OPRA as part of its paid market-data plan and
the Basic plan as indicative only. Confirm the current plan and price with
Alpaca before subscribing; this document does not.

## What this branch changes

| Change | Before | After |
| --- | --- | --- |
| Failed snapshot chunk | Skipped silently; chain returned with no quotes | Raises; counted by provider metering |
| Contract with no valid, fresh quote | Listed at bid = ask = 0, read as illiquid | Omitted, same as the Tradier adapter |
| Non-tradable contract | Listed | Omitted |
| `chain.fetched_at` | Naive UTC; an Eastern-time host reads it as 4 h in the future, so the 90 s staleness check never fired | Aware UTC |
| Preflight canary | — | Fails when the provider is Alpaca and `ALPACA_OPTIONS_FEED` is not `opra` |
| `scripts/ops/common.sh` | `TRADIER_MARKET_DATA_MODE=observe` | `off`: nothing calls Tradier |

Not changed, by decision:

- **Greek age.** Tradier Greeks refresh roughly hourly and would need their own
  age check if Tradier were ever restored. Alpaca computes Greeks from the
  snapshot quote, which the freshness rule above already bounds.
- **Open interest from `get_option_quote`** is still 0 on Alpaca (the snapshot
  endpoint does not carry it). No consumer reads it from that path; chains take
  open interest from the contracts endpoint.

## Deployment (VPS, with sessions stopped)

1. Subscribe the Alpaca account to a plan that includes OPRA options data.
2. In `/root/trader/.env`: `OPTIONS_DATA_PROVIDER=alpaca`,
   `ALPACA_OPTIONS_FEED=opra`. Remove the Tradier token.
3. Verify entitlement before anything else:
   `.venv/bin/python scripts/session_ops.py check-options-data`
   It must print `OPTIONS_DATA_READY` with a non-zero contract count during
   market hours. Pre-market, a zero count is expected (quotes are filtered as
   stale); an error is not.
4. Declare the new cohort: record `options_data_provider: "alpaca"` and the new
   `alpaca_broker` adapter hash in `evaluation/phase3_tracking.json` →
   `phase3_fingerprint`, with an amendment block stating the date, the verified
   commit and the step 3 output. Regenerate hashes with
   `python scripts/capture_session_fingerprint.py`.
5. Deploy that commit. The installer will refuse it because it changes frozen
   files; this is a reviewed exception, performed manually with sessions
   stopped. Then run `scripts/ops/preflight.sh --check-only`.

## What restarts from zero

Observation progress keys on provider, adapter hash and cohort, so the
Tradier cohort's sessions and its 6 eligible opportunities do not carry over.
That cohort is closed (see the outcome addendum in
`evaluation/checkpoint_review_preregistration_2026_09.md`). The new cohort's
first question should be the one in that addendum: whether any signal has an
edge on the underlying at all, before any option is selected.
