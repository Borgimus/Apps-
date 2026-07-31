"""Market-data staleness, completeness, feed metadata, spread."""
from datetime import datetime, timedelta, timezone

import pytest

from src.data.market_data import (
    Bar,
    DataError,
    Feed,
    MarketSnapshot,
    Quote,
    detect_bar_gaps,
    require_fresh,
    require_min_bars,
    spread_acceptable,
)

UTC = timezone.utc


def mk_bar(ts, c=100.0):
    return Bar(ts=ts, open=c, high=c + 1, low=c - 1, close=c, volume=1000)


def snapshot(as_of, last_ts, n=5, feed=Feed.IEX):
    bars = [mk_bar(last_ts - timedelta(minutes=(n - 1 - i))) for i in range(n)]
    return MarketSnapshot(symbol="AAA", as_of=as_of, feed=feed, adjusted=True, bars=bars)


def test_staleness_seconds_and_fresh_ok():
    now = datetime(2026, 7, 31, 16, 0, tzinfo=UTC)
    snap = snapshot(now, now - timedelta(seconds=30))
    assert snap.staleness_seconds == pytest.approx(30.0)
    require_fresh(snap, 120)  # no raise


def test_stale_data_rejected():
    now = datetime(2026, 7, 31, 16, 0, tzinfo=UTC)
    snap = snapshot(now, now - timedelta(seconds=300))
    with pytest.raises(DataError):
        require_fresh(snap, 120)


def test_future_bar_rejected():
    now = datetime(2026, 7, 31, 16, 0, tzinfo=UTC)
    snap = snapshot(now, now + timedelta(seconds=10))
    with pytest.raises(DataError):
        require_fresh(snap, 120)


def test_require_min_bars():
    now = datetime(2026, 7, 31, 16, 0, tzinfo=UTC)
    snap = snapshot(now, now, n=3)
    with pytest.raises(DataError):
        require_min_bars(snap, 200)
    require_min_bars(snap, 3)  # exactly enough, no raise


def test_feed_metadata_recorded():
    now = datetime(2026, 7, 31, 16, 0, tzinfo=UTC)
    snap = snapshot(now, now, feed=Feed.SIP)
    assert snap.feed is Feed.SIP  # IEX vs SIP is recorded, never assumed


def test_detect_bar_gaps():
    base = datetime(2026, 7, 31, 14, 0, tzinfo=UTC)
    bars = [mk_bar(base), mk_bar(base + timedelta(minutes=1)),
            mk_bar(base + timedelta(minutes=10))]  # 9-min gap
    gaps = detect_bar_gaps(bars, timedelta(minutes=1))
    assert len(gaps) == 1


def test_spread_acceptable():
    q = Quote(ts=datetime(2026, 7, 31, 14, 0, tzinfo=UTC), bid=100.0, ask=100.2)
    assert spread_acceptable(q, max_spread_pct=0.5) is True
    wide = Quote(ts=q.ts, bid=100.0, ask=105.0)
    assert spread_acceptable(wide, max_spread_pct=0.5) is False
