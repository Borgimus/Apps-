# Cumulative free-equity research review

As of session: 2026-11-12
Full valid sessions: 12; partial: 1; invalid: 1
Eligible signals in full sessions: 561
Stage: evidence_review_required
Order placement: disabled. Broker and options P&L: unavailable.

| Strategy | Horizon | Outcomes | Mean gross bps | After assumed 10 bps |
| --- | ---: | ---: | ---: | ---: |
| orb | 5 | 160 | 0.0408 | -9.9592 |
| orb | 15 | 160 | 8.2501 | -1.7499 |
| orb | 30 | 160 | 11.0296 | 1.0296 |
| vwap_reclaim | 5 | 396 | 1.5419 | -8.4581 |
| vwap_reclaim | 15 | 394 | 2.4596 | -7.5404 |
| vwap_reclaim | 30 | 391 | 1.8698 | -8.1302 |

| Assumed cost bps | Closed proxies | Unresolved | Proxy net dollars |
| ---: | ---: | ---: | ---: |
| 5 | 35 | 0 | 32.407998 |
| 10 | 35 | 0 | 17.915995 |
| 20 | 35 | 0 | -11.06801 |

## Sessions

| Date | Classification | Eligible signals |
| --- | --- | ---: |
| 2026-10-26 | partial | 30 |
| 2026-10-27 | full_valid | 64 |
| 2026-10-28 | full_valid | 53 |
| 2026-10-29 | full_valid | 46 |
| 2026-10-30 | full_valid | 53 |
| 2026-11-02 | full_valid | 44 |
| 2026-11-03 | full_valid | 51 |
| 2026-11-04 | full_valid | 34 |
| 2026-11-05 | full_valid | 41 |
| 2026-11-06 | full_valid | 43 |
| 2026-11-09 | full_valid | 30 |
| 2026-11-10 | invalid | 70 |
| 2026-11-11 | full_valid | 49 |
| 2026-11-12 | full_valid | 53 |

## Limitations

- Completed stock-bar entry/exit proxies with assumed costs; no fills or actual broker P&L.
- One long position, whole shares, no leverage, at most three entries per day. Shorts are excluded because availability is unverified.
- Exit is fixed at 30 minutes. No stop or profit target is inferred from intrabar price ordering.
- The daily realized-loss gate blocks subsequent entries; it is not a guaranteed loss cap.
- Pending slots remain reserved until exit evidence arrives. An unresolved selected outcome blocks subsequent replay exposure.
- Drawdown is based on realized proxy results and excludes unrealized intratrade losses.
- Signals/horizons overlap. Session counts represent market days, not independent trade samples.
- IEX covers one exchange; its volume, VWAP and signals can differ from consolidated data.
- First 20 full sessions are development; the next 10 are a disjoint frozen holdout. Checkpoints never enable orders.
- Five historical full sessions were known when this analysis policy was declared. Their replay is retrospective.
- Positive proxy results do not establish stock or options profitability or paper/live readiness.
