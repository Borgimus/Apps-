"""Volatility-index comparison. Stage 2 is a GET-only options quote collector."""
from statistics import mean
import math

import httpx
import numpy as np

from app.research.history.bars import utc
from app.research.replay.stats import newey_west, split_statistics, yearly_concentration


def compare(panel, ticker, horizon):
    days = sorted(panel["SPY"])
    prices = panel["SPY"]
    spreads = {}
    rows, tail, absent = [], 0, 0
    for i, day in enumerate(days):
        implied = panel[ticker].get(day)
        if implied is None:
            absent += 1
            continue
        if i + horizon >= len(days):
            tail += 1
            continue
        closes = [prices[d] for d in days[i:i + horizon + 1]]
        returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        realized = 100 * math.sqrt(252 * mean(r*r for r in returns))
        spread = implied - realized
        spreads[day] = spread * 100  # bps of annualized volatility, not P&L
        rows.append((day, implied, realized, spread))
    zero = dict.fromkeys(spreads, 0.0)
    stats = split_statistics(spreads, zero, days, paired=True, lag=horizon)
    annual, concentration = yearly_concentration(spreads, zero, paired=True)
    yearly = {}
    for year in sorted({d.year for d in spreads}):
        vals = [v for d, v in spreads.items() if d.year == year]
        yearly[str(year)] = newey_west(vals, horizon)
    worst = sorted(rows, key=lambda x: x[-1])[:math.ceil(len(rows) * .01)]
    return {"stats": stats, "mean_spread_vol_points": mean(x[3] for x in rows) if rows else None,
            "implied_above_realized_fraction": sum(x[3] > 0 for x in rows) / len(rows) if rows else None,
            "spread_quantiles_vol_points": dict(zip(("p01", "p05", "p50", "p95", "p99"),
                  np.quantile([x[3] for x in rows], [.01, .05, .5, .95, .99]).tolist())) if rows else {},
            "annual_cluster_statistics": yearly, "annual_max_contribution": concentration,
            "annual_total_spread_bps": annual, "unmatured_horizons": tail, "unavailable_index_closes": absent,
            "worst_one_percent": [{"date": d.isoformat(), "implied_vol_points": i,
                                   "realized_vol_points": r, "spread_vol_points": s} for d, i, r, s in worst]}


def screen(panel):
    standard = compare(panel, "^VIX", 21)
    short = compare(panel, "^VIX9D", 5)
    s = standard["stats"]
    conc = standard["annual_max_contribution"]
    checks = {"at_least_100_observations": s["all"]["candidate_days"] >= 100,
              "primary_t_at_least_three": (s["all"]["t"] or 0) >= 3,
              "positive_temporal_splits": all((s[k]["mean_bps"] or 0) > 0
                                               for k in ("first_half", "second_half", "holdout")),
              "year_contribution_at_most_40_percent": conc is not None and conc <= .4}
    return {"primary": s["all"], "checks": checks, "VIX": standard, "VIX9D_diagnostic": short,
            "independence_unit": "session date; overlapping windows adjusted with 21-lag HAC",
            "status": "blocked" if all(checks.values()) else "killed",
            "informational_screen_passed": all(checks.values()),
            "reason": "Free information screen passed; short-dated SPY stage requires owner-approved OPRA data"
                      if all(checks.values()) else "Free information screen failed preregistered criteria",
            "option_returns": None, "option_stage": "blocked_pending_owner_opra"}


class OpraCollector:
    """Opt-in snapshots for a separately approved V1 Stage 2 study."""
    def __init__(self, key, secret, *, transport=None):
        if not key or not secret:
            raise ValueError("OPRA collector needs existing entitled keys")
        self.client = httpx.Client(base_url="https://data.alpaca.markets", timeout=20, transport=transport,
                                  headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def close(self):
        self.client.close()

    def snapshot(self, *, first_expiry, last_expiry, observed_clock, max_age_seconds=90):
        if not 0 <= (last_expiry - first_expiry).days <= 7 or max_age_seconds <= 0:
            raise ValueError("Invalid V1 expiration or quote freshness range")
        params = {"feed": "opra", "expiration_date_gte": first_expiry.isoformat(),
                  "expiration_date_lte": last_expiry.isoformat(), "limit": 1000}
        records, missing, tokens = [], [], set()
        for _ in range(20):
            response = self.client.get("/v1beta1/options/snapshots/SPY", params=params)
            response.raise_for_status()
            at = utc(observed_clock())
            obj = response.json()
            snaps = obj.get("snapshots")
            if not isinstance(snaps, dict):
                raise ValueError("OPRA snapshots are missing")
            for symbol, row in sorted(snaps.items()):
                quote = row.get("latestQuote") or {}
                greeks = row.get("greeks") or {}
                iv = row.get("impliedVolatility")
                bid, ask = quote.get("bp"), quote.get("ap")
                timestamp = utc(quote["t"]) if quote.get("t") else None
                q_valid = (isinstance(bid, (float, int)) and isinstance(ask, (float, int))
                           and math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask
                           and timestamp is not None and 0 <= (at - timestamp).total_seconds() <= max_age_seconds)
                g_valid = (isinstance(iv, (float, int)) and math.isfinite(iv) and iv > 0
                           and all(isinstance(greeks.get(g), (float, int)) and math.isfinite(greeks[g])
                                   for g in ("delta", "gamma", "theta", "vega")))
                record = {"symbol": symbol, "feed": "opra", "observed_at": at.isoformat(),
                          "quote": quote if q_valid else None, "greeks": greeks if g_valid else None,
                          "iv": iv if g_valid else None, "quote_valid": q_valid, "greeks_valid": g_valid}
                records.append(record)
                if not q_valid or not g_valid:
                    missing.append(symbol)
            token = obj.get("next_page_token")
            if not token:
                return {"records": records, "unusable_symbols": missing, "broker_entries_enabled": False}
            if not isinstance(token, str) or token in tokens:
                raise ValueError("Invalid OPRA snapshot page token")
            tokens.add(token)
            params["page_token"] = token
        raise ValueError("OPRA snapshot page limit exceeded")
