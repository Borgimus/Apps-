"""Reconciliation: broker wins; missing stops, mismatches, and unknown orders block risk."""
from src.execution.reconciliation import (
    BrokerOrder,
    BrokerPosition,
    ExpectedPosition,
    IncidentKind,
    reconcile,
)


def stop_order(symbol, qty, coid="stop-1", status="accepted"):
    return BrokerOrder(client_order_id=coid, symbol=symbol, side="sell",
                       order_type="stop", qty=qty, status=status)


def test_clean_reconciliation_with_stop():
    res = reconcile(
        broker_positions=[BrokerPosition("AAA", 100, 50.0)],
        broker_orders=[stop_order("AAA", 100)],
        expected_positions=[ExpectedPosition("AAA", 100)],
        known_client_order_ids={"stop-1"},
    )
    assert res.ok and not res.blocks_new_risk


def test_missing_stop_blocks_risk():
    res = reconcile(
        broker_positions=[BrokerPosition("AAA", 100, 50.0)],
        broker_orders=[],  # no protective stop
        expected_positions=[ExpectedPosition("AAA", 100)],
        known_client_order_ids=set(),
    )
    kinds = {i.kind for i in res.incidents}
    assert IncidentKind.MISSING_STOP in kinds and res.blocks_new_risk


def test_partial_stop_coverage_is_missing_stop():
    res = reconcile(
        broker_positions=[BrokerPosition("AAA", 100, 50.0)],
        broker_orders=[stop_order("AAA", 40)],  # only covers 40 of 100
        expected_positions=[ExpectedPosition("AAA", 100)],
        known_client_order_ids={"stop-1"},
    )
    assert any(i.kind is IncidentKind.MISSING_STOP for i in res.incidents)


def test_quantity_mismatch_broker_wins():
    res = reconcile(
        broker_positions=[BrokerPosition("AAA", 80, 50.0)],
        broker_orders=[stop_order("AAA", 80)],
        expected_positions=[ExpectedPosition("AAA", 100)],
        known_client_order_ids={"stop-1"},
    )
    assert any(i.kind is IncidentKind.POSITION_MISMATCH for i in res.incidents)


def test_unknown_position_flagged():
    res = reconcile(
        broker_positions=[BrokerPosition("ZZZ", 10, 5.0)],
        broker_orders=[stop_order("ZZZ", 10)],
        expected_positions=[],
        known_client_order_ids={"stop-1"},
    )
    assert any(i.kind is IncidentKind.UNKNOWN_POSITION for i in res.incidents)


def test_missing_position_flagged():
    res = reconcile(
        broker_positions=[],
        broker_orders=[],
        expected_positions=[ExpectedPosition("AAA", 100)],
        known_client_order_ids=set(),
    )
    assert any(i.kind is IncidentKind.MISSING_POSITION for i in res.incidents)


def test_unknown_open_order_flagged():
    res = reconcile(
        broker_positions=[BrokerPosition("AAA", 100, 50.0)],
        broker_orders=[stop_order("AAA", 100, coid="rogue-1")],
        expected_positions=[ExpectedPosition("AAA", 100)],
        known_client_order_ids=set(),  # rogue-1 not originated here
    )
    assert any(i.kind is IncidentKind.UNKNOWN_ORDER for i in res.incidents)
