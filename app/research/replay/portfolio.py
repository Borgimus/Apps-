"""Adapt research signals to the immutable equity portfolio policy and evaluator."""
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from app.research.equity_observer import ET, next_entry_bar, stamp
from app.research.portfolio_review import Session as PolicySession, load_policy, replay


def policy_session(session, signals, *, shared=False):
    events = []
    for s in signals:
        events.append({"id": f"{s.hypothesis_id}|{s.symbol}|{s.decided_at.isoformat()}",
                       "symbol": s.symbol, "strategy": s.hypothesis_id,
                       "direction": "long" if s.direction == 1 else "short",
                       "observed_at": s.decided_at.isoformat(),
                       "entry_bar": next_entry_bar(s.decided_at).isoformat(), "prospective": True})
    if shared:
        events.extend(session.recorded_signals)
    bars, availability = defaultdict(dict), {}
    symbols = list(dict.fromkeys((*session.symbols, *(e["symbol"] for e in events))))
    for sym in symbols:
        bars[sym] = {}
    for bar in session.current():
        t = bar.start.astimezone(ET)
        bars[bar.symbol][t] = bar.frozen_row()
        availability[(bar.symbol, t)] = bar.available_at
    generated = max((b.available_at for b in session.bars), default=None)
    if generated is None:
        raise ValueError("Cannot construct a portfolio session with no observations")
    report = {"date": session.day.isoformat(), "symbols": symbols, "signals": events,
              "generated_at": generated.isoformat()}
    return PolicySession(report, "research", bars=bars, available=availability)



@dataclass(frozen=True)
class SizedSessions:
    """Provide the frozen evaluator a count and a fresh lazy pass over sessions."""

    count: int
    factory: Callable[[], Iterator[PolicySession]]

    def __len__(self):
        return self.count

    def __iter__(self):
        return self.factory()


def scenarios(root, sessions):
    policy = load_policy(root)
    return {str(cost): replay(sessions, policy, cost) for cost in policy["round_trip_cost_bps"]}
