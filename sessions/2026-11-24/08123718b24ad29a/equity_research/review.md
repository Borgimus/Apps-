# Cumulative free-equity research review

As of session: 2026-11-24
Full valid sessions: 20; partial: 1; invalid: 1
Eligible signals in full sessions: 947
Stage: evidence_review_required
Order placement: disabled. Broker and options P&L: unavailable.

| Strategy | Horizon | Outcomes | Mean gross bps | After assumed 10 bps |
| --- | ---: | ---: | ---: | ---: |
| orb | 5 | 288 | -0.7827 | -10.7827 |
| orb | 15 | 288 | 1.889 | -8.111 |
| orb | 30 | 288 | 4.1228 | -5.8772 |
| vwap_reclaim | 5 | 652 | 1.0538 | -8.9462 |
| vwap_reclaim | 15 | 650 | 2.2375 | -7.7625 |
| vwap_reclaim | 30 | 646 | 5.1509 | -4.8491 |

| Assumed cost bps | Closed proxies | Unresolved | Proxy net dollars |
| ---: | ---: | ---: | ---: |
| 5 | 57 | 0 | -49.532348 |
| 10 | 57 | 0 | -73.239695 |
| 20 | 57 | 0 | -120.65439 |

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
| 2026-11-23 | full_valid | 38 |
| 2026-11-24 | full_valid | 51 |

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
