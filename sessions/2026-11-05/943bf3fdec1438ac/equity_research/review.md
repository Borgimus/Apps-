# Cumulative free-equity research review

As of session: 2026-11-05
Full valid sessions: 8; partial: 1; invalid: 0
Eligible signals in full sessions: 386
Stage: collecting_to_10
Order placement: disabled. Broker and options P&L: unavailable.

| Strategy | Horizon | Outcomes | Mean gross bps | After assumed 10 bps |
| --- | ---: | ---: | ---: | ---: |
| orb | 5 | 112 | 6.8484 | -3.1516 |
| orb | 15 | 112 | 17.1018 | 7.1018 |
| orb | 30 | 112 | 25.6156 | 15.6156 |
| vwap_reclaim | 5 | 269 | 2.4256 | -7.5744 |
| vwap_reclaim | 15 | 267 | 0.7842 | -9.2158 |
| vwap_reclaim | 30 | 264 | -3.4302 | -13.4302 |

| Assumed cost bps | Closed proxies | Unresolved | Proxy net dollars |
| ---: | ---: | ---: | ---: |
| 5 | 23 | 0 | 16.609223 |
| 10 | 23 | 0 | 7.193445 |
| 20 | 23 | 0 | -11.63811 |

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
