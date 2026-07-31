"""Idempotent client order IDs and order builders."""
import pytest

from src.execution.orders import (
    build_entry_order,
    build_final_exit,
    build_partial_exit,
    build_protective_stop,
    make_client_order_id,
)


def test_client_order_id_deterministic():
    a = make_client_order_id("T1", "entry", "AAA", 100.0, 100.5, 200)
    b = make_client_order_id("T1", "entry", "AAA", 100.0, 100.5, 200)
    assert a == b  # retries produce the same id -> no duplicate positions


def test_client_order_id_changes_with_inputs():
    a = make_client_order_id("T1", "entry", "AAA", 100.0, 100.5, 200)
    b = make_client_order_id("T1", "entry", "AAA", 100.0, 100.5, 201)
    assert a != b


def test_entry_is_stop_limit_never_market():
    o = build_entry_order(trade_id="T1", symbol="AAA", qty=200,
                          breakout_level=100.0, limit_price=100.5)
    assert o.order_type == "stop_limit"
    assert o.stop_price == 100.0 and o.limit_price == 100.5
    assert o.side == "buy"


def test_entry_limit_must_cover_level():
    with pytest.raises(ValueError):
        build_entry_order(trade_id="T1", symbol="AAA", qty=10,
                          breakout_level=100.0, limit_price=99.0)


def test_protective_stop_builder():
    o = build_protective_stop(trade_id="T1", symbol="AAA", qty=200, stop_price=95.0)
    assert o.side == "sell" and o.order_type == "stop" and o.stop_price == 95.0


def test_partial_and_final_builders():
    p = build_partial_exit(trade_id="T1", symbol="AAA", qty=50, limit_price=125.0)
    assert p.qty == 50 and p.side == "sell"
    f = build_final_exit(trade_id="T1", symbol="AAA", qty=150, session_date="2026-07-31")
    assert f.time_in_force == "opg"  # market-on-open for the next session
    # Distinct session -> distinct idempotency key.
    f2 = build_final_exit(trade_id="T1", symbol="AAA", qty=150, session_date="2026-08-01")
    assert f.client_order_id != f2.client_order_id
