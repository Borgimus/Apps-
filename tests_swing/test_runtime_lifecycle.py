"""Startup reconciliation gating, readiness, and graceful shutdown."""
import pytest

from src.broker.alpaca_paper import LiveEndpointRejected, PAPER_HOST
from src.execution.reconciliation import (
    BrokerOrder,
    BrokerPosition,
    ExpectedPosition,
    reconcile,
)
from src.runtime.app import SwingService, run_startup_reconciliation, verify_paper_endpoint
from src.runtime.lifecycle import Readiness, ShutdownController


def _clean_recon():
    return reconcile(
        broker_positions=[BrokerPosition("AAA", 100, 50.0)],
        broker_orders=[BrokerOrder("stop-1", "AAA", "sell", "stop", 100, "accepted")],
        expected_positions=[ExpectedPosition("AAA", 100)],
        known_client_order_ids={"stop-1"},
    )


def _dirty_recon():
    return reconcile(
        broker_positions=[BrokerPosition("AAA", 100, 50.0)],
        broker_orders=[],  # missing stop -> blocks
        expected_positions=[ExpectedPosition("AAA", 100)],
        known_client_order_ids=set(),
    )


def test_not_ready_before_reconciliation():
    r = Readiness()
    r.paper_endpoint_verified = True
    assert r.is_ready() is False


def test_ready_only_after_clean_reconciliation():
    r = Readiness()
    r.paper_endpoint_verified = True
    run_startup_reconciliation(r, _clean_recon)
    assert r.is_ready() is True


def test_reconciliation_mismatch_blocks_readiness():
    r = Readiness()
    r.paper_endpoint_verified = True
    run_startup_reconciliation(r, _dirty_recon)
    assert r.is_ready() is False
    assert any("MISSING_STOP" in reason for reason in r.reasons)


def test_paper_endpoint_verification_rejects_live():
    r = Readiness()
    with pytest.raises(LiveEndpointRejected):
        verify_paper_endpoint(r, "https://api.alpaca.markets")
    assert r.paper_endpoint_verified is False


def test_service_start_end_to_end():
    svc = SwingService(base_url=f"https://{PAPER_HOST}")
    svc.start(_clean_recon)
    assert svc.ready() is True


def test_graceful_shutdown_drains_but_keeps_managing():
    s = ShutdownController()
    assert s.accepting_new_entries() is True
    s.request_shutdown()
    assert s.accepting_new_entries() is False       # no new entries while draining
    assert s.still_managing_positions() is True      # keeps managing existing positions
    assert s.cancels_protective_stops() is False     # never abandons stops


def test_shutdown_blocks_service_readiness():
    svc = SwingService(base_url=f"https://{PAPER_HOST}")
    svc.start(_clean_recon)
    svc.shutdown.request_shutdown()
    assert svc.ready() is False
