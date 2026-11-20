# Cumulative free-equity research review

As of session: 2026-11-20
Full valid sessions: 18; partial: 1; invalid: 1
Eligible signals in full sessions: 858
Stage: evidence_review_required
Order placement: disabled. Broker and options P&L: unavailable.

| Strategy | Horizon | Outcomes | Mean gross bps | After assumed 10 bps |
| --- | ---: | ---: | ---: | ---: |
| orb | 5 | 263 | -0.593 | -10.593 |
| orb | 15 | 263 | 4.7747 | -5.2253 |
| orb | 30 | 263 | 11.153 | 1.153 |
| vwap_reclaim | 5 | 588 | 1.5331 | -8.4669 |
| vwap_reclaim | 15 | 586 | 2.8728 | -7.1272 |
| vwap_reclaim | 30 | 582 | 7.012 | -2.988 |

| Assumed cost bps | Closed proxies | Unresolved | Proxy net dollars |
| ---: | ---: | ---: | ---: |
| 5 | 51 | 0 | -52.092765 |
| 10 | 51 | 0 | -73.14553 |
| 20 | 51 | 0 | -115.25106 |

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
| 2026-11-13 | full_valid | 45 |
| 2026-11-16 | full_valid | 66 |
| 2026-11-17 | full_valid | 68 |
| 2026-11-18 | full_valid | 47 |
| 2026-11-19 | full_valid | 31 |
| 2026-11-20 | full_valid | 40 |

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
