"""Paper-endpoint enforcement and a fake-broker order round trip."""
import pytest

from src.broker.alpaca_paper import (
    AlpacaPaperBroker,
    LiveEndpointRejected,
    PAPER_HOST,
    assert_paper_endpoint,
)
from src.broker.interface import OrderRequest


class FakeClient:
    def __init__(self):
        self.submitted = []

    def get_account(self):
        return {"equity": "100000", "cash": "100000", "buying_power": "200000"}

    def list_positions(self):
        return [{"symbol": "AAA", "qty": "10", "avg_entry_price": "50"}]

    def submit_order(self, **kw):
        self.submitted.append(kw)
        return {"id": "brk-1", "status": "accepted"}

    def cancel_order(self, oid):
        pass

    def replace_order(self, oid, **kw):
        return {"id": oid, "status": "replaced", "client_order_id": "c1"}


def test_assert_paper_endpoint_accepts_paper():
    assert assert_paper_endpoint(f"https://{PAPER_HOST}") == PAPER_HOST


def test_rejects_live_endpoint():
    with pytest.raises(LiveEndpointRejected):
        assert_paper_endpoint("https://api.alpaca.markets")


def test_rejects_non_tls():
    with pytest.raises(LiveEndpointRejected):
        assert_paper_endpoint(f"http://{PAPER_HOST}")


def test_rejects_unknown_host():
    with pytest.raises(LiveEndpointRejected):
        assert_paper_endpoint("https://evil.example.com")


def test_broker_construction_rejects_live():
    with pytest.raises(LiveEndpointRejected):
        AlpacaPaperBroker("https://api.alpaca.markets", "k", "s", FakeClient())


def test_broker_requires_credentials():
    with pytest.raises(LiveEndpointRejected):
        AlpacaPaperBroker(f"https://{PAPER_HOST}", "", "", FakeClient())


def test_order_round_trip_on_paper():
    fc = FakeClient()
    broker = AlpacaPaperBroker(f"https://{PAPER_HOST}", "k", "s", fc)
    acct = broker.get_account()
    assert acct.equity == 100000 and acct.endpoint.endswith(PAPER_HOST)
    ack = broker.submit_order(OrderRequest(
        symbol="AAA", side="buy", qty=10, order_type="stop_limit",
        limit_price=50.5, stop_price=50.0, client_order_id="c-abc"))
    assert ack.broker_order_id == "brk-1"
    assert fc.submitted[0]["client_order_id"] == "c-abc"
