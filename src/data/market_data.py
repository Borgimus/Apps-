"""Market-data domain types, feed metadata, and staleness / completeness checks.

Execution-critical prices and indicators are re-validated from this market-data source
(Alpaca or another explicitly configured feed) — TC2000 and Alpaca feeds are NOT assumed
identical. Every snapshot records its source, feed (IEX/SIP), and the bar timestamp so a
decision can be reconstructed. Missing or stale data fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Sequence


class Feed(str, Enum):
    IEX = "iex"
    SIP = "sip"


class DataError(RuntimeError):
    """Raised when data is missing/insufficient/stale — callers must fail closed."""


@dataclass(frozen=True)
class Bar:
    ts: datetime            # bar timestamp (tz-aware, UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float

    def as_dict(self) -> dict:
        return {"open": self.open, "high": self.high, "low": self.low,
                "close": self.close, "volume": self.volume}


@dataclass(frozen=True)
class Quote:
    ts: datetime
    bid: float
    ask: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        mid = (self.ask + self.bid) / 2.0
        return (self.spread / mid * 100.0) if mid > 0 else float("inf")


@dataclass
class MarketSnapshot:
    symbol: str
    as_of: datetime                 # when the snapshot was taken (UTC)
    feed: Feed
    adjusted: bool
    bars: Sequence[Bar]
    quote: Quote | None = None
    meta: dict = field(default_factory=dict)

    @property
    def latest_bar(self) -> Bar:
        if not self.bars:
            raise DataError(f"{self.symbol}: no bars in snapshot")
        return self.bars[-1]

    @property
    def staleness_seconds(self) -> float:
        return (self.as_of - self.latest_bar.ts).total_seconds()

    def bar_dicts(self) -> list[dict]:
        return [b.as_dict() for b in self.bars]


def ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise DataError("timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def require_min_bars(snapshot: MarketSnapshot, minimum: int) -> None:
    """Reject when fewer than ``minimum`` bars are available (e.g. for a 200-SMA)."""
    if len(snapshot.bars) < minimum:
        raise DataError(
            f"{snapshot.symbol}: need >= {minimum} bars, have {len(snapshot.bars)}"
        )


def require_fresh(snapshot: MarketSnapshot, max_staleness_seconds: float) -> None:
    """Reject when the latest bar is older than the tolerance (fail-closed)."""
    stale = snapshot.staleness_seconds
    if stale < 0:
        raise DataError(f"{snapshot.symbol}: latest bar timestamp is in the future ({stale:.0f}s)")
    if stale > max_staleness_seconds:
        raise DataError(
            f"{snapshot.symbol}: stale data — latest bar {stale:.0f}s old "
            f"> {max_staleness_seconds:.0f}s tolerance"
        )


def detect_bar_gaps(bars: Sequence[Bar], expected_interval: timedelta,
                    tolerance: float = 0.5) -> list[tuple[datetime, datetime]]:
    """Return (prev_ts, next_ts) pairs where the spacing exceeds the expected interval.

    Used to spot missing bars in an intraday series. Daily series should skip non-trading
    days before calling this (calendar-aware), so this is primarily for intraday data.
    """
    gaps: list[tuple[datetime, datetime]] = []
    max_gap = expected_interval * (1 + tolerance)
    for prev, nxt in zip(bars, bars[1:]):
        if (nxt.ts - prev.ts) > max_gap:
            gaps.append((prev.ts, nxt.ts))
    return gaps


def spread_acceptable(quote: Quote, max_spread_pct: float) -> bool:
    return quote.bid > 0 and quote.ask >= quote.bid and quote.spread_pct <= max_spread_pct
