"""US equities (NYSE/Nasdaq) trading calendar and session classification.

Pure stdlib (``datetime`` + ``zoneinfo``). Handles weekends, fixed and floating holidays,
holiday observance (Sat→Fri, Sun→Mon), early closes (1:00 p.m. ET), DST via the
America/New_York zone, and a clock-drift check. All decisions use America/New_York.

This is authoritative for "is the regular session open right now?" — the strategy never
enters outside the regular session in v1.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
PREMARKET_OPEN = time(4, 0)
POSTMARKET_CLOSE = time(20, 0)


class SessionState(str, Enum):
    CLOSED = "CLOSED"
    PRE_MARKET = "PRE_MARKET"
    REGULAR = "REGULAR"
    POST_MARKET = "POST_MARKET"


# ---------------------------------------------------------------------------
# Holiday computation
# ---------------------------------------------------------------------------
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Mon=0) of ``month``/``year`` (n>=1)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` of ``month``/``year``."""
    if month == 12:
        d = date(year, 12, 31)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _easter(year: int) -> date:
    """Anonymous Gregorian computus — used only for Good Friday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """NYSE/federal observance: Saturday→Friday, Sunday→Monday."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def holidays(year: int) -> set[date]:
    """Full-closure NYSE holidays for ``year`` (observed dates)."""
    hs = {
        _observed(date(year, 1, 1)),                 # New Year's Day
        _nth_weekday(year, 1, 0, 3),                 # MLK — 3rd Monday Jan
        _nth_weekday(year, 2, 0, 3),                 # Washington's Birthday — 3rd Monday Feb
        _easter(year) - timedelta(days=2),           # Good Friday
        _last_weekday(year, 5, 0),                   # Memorial Day — last Monday May
        _observed(date(year, 6, 19)),                # Juneteenth (2022+)
        _observed(date(year, 7, 4)),                 # Independence Day
        _nth_weekday(year, 9, 0, 1),                 # Labor Day — 1st Monday Sep
        _nth_weekday(year, 11, 3, 4),                # Thanksgiving — 4th Thursday Nov
        _observed(date(year, 12, 25)),               # Christmas
    }
    if year < 2022:
        hs.discard(_observed(date(year, 6, 19)))
    return hs


def early_closes(year: int) -> set[date]:
    """Days with a 1:00 p.m. ET early close (day after Thanksgiving, Christmas Eve,
    and July 3 when it is itself a normal trading day)."""
    out: set[date] = set()
    day_after_tg = _nth_weekday(year, 11, 3, 4) + timedelta(days=1)
    out.add(day_after_tg)

    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() < 5 and xmas_eve not in holidays(year):
        out.add(xmas_eve)

    jul3 = date(year, 7, 3)
    if jul3.weekday() < 5 and jul3 not in holidays(year):
        out.add(jul3)

    # An early-close day that is actually a full holiday is not an early close.
    return {d for d in out if d not in holidays(year) and d.weekday() < 5}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def regular_close_time(d: date) -> time:
    return EARLY_CLOSE if d in early_closes(d.year) else REGULAR_CLOSE


# ---------------------------------------------------------------------------
# Session classification
# ---------------------------------------------------------------------------
def _to_et(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(ET)


def session_state(dt: datetime) -> SessionState:
    """Classify the session at ``dt`` (any tz-aware datetime), in ET."""
    et = _to_et(dt)
    d = et.date()
    if not is_trading_day(d):
        return SessionState.CLOSED
    t = et.timetz().replace(tzinfo=None)
    close = regular_close_time(d)
    if t < PREMARKET_OPEN or t >= POSTMARKET_CLOSE:
        return SessionState.CLOSED
    if t < REGULAR_OPEN:
        return SessionState.PRE_MARKET
    if t < close:
        return SessionState.REGULAR
    return SessionState.POST_MARKET


def is_regular_session(dt: datetime) -> bool:
    return session_state(dt) is SessionState.REGULAR


def next_regular_open(dt: datetime) -> datetime:
    """The next regular-session open at/after ``dt`` (ET, tz-aware)."""
    et = _to_et(dt)
    d = et.date()
    # If today is a trading day and we're before the open, use today.
    if is_trading_day(d) and et.timetz().replace(tzinfo=None) < REGULAR_OPEN:
        return datetime.combine(d, REGULAR_OPEN, tzinfo=ET)
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return datetime.combine(d, REGULAR_OPEN, tzinfo=ET)


# ---------------------------------------------------------------------------
# Clock drift
# ---------------------------------------------------------------------------
@dataclass
class DriftCheck:
    within_tolerance: bool
    drift_seconds: float
    tolerance_seconds: float


def clock_drift(local_now: datetime, trusted_now: datetime, tolerance_seconds: float) -> DriftCheck:
    """Compare a local clock to a trusted time source. Fail-closed if drift exceeds tolerance."""
    drift = abs((local_now - trusted_now).total_seconds())
    return DriftCheck(drift <= tolerance_seconds, drift, tolerance_seconds)
