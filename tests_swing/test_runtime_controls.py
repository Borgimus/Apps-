"""Fail-closed entry gate and paper-only emergency stop."""
from src.runtime.controls import (
    EntryContext,
    can_manage_existing_positions,
    can_open_new_entry,
)


def _all_clear(**over):
    base = dict(
        paper_endpoint_verified=True, data_fresh=True, broker_reconciled=True,
        tc2000_batch_fresh=True, db_ok=True, all_positions_have_stops=True,
        risk_limit_ok=True, buying_power_ok=True, regular_session=True, clock_ok=True,
        no_duplicate_intent=True, emergency_stop=False,
    )
    base.update(over)
    return EntryContext(**base)


def test_all_clear_allows_entry():
    assert can_open_new_entry(_all_clear()).allowed is True


def test_each_condition_blocks_entry():
    cases = {
        "endpoint_not_verified_paper": dict(paper_endpoint_verified=False),
        "market_data_stale_or_unavailable": dict(data_fresh=False),
        "broker_state_unreconciled": dict(broker_reconciled=False),
        "tc2000_batch_stale_or_invalid": dict(tc2000_batch_fresh=False),
        "database_persistence_failed": dict(db_ok=False),
        "existing_position_missing_stop": dict(all_positions_have_stops=False),
        "risk_limit_reached": dict(risk_limit_ok=False),
        "insufficient_buying_power": dict(buying_power_ok=False),
        "market_closed_or_unsupported_session": dict(regular_session=False),
        "clock_drift_exceeds_tolerance": dict(clock_ok=False),
        "duplicate_order_intent": dict(no_duplicate_intent=False),
    }
    for expected_reason, override in cases.items():
        decision = can_open_new_entry(_all_clear(**override))
        assert decision.allowed is False
        assert expected_reason in decision.blockers


def test_emergency_stop_blocks_new_entries_only():
    decision = can_open_new_entry(_all_clear(emergency_stop=True))
    assert decision.allowed is False and "emergency_stop_active" in decision.blockers
    # Managing/closing existing positions remains permitted.
    assert can_manage_existing_positions() is True


def test_multiple_blockers_reported_together():
    decision = can_open_new_entry(_all_clear(data_fresh=False, clock_ok=False))
    assert {"market_data_stale_or_unavailable", "clock_drift_exceeds_tolerance"} <= set(decision.blockers)
