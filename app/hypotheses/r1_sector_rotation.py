"""Month-end information, next-session-close entries and drift-aware turnover."""
from collections import defaultdict
from statistics import mean
import math

from app.research.replay.stats import split_statistics, yearly_concentration

SECTORS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")


def month(day):
    return day.year * 12 + day.month - 1


def rotation(panel, costs=(5, 10, 20)):
    groups = defaultdict(list)
    for d in sorted(panel["SPY"]):
        groups[month(d)].append(d)
    closes = {key: days[-1] for key, days in groups.items()}
    observations, unresolved = [], []
    for m in sorted(groups):
        if not all(k in closes for k in (m - 12, m - 1, m, m + 1, m + 2)):
            continue
        entry, exit_at = groups[m + 1][0], groups[m + 2][0]
        returns = {}
        for sector in SECTORS:
            rows = panel[sector]
            past, last = rows.get(closes[m - 12]), rows.get(closes[m - 1])
            if past is not None and last is not None:
                returns[sector] = last / past - 1
        if len(returns) < 3:
            continue
        top = tuple(sorted(returns, key=lambda s: (-returns[s], s))[:3])
        arms = {"R1": top, "equal_weight": tuple(sorted(returns)), "SPY": ("SPY",)}
        held = set().union(*arms.values())
        missing = sorted(s for s in held if entry not in panel[s] or exit_at not in panel[s])
        if missing:
            unresolved.append({"date": entry.isoformat(), "missing_symbols": missing})
            continue
        period_returns = {s: panel[s][exit_at] / panel[s][entry] - 1 for s in held}
        observations.append((entry, exit_at, arms, period_returns))
    scenarios = {}
    for cost in costs:
        records, carry, curves = [], defaultdict(dict), defaultdict(lambda: 1.0)
        peaks, max_drawdown = defaultdict(lambda: 1.0), defaultdict(float)
        previous_exit = None
        for entry, exit_at, arms, period_returns in observations:
            if previous_exit != entry:
                carry.clear()
            record = {"date": entry.isoformat(), "exit_at": exit_at.isoformat(), "arms": {}}
            for arm, symbols in arms.items():
                target = {s: 1 / len(symbols) for s in symbols}
                weights = carry[arm]
                turnover = (sum(abs(target.get(s, 0) - weights.get(s, 0)) for s in set(target) | set(weights))
                            if arm != "SPY" else (0 if weights else 1))
                gross = mean(period_returns[s] for s in symbols)
                net = (1 + gross) * (1 - cost / 10000 * turnover) - 1
                curves[arm] *= 1 + net
                peaks[arm] = max(peaks[arm], curves[arm])
                max_drawdown[arm] = max(max_drawdown[arm], 1 - curves[arm] / peaks[arm])
                carry[arm] = {s: target[s] * (1 + period_returns[s]) / (1 + gross) for s in symbols}
                record["arms"][arm] = {"holdings": list(symbols), "turnover": turnover,
                                       "gross_bps": gross * 1e4, "net_bps": net * 1e4}
            records.append(record)
            previous_exit = exit_at
        scenarios[str(cost)] = {"months": records, "max_drawdown_pct": {a: d * 100 for a, d in max_drawdown.items()}}
    return scenarios, unresolved


def screen(panel):
    from datetime import date

    scenarios, missing = rotation(panel)
    primary = scenarios["10"]["months"]
    candidate = {date.fromisoformat(x["date"]): x["arms"]["R1"]["net_bps"] for x in primary}
    control = {date.fromisoformat(x["date"]): x["arms"]["equal_weight"]["net_bps"] for x in primary}
    stats = split_statistics(candidate, control, candidate, paired=True, lag=3)
    yearly, concentration = yearly_concentration(candidate, control, paired=True)
    stress = [x["arms"]["R1"]["net_bps"] for x in scenarios["20"]["months"]]
    checks = {"180_complete_months": len(candidate) >= 180,
              "primary_t_at_least_three": (stats["all"]["t"] or 0) >= 3,
              "positive_net_10_bps": bool(candidate) and mean(candidate.values()) > 0,
              "nonnegative_net_20_bps": bool(stress) and mean(stress) >= 0,
              "positive_temporal_splits": all((stats[k]["mean_bps"] or 0) > 0
                                               for k in ("first_half", "second_half", "holdout")),
              "year_contribution_at_most_40_percent": concentration is not None and concentration <= .4,
              "selected_prices_resolved": not missing}
    costs_summary = {}
    for cost, item in scenarios.items():
        vals = item["months"]
        costs_summary[cost] = {
            "months": len(vals), "max_drawdown_pct": item["max_drawdown_pct"],
            "mean_net_bps": {arm: mean(x["arms"][arm]["net_bps"] for x in vals) if vals else None
                             for arm in ("R1", "equal_weight", "SPY")},
            "mean_turnover": {arm: mean(x["arms"][arm]["turnover"] for x in vals) if vals else None
                              for arm in ("R1", "equal_weight", "SPY")},
        }
    return {"primary": stats["all"], "stats": stats, "checks": checks, "unresolved": missing,
            "candidate_mean_net_bps": mean(candidate.values()) if candidate else None,
            "control_mean_net_bps": mean(control.values()) if control else None,
             "annual_incremental_bps": yearly, "annual_max_contribution": concentration,
            "scenarios": costs_summary,
            "latest_completed_holdings": primary[-1] if primary else None,
            "independence_unit": "month",
            "status": "blocked" if missing or not candidate else "passed_phase1" if all(checks.values()) else "killed",
            "reason": "Selected monthly prices incomplete" if missing or not candidate else
                      "Monthly screening gates passed" if all(checks.values()) else "Monthly screening gates failed"}
