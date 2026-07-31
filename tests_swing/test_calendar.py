"""US equities calendar: holidays, early closes, sessions, DST, clock drift."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.data.calendar import (
    SessionState,
    clock_drift,
    early_closes,
    holidays,
    is_regular_session,
    is_trading_day,
    next_regular_open,
    regular_close_time,
    session_state,
)

ET = ZoneInfo("America/New_York")


def test_known_2026_holidays():
    h = holidays(2026)
    assert date(2026, 1, 1) in h            # New Year's (Thu)
    assert date(2026, 1, 19) in h           # MLK
    assert date(2026, 2, 16) in h           # Presidents
    assert date(2026, 4, 3) in h            # Good Friday (Easter Apr 5)
    assert date(2026, 5, 25) in h           # Memorial
    assert date(2026, 6, 19) in h           # Juneteenth
    assert date(2026, 7, 3) in h            # Independence observed (Jul 4 is Sat)
    assert date(2026, 9, 7) in h            # Labor
    assert date(2026, 11, 26) in h          # Thanksgiving
    assert date(2026, 12, 25) in h          # Christmas


def test_juneteenth_not_before_2022():
    assert date(2021, 6, 18) not in holidays(2021)
    assert date(2022, 6, 20) in holidays(2022)  # Jun 19 2022 is Sunday -> observed Mon 20


def test_early_closes_2026():
    ec = early_closes(2026)
    assert date(2026, 11, 27) in ec         # day after Thanksgiving
    assert date(2026, 12, 24) in ec         # Christmas Eve (Thu)
    assert date(2026, 7, 3) not in ec       # that day is a full holiday this year


def test_is_trading_day():
    assert is_trading_day(date(2026, 7, 31)) is True   # Friday, normal
    assert is_trading_day(date(2026, 8, 1)) is False   # Saturday
    assert is_trading_day(date(2026, 12, 25)) is False  # Christmas


def test_regular_session_dst_summer_and_winter():
    # 10:00 ET is regular in both EDT (summer) and EST (winter).
    assert is_regular_session(datetime(2026, 7, 31, 10, 0, tzinfo=ET))
    assert is_regular_session(datetime(2026, 1, 5, 10, 0, tzinfo=ET))


def test_session_boundaries():
    d = date(2026, 7, 31)
    assert session_state(datetime(2026, 7, 31, 9, 0, tzinfo=ET)) is SessionState.PRE_MARKET
    assert session_state(datetime(2026, 7, 31, 9, 30, tzinfo=ET)) is SessionState.REGULAR
    assert session_state(datetime(2026, 7, 31, 15, 59, tzinfo=ET)) is SessionState.REGULAR
    assert session_state(datetime(2026, 7, 31, 16, 0, tzinfo=ET)) is SessionState.POST_MARKET
    assert session_state(datetime(2026, 8, 1, 12, 0, tzinfo=ET)) is SessionState.CLOSED
    assert regular_close_time(d) == regular_close_time(date(2026, 7, 31))


def test_early_close_shortens_regular_session():
    # 14:00 ET on an early-close day is POST_MARKET (regular closed at 13:00).
    assert session_state(datetime(2026, 11, 27, 14, 0, tzinfo=ET)) is SessionState.POST_MARKET
    assert session_state(datetime(2026, 11, 27, 12, 0, tzinfo=ET)) is SessionState.REGULAR


def test_next_regular_open_skips_weekend_and_holiday():
    # Friday after close -> next open is Monday.
    nxt = next_regular_open(datetime(2026, 7, 31, 17, 0, tzinfo=ET))
    assert nxt.date() == date(2026, 8, 3) and nxt.hour == 9 and nxt.minute == 30
    # Christmas Eve early close -> Christmas is closed -> next open Dec 28 (Mon).
    nxt2 = next_regular_open(datetime(2026, 12, 24, 14, 0, tzinfo=ET))
    assert nxt2.date() == date(2026, 12, 28)


def test_clock_drift():
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=ET)
    assert clock_drift(now, now + timedelta(seconds=1), 2.0).within_tolerance is True
    assert clock_drift(now, now + timedelta(seconds=5), 2.0).within_tolerance is False
