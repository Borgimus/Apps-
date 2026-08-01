"""Durable trade-state store: persistence, reconstruction, 5R-once, reconciliation inputs."""
from src.execution.reconciliation import BrokerPosition
from src.live.trade_state import TradeStateStore


def store(tmp_path):
    return TradeStateStore(tmp_path / "state.json")


def test_record_entry_and_reconstruct(tmp_path):
    s = store(tmp_path)
    s.record_entry(trade_id="AAA-v1", symbol="AAA", shares=100, entry_price=101.5,
                   initial_stop=95.0, entry_coid="entry-1", stop_coid="stop-1")
    # Broker reports the position -> reconstructed LivePosition overlays stored metadata.
    positions = s.reconstruct_positions([BrokerPosition("AAA", 100, 101.5)])
    assert len(positions) == 1
    p = positions[0]
    assert p.trade_id == "AAA-v1" and p.initial_stop == 95.0 and p.has_stop and not p.partial_done


def test_broker_qty_wins_in_reconstruction(tmp_path):
    s = store(tmp_path)
    s.record_entry(trade_id="AAA-v1", symbol="AAA", shares=100, entry_price=101.5,
                   initial_stop=95.0, entry_coid="e", stop_coid="s")
    # Broker shows 60 (e.g. a partial already filled) -> reconstruction uses 60.
    positions = s.reconstruct_positions([BrokerPosition("AAA", 60, 101.5)])
    assert positions[0].open_shares == 60


def test_position_not_at_broker_is_not_fabricated(tmp_path):
    s = store(tmp_path)
    s.record_entry(trade_id="AAA-v1", symbol="AAA", shares=100, entry_price=101.5,
                   initial_stop=95.0, entry_coid="e", stop_coid="s")
    assert s.reconstruct_positions([]) == []   # broker has no AAA -> skipped, never invented


def test_five_r_partial_is_once(tmp_path):
    s = store(tmp_path)
    s.record_entry(trade_id="AAA-v1", symbol="AAA", shares=100, entry_price=100.0,
                   initial_stop=95.0, entry_coid="e", stop_coid="s")
    s.on_partial("AAA-v1", sold_shares=25, new_stop_price=100.0, partial_coid="p1", stop_coid="s2")
    t = s.get("AAA-v1")
    assert t.partial_done and t.open_shares == 75 and t.stop_price == 100.0
    # A second call is a no-op (idempotent 5R-once).
    s.on_partial("AAA-v1", sold_shares=25, new_stop_price=100.0, partial_coid="p2", stop_coid="s3")
    assert s.get("AAA-v1").open_shares == 75


def test_persistence_survives_reload(tmp_path):
    s = store(tmp_path)
    s.record_entry(trade_id="AAA-v1", symbol="AAA", shares=100, entry_price=100.0,
                   initial_stop=95.0, entry_coid="e", stop_coid="s")
    s.on_partial("AAA-v1", sold_shares=25, new_stop_price=100.0, partial_coid="p1", stop_coid=None)
    # New store instance reading the same file -> partial_done persists (no double 5R after restart).
    s2 = TradeStateStore(tmp_path / "state.json")
    reloaded = s2.reconstruct_positions([BrokerPosition("AAA", 75, 100.0)])
    assert reloaded[0].partial_done is True


def test_known_client_order_ids_and_expected_positions(tmp_path):
    s = store(tmp_path)
    s.record_entry(trade_id="AAA-v1", symbol="AAA", shares=100, entry_price=100.0,
                   initial_stop=95.0, entry_coid="entry-1", stop_coid="stop-1")
    assert s.known_client_order_ids() == {"entry-1", "stop-1"}
    exp = s.expected_positions()
    assert exp[0].symbol == "AAA" and exp[0].qty == 100


def test_sync_and_close(tmp_path):
    s = store(tmp_path)
    s.record_entry(trade_id="AAA-v1", symbol="AAA", shares=100, entry_price=100.0,
                   initial_stop=95.0, entry_coid="e", stop_coid="s")
    s.sync_open_shares("AAA-v1", 0)   # broker shows flat -> trade closed
    assert s.get("AAA-v1").closed and s.open_trades() == []


def test_update_high(tmp_path):
    s = store(tmp_path)
    s.record_entry(trade_id="AAA-v1", symbol="AAA", shares=100, entry_price=100.0,
                   initial_stop=95.0, entry_coid="e", stop_coid="s")
    s.update_high("AAA-v1", 130.0)
    s.update_high("AAA-v1", 120.0)   # lower -> ignored
    assert s.get("AAA-v1").high_since_entry == 130.0
