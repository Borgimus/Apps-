"""Market starts and effective availability are timestamps with different roles."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
import gzip
from itertools import groupby
import json
from pathlib import Path
from typing import Iterable, Protocol

from app.research.equity_observer import ET, INTERVAL, PROTOCOL
from app.research.history.bars import normalize_bar, utc


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    start: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def end(self):
        return self.start + INTERVAL

    @property
    def day(self):
        return self.start.astimezone(ET).date()

    @classmethod
    def from_row(cls, symbol, row, available_at):
        bar = normalize_bar(row)
        start = utc(bar.pop("t"))
        available_at = utc(available_at)
        if available_at < start + INTERVAL:
            raise ValueError("A completed bar cannot be available before its close")
        return cls(symbol, start, available_at, **bar)

    def frozen_row(self):
        return {"t": self.start.astimezone(ET).isoformat(), "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "volume": self.volume}


@dataclass
class Session:
    day: date
    bars: tuple[Bar, ...]
    recorded_signals: tuple[dict, ...] = ()
    failure_count: int = 0
    symbols: tuple[str, ...] = ()
    code_sha: str = ""

    def arrivals(self):
        for at, group in groupby(self.bars, key=lambda bar: bar.available_at):
            yield at, tuple(group)

    def current(self):
        return (bar for bar in self.bars if bar.day == self.day)


class BarSource(Protocol):
    def sessions(self) -> Iterable[Session]: ...


class HistoricalSource:
    def __init__(self, panel: dict[str, list[dict]] | Iterable[dict[str, list[dict]]]):
        self.panels = (panel,) if isinstance(panel, dict) else panel

    def sessions(self):
        previous = None
        for panel in self.panels:
            for session in self._sessions(panel):
                if previous is not None and session.day <= previous:
                    raise ValueError("Historical panels must cover ordered, disjoint session dates")
                previous = session.day
                yield session
            del panel

    @staticmethod
    def _sessions(panel):
        from heapq import merge

        def stream(symbol, rows):
            previous = None
            for row in rows:
                start = utc(row["t"])
                if previous is not None and start <= previous:
                    raise ValueError(f"Non-increasing historical bars for {symbol}")
                previous = start
                if time(9, 30) <= start.astimezone(ET).time() < time(12, 30):
                    yield Bar.from_row(symbol, row, start + INTERVAL + timedelta(seconds=60))

        bars = merge(*(stream(s, rows) for s, rows in panel.items()), key=lambda b: b.available_at)
        for day, group in groupby(bars, key=lambda b: b.day):
            yield Session(day, tuple(group), symbols=tuple(panel))


class ArchiveSource:
    def __init__(self, paths: Iterable[Path]):
        self.paths = tuple(paths)

    @staticmethod
    def read(path: Path) -> Session:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as stream:
            events = (json.loads(line) for line in stream if line.strip())
            start = next(events, None)
            if not start or start.get("event") != "session_start":
                raise ValueError(f"Missing archive session start in {path.name}")
            first = utc(start["observed_at"])
            day = first.astimezone(ET).date()
            code = start["code_sha"]
            if start.get("broker_entries_enabled") is not False:
                raise ValueError("Archive is not a disarmed equity research session")
            symbols = tuple(start["symbols"])
            if not symbols or len(set(symbols)) != len(symbols):
                raise ValueError("Invalid archived universe")
            last = first
            bars, signals, failures = [], [], 0
            seen = set()
            if start.get("protocol") != PROTOCOL or start.get("feed") != "iex":
                raise ValueError("Unexpected archive protocol or feed")
            for event in events:
                at = utc(event["observed_at"])
                if (at < last or at.astimezone(ET).date() != day or event.get("code_sha") != code
                        or event.get("protocol") != PROTOCOL or event.get("feed") != "iex"):
                    raise ValueError("Invalid archived event ordering or provenance")
                last = at
                kind = event["event"]
                if kind == "completed_bar":
                    symbol = event["symbol"]
                    bar = Bar.from_row(symbol, event["bar"], at)
                    if symbol not in symbols or (symbol, bar.start) in seen:
                        raise ValueError("Unexpected symbol or revised archive bar")
                    seen.add((symbol, bar.start))
                    bars.append(bar)
                elif kind == "signal":
                    signal = event["signal"]
                    if utc(signal["observed_at"]) != at or signal["symbol"] not in symbols:
                        raise ValueError("Recorded signal differs from its archive event")
                    signals.append(signal)
                elif kind in {"data_error", "signal_error"}:
                    failures += 1
                else:
                    raise ValueError(f"Unknown archived event kind {kind}")
        return Session(day, tuple(bars), tuple(signals), failures, symbols, code)

    def sessions(self):
        previous = None
        for path in sorted(self.paths, key=lambda p: p.name):
            session = self.read(path)
            if previous is not None and session.day <= previous:
                raise ValueError("Archive sessions must be ordered and unique")
            previous = session.day
            yield session


def index_current(session):
    index = defaultdict(dict)
    for bar in session.current():
        index[bar.symbol][bar.start] = bar
    return index
