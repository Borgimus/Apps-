# Zero-subscription equity-signal research

Declared October 24, 2026. Protocol: `iex_equity_signal_screen_v1_2026_10_24`.

## Purpose and cost

The app has not established positive expectancy. Recovery must produce useful evidence without requiring a paid data plan. The recovery installer now defaults to `equity_research`, using the existing Alpaca Basic account and its free IEX stock bars. No subscription, deposit, options quote request, or broker order is made by this mode. Existing VPS costs are separate.

The original options research remains suspended. Options P&L and broker realized P&L are unavailable in these new reports. Free research never contributes to the old options-readiness count, and it never removes the broker kill switch.

## Fixed research contract

- Universe: the 38 symbols in the existing enabled groups in `config/ticker_universe.yaml`.
- Window: 09:30-12:30 Eastern on broker-calendar market days. New prospective observations stop before 12:00.
- Source: explicit `feed=iex` on `GET /v2/stocks/bars`, five-minute raw OHLCV, regular-session bars only. Initial historical bars provide causal warm-up; subsequent requests refresh the current day.
- Signals: existing ORB (15-minute range, two confirmation bars, volume confirmation, minimum range 0.5) and VWAP reclaim/rejection (0.2% proximity, two confirmation bars). The observer does not change production strategy code or relax options gates.
- Only completed bars enter evaluation. The signal's confirmation time is the end of its confirming five-minute bar.
- Actual discovery time is captured after the HTTP response. Signals discovered more than ten minutes after confirmation, or whose next entry proxy starts at/after noon, are excluded from prospective summaries and retained as diagnostics.
- Entry proxy: first five-minute bar beginning at or after discovery. Its open becomes available only when the completed bar arrives. No fill is assumed.
- Outcomes: direction-adjusted underlying returns at 5, 15 and 30 minutes from that proxy, in basis points. Each outcome requires every constituent bar; missing bars produce null outcomes. One hundred basis points equals 1%.
- Reports show sample counts, mean, median, positive fraction and sensitivity to assumed round-trip costs of 5/10/20 basis points. This sensitivity is not a fill/slippage/borrow model.
- First-observed completed bar values and signal IDs are preserved. Later provider revisions cannot rewrite earlier decisions. Same-day restart under a different source commit is refused.
- Signals and horizons overlap; counts are not independent trades. Gross directional movement is a screening measure. It does not establish options expectancy, execution quality, position sizing, portfolio returns or profitability.

## Data quality and failure handling

IEX covers one exchange. Its volume, VWAP and signal set can differ from consolidated data. Results therefore belong to this new cohort and cannot be merged with the old Tradier/OPRA cohort.

The client validates HTTP responses, schemas, finite prices/volumes, OHLC consistency, timestamps and pagination. Failed or malformed responses remain errors. During the research window, SPY and at least 80% of the symbol universe must have sufficiently recent completed bars for healthy coverage. Provider errors remain visible for the day. No empty-data fallback creates synthetic quotes or outcomes.

An independent watchdog checks research provenance, order-disable state, heartbeat freshness and data quality. Existing ntfy delivery is reused when available. Missing notification delivery is recorded explicitly; local research status and incidents remain available without requiring another service account.

## Recovery and operation

Run the exact published release through `scripts/ops/recover.sh RELEASE_SHA`. Free research is the default; it does not attempt OPRA authorization.

The installer retains its existing database/evidence backups, source checks, full tests and broker-flat verification. It then checks free IEX access, runs the dedicated research preflight and installs:

- 09:20 weekdays: research preflight.
- 09:30 weekdays: read-only observer.
- Every minute: independent research watchdog during the relevant window.
- 12:35, 12:40 and 12:50 weekdays: broker-flat verification and artifact-publication attempts.

The broker calendar handles holidays. The broker kill switch stays active. Research has its own date/SHA readiness marker; `.session_armed` remains absent. Existing dashboard broker start/restart endpoints are disabled in this mode.

The dedicated page is `/equity-research`, with JSON at `/api/equity-research`, on the existing private dashboard. It displays the latest report date and data status explicitly. Before the first market session it displays an awaiting-session state.

Reports, append-only raw observations and restart state live under `/root/trader-evidence/equity_research/`. Immutable artifact snapshots include the new stream separately. An expired GitHub write credential can delay uploads; local evidence is preserved and research collection continues. Public source retrieval requires no personal token.

## Validation and remaining proof

Local full suite with the broker kill switch active: **1,095 passed, 2 skipped in 44.47 seconds**. New regression tests cover IEX-only GET requests, pagination, forming/future/invalid bars, stale canaries, causal discovery, delayed-entry proxies, missing horizons, signal deduplication, revision preservation, same-day source boundaries, absent options P&L, watchdog validation, free installer lock handoff and archived research evidence.

The host has already verified the existing Alpaca paper account is flat. This release still requires the host's IEX canary and dedicated research preflight to pass. Future market-day coverage, alert receipt, Tailscale access and GitHub artifact writes are unverified until exercised. No strategy profitability is claimed.
