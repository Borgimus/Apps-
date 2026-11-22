"""Per-session scratch storage for multiple passes of the frozen replay."""
from datetime import date
import gzip
import json
from pathlib import Path

from app.research.history.bars import utc
from .sources import Bar, Session


class SessionSpool:
    """Stores only current-date research inputs in a private temporary directory."""

    def __init__(self, directory: Path):
        self.directory = directory

    def put(self, session, signals):
        payload = {
            "symbols": list(session.symbols),
            "frozen": list(session.recorded_signals),
            "bars": [[b.symbol, b.start.isoformat(), b.available_at.isoformat(),
                      b.open, b.high, b.low, b.close, b.volume] for b in session.current()],
            "signals": [[s.hypothesis_id, s.symbol, s.decided_at.isoformat(), s.direction] for s in signals],
        }
        with gzip.open(self._path(session.day), "wt", compresslevel=1, encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), allow_nan=False)

    def _path(self, day: date):
        return self.directory / f"{day.isoformat()}.json.gz"

    def get(self, day: date):
        from .engine import HypothesisSignal

        with gzip.open(self._path(day), "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        bars = tuple(Bar(symbol, utc(start), utc(available), *ohlcv)
                     for symbol, start, available, *ohlcv in payload["bars"])
        session = Session(day, bars, recorded_signals=tuple(payload["frozen"]),
                          symbols=tuple(payload["symbols"]))
        signals = tuple(HypothesisSignal(h, symbol, utc(at), direction)
                        for h, symbol, at, direction in payload["signals"])
        return session, signals
