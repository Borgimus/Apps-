"""A strategy sees only the immutable prefix presented by the replay clock."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from types import MappingProxyType
from typing import Mapping

from app.research.equity_observer import ET, INTERVAL, next_entry_bar
from app.research.history.bars import utc
from .sources import Bar, Session


class LookAheadError(ValueError):
    """Raised when a rule requests a forming or not-yet-delivered bar."""


@dataclass(frozen=True, slots=True)
class HypothesisSignal:
    hypothesis_id: str
    symbol: str
    decided_at: datetime
    direction: int = 1
    exits: tuple[str, ...] = ("30m", "60m", "12:30")
    features: Mapping = field(default_factory=dict)
    control_of: str | None = None

    def __post_init__(self):
        if self.direction not in {-1, 1} or not self.symbol:
            raise ValueError("Invalid hypothesis symbol or direction")
        object.__setattr__(self, "decided_at", utc(self.decided_at))
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    @property
    def day(self):
        return self.decided_at.astimezone(ET).date()


class View:
    def __init__(self, day: date, at: datetime, published: dict, scheduled: dict, changed: tuple):
        self.day = day
        self.at = utc(at)
        self.changed = changed
        self._published = published
        self._scheduled = scheduled

    def bar(self, symbol, start):
        start = utc(start)
        arrival = self._scheduled.get((symbol, start))
        if start + INTERVAL > self.at or (arrival is not None and arrival > self.at):
            raise LookAheadError(f"{symbol} {start.isoformat()} is unavailable at {self.at.isoformat()}")
        bar = self._published.get(symbol, {}).get(start)
        return bar if bar is not None and bar.available_at <= self.at else None

    def bars(self, symbol, day=None):
        day = self.day if day is None else day
        if day > self.day:
            raise LookAheadError("Future session requested")
        return tuple(sorted((b for b in self._published.get(symbol, {}).values()
                             if b.day == day and b.available_at <= self.at), key=lambda b: b.start))


def views(session: Session):
    published = defaultdict(dict)
    scheduled = {(b.symbol, b.start): b.available_at for b in session.bars}
    for at, batch in session.arrivals():
        for bar in batch:
            published[bar.symbol][bar.start] = bar
        if not time(9, 30) <= at.astimezone(ET).time() <= time(11, 30):
            continue
        changed = tuple(b for b in batch if b.day == session.day)
        if changed:
            yield View(session.day, at, published, scheduled, changed)


def run(session: Session, rule):
    found = []
    seen = set()
    for view in views(session):
        for signal in rule(view):
            if signal.decided_at != view.at:
                raise LookAheadError("A rule must emit its decision at the replay clock time")
            key = (signal.hypothesis_id, signal.symbol, signal.direction)
            if key not in seen:
                seen.add(key)
                found.append(signal)
    return found


def holding_window(signal, exit_id):
    entry = utc(next_entry_bar(signal.decided_at))
    end = (utc(datetime.combine(signal.day, time(12, 30), ET)) if exit_id == "12:30"
           else entry + INTERVAL * (int(exit_id[:-1]) // 5))
    return entry, end


def outcome(signal, bars, exit_id):
    entry, end = holding_window(signal, exit_id)
    path = []
    cursor = entry
    while cursor < end:
        bar = bars.get(signal.symbol, {}).get(cursor)
        if bar is None:
            return None
        path.append(bar)
        cursor += INTERVAL
    if not path:
        return None
    first = path[0].open
    adverse = min(b.low for b in path) if signal.direction == 1 else max(b.high for b in path)
    return {"gross_bps": signal.direction * (path[-1].close / first - 1) * 1e4,
            "mae_bps": min(0.0, signal.direction * (adverse / first - 1) * 1e4),
            "entry_at": entry.isoformat(), "exit_at": end.isoformat(),
            "settled_at": max(b.available_at for b in path).isoformat()}
