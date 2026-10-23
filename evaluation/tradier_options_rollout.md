# Tradier options data with Alpaca paper execution

Default behavior is unchanged: OPTIONS_DATA_PROVIDER=alpaca.

The optional OPTIONS_DATA_PROVIDER=tradier uses TRADIER_MARKET_DATA_TOKEN
for production GET /v1/markets/quotes requests only. The HTTP client blocks
other hosts, paths and methods and does not follow redirects. It has no
Tradier account or order methods. The token itself is a production credential
with broader permissions, so keep it private.

Alpaca remains the paper execution broker, calendar, eligible-contract source,
and equity data source. Only Alpaca contracts explicitly marked tradable enter
the new option chain. Tradier supplies option bid/ask, volume, open interest,
and Greeks. Missing quotes do not fall back to Alpaca indicative prices.
Both bid and ask dates are required, future dates are rejected, and the older
side must be at most 60 seconds old. Invalid/crossed/nonpositive quotes are
excluded. Provider failures propagate. Missing Greeks stay missing; provider
Greeks can update less frequently than quotes and are not certified fresh by
this adapter. The feed label is tradier_opra, separate from Alpaca opra.

## Deployment sequence

1. Keep KILL_SWITCH active and the session unarmed. Preserve existing backups.
2. Fetch and merge the tested branch using the operator's one-command workflow.
3. Run tests from a clean git archive with the VPS virtual environment.
4. Back up the private environment file before adding the production data token
   via hidden input. Keep BROKER=alpaca and LIVE_TRADING_ENABLED=false. Set
   OPTIONS_DATA_PROVIDER=tradier only after credentials are stored safely.
   Do not change TRADIER_ACCESS_TOKEN or TRADIER_BASE_URL for execution.
5. Run read-only account/endpoint and data checks. Monday September 7 is a
   market holiday; Friday quotes cannot establish freshness during market hours.
6. Capture a new fingerprint and declare a separate data-source cohort after
   market-hours quote verification. The old baseline intentionally fails with
   Tradier enabled. Record options_data_provider and options_data_adapter_hash
   from the capture, along with the normal fingerprint fields. Do not relabel
   historical sessions or remove freshness guards to make the check pass.
7. Review preflight and automation before separately arming any paper session.

Neither credentials nor VPS settings are changed by merging this branch.
Historical ledger rebuilding and broker-fill reconciliation remain separate.
The shadow simulation remains portfolio_validated=false and does not count
as readiness evidence. Tradier data does not establish a profitable strategy.

Rollback: while stopped and unarmed, restore OPTIONS_DATA_PROVIDER=alpaca.
This restores prior data behavior but does not resolve Alpaca OPRA entitlement.

References:
- https://docs.tradier.com/docs/market-data
- https://docs.tradier.com/reference/brokerage-api-markets-get-quotes
