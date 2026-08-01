"""Alpaca paper REST + data clients: request building via an injected sender (no network)."""
import pytest

from src.broker.alpaca_client import AlpacaPaperRESTClient
from src.broker.alpaca_paper import LiveEndpointRejected, PAPER_HOST
from src.data.alpaca_data import AlpacaDataClient

PAPER = f"https://{PAPER_HOST}"


def test_rest_client_rejects_live_endpoint():
    with pytest.raises(LiveEndpointRejected):
        AlpacaPaperRESTClient("https://api.alpaca.markets", "k", "s", send=lambda *a: (200, {}))


def test_rest_submit_order_builds_expected_payload():
    calls = []

    def send(method, url, headers, json_body, params):
        calls.append((method, url, json_body))
        return 200, {"id": "brk-1", "status": "accepted"}

    c = AlpacaPaperRESTClient(PAPER, "key", "secret", send=send)
    out = c.submit_order(symbol="AAA", side="buy", qty=200, type="stop_limit",
                         limit_price=100.5, stop_price=100.0, client_order_id="entry-1",
                         time_in_force="day")
    method, url, body = calls[-1]
    assert method == "POST" and url.endswith("/v2/orders")
    assert body["client_order_id"] == "entry-1" and body["qty"] == "200"
    assert body["stop_price"] == "100.0" and body["limit_price"] == "100.5"
    assert out["id"] == "brk-1"


def test_rest_error_does_not_leak_credentials():
    def send(method, url, headers, json_body, params):
        return 403, {"message": "forbidden"}

    c = AlpacaPaperRESTClient(PAPER, "key", "supersecret", send=send)
    with pytest.raises(RuntimeError) as ei:
        c.get_account()
    assert "supersecret" not in str(ei.value)


def test_data_client_parses_daily_bars():
    def send(method, url, headers, params):
        return 200, {"bars": [
            {"t": "2026-07-30T04:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1000},
            {"t": "2026-07-31T04:00:00Z", "o": 10.5, "h": 12, "l": 10, "c": 11.5, "v": 2000},
        ]}

    d = AlpacaDataClient("k", "s", feed="iex", send=send)
    snap = d.get_daily_snapshot("AAA", limit=2)
    assert len(snap.bars) == 2 and snap.feed.value == "iex"
    assert snap.latest_bar.close == 11.5


def test_data_client_last_trade_price():
    d = AlpacaDataClient("k", "s", send=lambda m, u, h, p: (200, {"trade": {"p": 42.5}}))
    assert d.get_last_trade_price("AAA") == 42.5
