# Cumulative free-equity research review

As of session: 2026-11-25
Full valid sessions: 21; partial: 1; invalid: 1
Eligible signals in full sessions: 991
Stage: evidence_review_required
Order placement: disabled. Broker and options P&L: unavailable.

| Strategy | Horizon | Outcomes | Mean gross bps | After assumed 10 bps |
| --- | ---: | ---: | ---: | ---: |
| orb | 5 | 294 | -0.9403 | -10.9403 |
| orb | 15 | 294 | 0.264 | -9.736 |
| orb | 30 | 294 | 2.8814 | -7.1186 |
| vwap_reclaim | 5 | 690 | 0.483 | -9.517 |
| vwap_reclaim | 15 | 688 | -0.029 | -10.029 |
| vwap_reclaim | 30 | 684 | 3.2194 | -6.7806 |

| Assumed cost bps | Closed proxies | Unresolved | Proxy net dollars |
| ---: | ---: | ---: | ---: |
| 5 | 60 | 0 | -56.711618 |
| 10 | 60 | 0 | -81.643235 |
| 20 | 60 | 0 | -131.50647 |

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
| 2026-11-25 | full_valid | 44 |

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
