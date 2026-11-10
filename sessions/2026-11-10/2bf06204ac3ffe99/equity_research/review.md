# Cumulative free-equity research review

As of session: 2026-11-10
Full valid sessions: 10; partial: 1; invalid: 1
Eligible signals in full sessions: 459
Stage: evidence_review_required
Order placement: disabled. Broker and options P&L: unavailable.

| Strategy | Horizon | Outcomes | Mean gross bps | After assumed 10 bps |
| --- | ---: | ---: | ---: | ---: |
| orb | 5 | 137 | 0.1801 | -9.8199 |
| orb | 15 | 137 | 10.8771 | 0.8771 |
| orb | 30 | 137 | 14.8598 | 4.8598 |
| vwap_reclaim | 5 | 317 | 1.7951 | -8.2049 |
| vwap_reclaim | 15 | 315 | -0.4038 | -10.4038 |
| vwap_reclaim | 30 | 312 | -2.5179 | -12.5179 |

| Assumed cost bps | Closed proxies | Unresolved | Proxy net dollars |
| ---: | ---: | ---: | ---: |
| 5 | 29 | 0 | 24.061312 |
| 10 | 29 | 0 | 11.977625 |
| 20 | 29 | 0 | -12.18975 |

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
