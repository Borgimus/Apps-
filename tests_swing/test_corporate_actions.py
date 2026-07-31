"""Corporate-action detection: splits, symbol changes, delistings, halts, stale."""
from datetime import datetime, timedelta, timezone

import pytest

from src.data.corporate_actions import (
    ActionKind,
    detect_delisting,
    detect_halt,
    detect_split_from_adjustment,
    detect_stale,
    detect_symbol_change,
    suspected_split,
)

UTC = timezone.utc


def test_forward_split_suspected():
    ev = suspected_split(prev_close=200.0, today_open=100.0)  # 2:1
    assert ev and ev.kind is ActionKind.SPLIT and ev.factor == 2.0


def test_reverse_split_suspected():
    ev = suspected_split(prev_close=1.0, today_open=10.0)  # 1:10 reverse
    assert ev and ev.factor == pytest.approx(0.1)


def test_ordinary_gap_not_a_split():
    assert suspected_split(prev_close=100.0, today_open=97.0) is None


def test_split_from_adjustment_disagreement():
    # raw halves (0.5) while adjusted ~flat (1.0) -> factor ~0.5 detected
    ev = detect_split_from_adjustment(raw_ratio=0.5, adj_ratio=1.0)
    assert ev and ev.kind is ActionKind.SPLIT


def test_symbol_change():
    assert detect_symbol_change("FB", "META").kind is ActionKind.SYMBOL_CHANGE
    assert detect_symbol_change("AAA", "AAA") is None


def test_delisting():
    assert detect_delisting(has_recent_bars=False, tradable=True, symbol="X").kind is ActionKind.DELISTING
    assert detect_delisting(has_recent_bars=True, tradable=False, symbol="X").kind is ActionKind.DELISTING
    assert detect_delisting(has_recent_bars=True, tradable=True, symbol="X") is None


def test_halt():
    assert detect_halt(halted_flag=True, symbol="X").kind is ActionKind.HALT
    assert detect_halt(halted_flag=False, symbol="X") is None


def test_stale():
    now = datetime(2026, 7, 31, 16, 0, tzinfo=UTC)
    old = now - timedelta(days=3)
    assert detect_stale(old, now, timedelta(days=1), "X").kind is ActionKind.STALE
    assert detect_stale(now, now, timedelta(days=1), "X") is None
