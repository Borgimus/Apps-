"""Orchestrates registered intraday arms without sharing their order slots."""
from collections import Counter, defaultdict, deque
from datetime import timedelta, time
from pathlib import Path
from tempfile import TemporaryDirectory
from statistics import mean

import numpy as np

from app.research.baseline import STRATEGY_PARAMETERS
from app.research.equity_observer import ET, INTERVAL, frame, next_entry_bar
from app.research.history.bars import utc
from app.strategies import OpeningRangeBreakoutStrategy, VWAPReclaimStrategy
from .controls import retime
from .costs import net_bps
from .engine import HypothesisSignal, holding_window, outcome, run
from .portfolio import SizedSessions, policy_session, scenarios
from .sources import index_current
from .spool import SessionSpool
from .stats import split_statistics, yearly_concentration

EXITS = ("30m", "60m", "12:30")
COSTS = (5, 10, 20)


def historical_frozen(session, prior):
    """Replay frozen strategy functions on monotonic historical availability.

    Both frozen rules operate within each day. Their only cross-day dependency
    is the count required before the observer calls them. A prefix of up to
    17 previously published bars supplies that count.
    """
    available = index_current(session)
    warmups = defaultdict(list)
    for earlier in tuple(prior)[-5:]:
        for bar in earlier.current():
            warmups[bar.symbol].append(bar)
    results = []
    for symbol, by_time in available.items():
        current = sorted(by_time.values(), key=lambda b: b.start)
        warmup = warmups[symbol][-17:]
        rows = frame([b.frozen_row() for b in warmup + current])
        for name, strategy in (
            ("orb", OpeningRangeBreakoutStrategy(dict(STRATEGY_PARAMETERS["orb"]))),
            ("vwap_reclaim", VWAPReclaimStrategy(dict(STRATEGY_PARAMETERS["vwap_reclaim"])))
        ):
            for signal in strategy.generate_signals(rows, symbol):
                stamp = utc(signal.timestamp)
                first = by_time.get(stamp)
                if first is None or first.end.astimezone(ET).time() >= time(12):
                    continue
                count = max(0, 17 - len(warmup))
                if count > len(current):
                    continue
                observed = max(first.available_at, current[count - 1].available_at) if count else first.available_at
                entry = next_entry_bar(observed)
                eligible = observed - first.end <= timedelta(minutes=10) and entry.time() < time(12)
                confirmed = first.end.astimezone(ET).isoformat()
                results.append({"id": f"{symbol}|{name}|{signal.direction.value}|{confirmed}",
                                "strategy": name, "direction": signal.direction.value, "symbol": symbol,
                                "confirmed_at": confirmed, "observed_at": observed.isoformat(),
                                "entry_bar": entry.isoformat(), "prospective": eligible})
    return tuple(results)


def orb_controls(session, symbols, hypothesis):
    for event in session.recorded_signals:
        if (event["strategy"] != "orb" or event["symbol"] not in symbols or not event["prospective"]
                or utc(event["observed_at"]).astimezone(ET).time() > time(11, 30)):
            continue
        arm = "inverse_orb" if event["direction"] == "short" else "orb_long"
        yield HypothesisSignal(arm, event["symbol"], utc(event["observed_at"]), control_of=hypothesis)


def record(signal, index):
    return {"id": signal.hypothesis_id, "symbol": signal.symbol, "date": signal.day.isoformat(),
            "decided_at": signal.decided_at.isoformat(), "control_of": signal.control_of,
            "features": dict(signal.features), "outcomes": {e: outcome(signal, index, e) for e in signal.exits}}


def arm_name(signal):
    return "sector_etf" if signal.hypothesis_id.startswith("sector_etf.") else signal.hypothesis_id


def replay_day(session, rule, prior):
    found = run(session, rule.prepare(tuple(prior)))
    if hasattr(rule, "finish"):
        rule.finish(found)
    prior.append(session)
    return found


def coverage_audit(source, rule, *, progress=None):
    """Locate the missing 30-minute holding bars for the S1/S3 primary arms."""
    prior = deque(maxlen=20)
    gaps = []
    market_days = 0
    for market_days, session in enumerate(source.sessions(), 1):
        if progress and market_days % 250 == 0:
            progress(f"{rule.id}: audited {market_days} sessions through {session.day}")
        index = index_current(session)
        for signal in replay_day(session, rule, prior):
            arm = arm_name(signal)
            if arm not in {rule.id, rule.control}:
                continue
            entry, end = holding_window(signal, "30m")
            symbol_bars = index.get(signal.symbol, {})
            required = [entry + INTERVAL * i for i in range((end - entry) // INTERVAL)]
            missing = [at.isoformat() for at in required if at not in symbol_bars]
            if missing:
                gaps.append({"arm": arm, "signal_id": signal.hypothesis_id, "symbol": signal.symbol,
                             "date": session.day.isoformat(), "decided_at": signal.decided_at.isoformat(),
                             "entry_at": entry.isoformat(), "exit_at": end.isoformat(),
                             "missing_bar_starts": missing})
    return {"id": rule.id, "market_days": market_days, "unresolved": len(gaps),
            "unresolved_by_arm": dict(Counter(gap["arm"] for gap in gaps)),
            "unresolved_by_symbol": dict(Counter(gap["symbol"] for gap in gaps)), "gaps": gaps}


def daily(rows, cost, exit_id="30m"):
    gross = defaultdict(list)
    for row in rows:
        result = row["outcomes"].get(exit_id)
        if result is not None:
            gross[row["day"]].append(net_bps(result["gross_bps"], cost))
    return {day: mean(values) for day, values in gross.items()}


def compact(portfolios):
    return {cost: {k: v for k, v in item.items() if k not in {"days", "trades"}}
            for cost, item in portfolios.items()}


def matched_timing(candidate, sessions_by_date):
    rng = np.random.default_rng(20261122)
    calendar = defaultdict(list)
    signal_days = {(s.symbol, s.day) for s in candidate}
    chosen = set()
    for day in sorted(sessions_by_date):
        calendar[day.year].append(day)
    for signal in candidate:
        options = [d for d in calendar[signal.day.year] if (signal.symbol, d) not in signal_days
                   and (signal.symbol, d) not in chosen and signal.symbol in sessions_by_date[d]]
        if options:
            day = options[int(rng.integers(len(options)))]
            chosen.add((signal.symbol, day))
            yield retime(signal, day, "same_clock_non_signal_day")


def study(root, source, rule, *, archive=False, scratch_dir=None, progress=None):
    with TemporaryDirectory(prefix="hypothesis-replay-", dir=scratch_dir) as directory:
        return _study(root, source, rule, SessionSpool(Path(directory)), archive=archive, progress=progress)


def _study(root, source, rule, spool, *, archive, progress):
    prior = deque(maxlen=20)
    markets, spy_days, signals, arms = [], [], [], defaultdict(list)
    symbols_by_date = {}
    for session in source.sessions():
        markets.append(session.day)
        index = index_current(session)
        if index.get("SPY"):
            spy_days.append(session.day)
        if archive and session.failure_count:
            return {"id": rule.id, "status": "blocked",
                    "reason": f"Archive contains a data or strategy failure on {session.day}",
                    "primary": None, "unresolved": session.failure_count, "checks": {}}
        symbols_by_date[session.day] = frozenset(index)
        if progress and len(markets) % 250 == 0:
            progress(f"{rule.id}: processed {len(markets)} sessions through {session.day}")
        if not archive:
            session.recorded_signals = historical_frozen(session, prior)
        found = replay_day(session, rule, prior)
        if rule.id == "S2":
            options = tuple(s for s in rule.symbols if s not in {"IWM", "DIA"})
            found.extend(orb_controls(session, options, rule.id))
        primary = [s for s in found if s.hypothesis_id == rule.id]
        signals.extend(primary)
        for signal in found:
            arms[arm_name(signal)].append({**record(signal, index), "day": signal.day})
        spool.put(session, primary)
    if rule.id in {"S1", "S2"}:
        by_day = defaultdict(list)
        for signal in matched_timing(signals, symbols_by_date):
            by_day[signal.day].append(signal)
        for day, controls in by_day.items():
            loaded, _ = spool.get(day)
            index = index_current(loaded)
            for signal in controls:
                arms["same_clock_non_signal_day"].append({**record(signal, index), "day": day})

    def policy_sessions(with_candidates, shared):
        for day in markets:
            loaded, primary = spool.get(day)
            yield policy_session(loaded, primary if with_candidates else (), shared=shared)
    candidate, control = arms[rule.id], arms[rule.control]
    own, benchmark = daily(candidate, 10), daily(control, 10)
    missing = sum(r["outcomes"]["30m"] is None for r in candidate + control)
    # An unresolved pair makes this a blocked diagnostic. Avoid using the
    # other arm's price to manufacture a matched observation.
    if rule.paired:
        included = set(own) & set(benchmark)
        own = {d: v for d, v in own.items() if d in included}
        benchmark = {d: v for d, v in benchmark.items() if d in included}
    stats = split_statistics(own, benchmark, markets, paired=rule.paired)
    yearly, concentration = yearly_concentration(own, benchmark, paired=rule.paired)
    stress = daily(candidate, 20)
    checks = {"history_at_least_five_years": len(spy_days) >= 2 and (spy_days[-1] - spy_days[0]).days >= 5 * 365,
              "100_independent_candidate_days": len(own) >= 100,
              "primary_t_at_least_three": (stats["all"]["t"] or 0) >= 3,
              "positive_net_10_bps": bool(own) and mean(own.values()) > 0,
              "nonnegative_net_20_bps": bool(stress) and mean(stress.values()) >= 0,
              "positive_temporal_splits": all((stats[k]["mean_bps"] or 0) > 0
                                              for k in ("first_half", "second_half", "holdout")),
              "year_contribution_at_most_40_percent": concentration is not None and concentration <= .4,
              "primary_outcomes_resolved": missing == 0}
    forecast = {}
    for name, rows in arms.items():
        forecast[name] = {"signals": len(rows),
                          "independent_days": len({r["day"] for r in rows}),
                          "max_adverse_bps_30m": min((r["outcomes"]["30m"]["mae_bps"] for r in rows
                                                  if r["outcomes"]["30m"] is not None), default=None),
                          "horizons": {e: {str(c): mean(vals) if (vals := daily(rows, c, e).values()) else None
                                             for c in COSTS} for e in EXITS}}
    return {"id": rule.id, "status": "blocked" if not checks["history_at_least_five_years"] or missing
            else "passed_phase1" if all(checks.values()) else "killed",
            "reason": "Historical coverage incomplete or selected primary prices missing"
                      if not checks["history_at_least_five_years"] or missing else
                      "Historical screen passed" if all(checks.values()) else "Historical screening gates failed",
            "checks": checks, "primary": stats["all"], "stats": stats, "annual_incremental_bps": yearly,
            "annual_max_contribution": concentration, "unresolved": missing,
            "market_days": len(markets), "first_session": str(markets[0]) if markets else None,
            "last_session": str(markets[-1]) if markets else None,
            "signal_days": len({s.day for s in signals}), "signals": len(signals),
            "frozen_reference_scope": "full archived universe" if archive else "historical candidate input panel",
            "observed_triggers_by_symbol": dict(Counter(s.symbol for s in signals)),
            "arms": forecast,
            "portfolio": {"standalone": compact(scenarios(root, SizedSessions(len(markets), lambda: policy_sessions(True, False)))),
                          "shared": compact(scenarios(root, SizedSessions(len(markets), lambda: policy_sessions(True, True)))),
                          "frozen_reference": compact(scenarios(root, SizedSessions(len(markets), lambda: policy_sessions(False, True))))}}
