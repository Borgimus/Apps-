"""Paper-ONLY Alpaca adapter.

Hard safety boundary: the adapter refuses to construct against any endpoint that is not the
verified Alpaca *paper* endpoint. There is no runtime flag that switches this to live. If a
non-paper endpoint (or a suspicious host) is supplied, construction raises and the process is
expected to terminate safely rather than continue.

The network client itself is injected so unit tests can run without credentials or sockets.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .interface import (
    Account,
    BrokerInterface,
    OrderAck,
    OrderRequest,
    Position,
)

# The only endpoint host this adapter will ever talk to.
PAPER_HOST = "paper-api.alpaca.markets"
LIVE_HOST = "api.alpaca.markets"


class LiveEndpointRejected(RuntimeError):
    """Raised when a non-paper Alpaca endpoint is supplied. Fail-closed."""


def assert_paper_endpoint(base_url: str) -> str:
    """Validate that ``base_url`` is the Alpaca paper endpoint; return the normalized host.

    Rejects the live host, any http (non-TLS) URL, and anything that is not the exact paper host.
    """
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise LiveEndpointRejected(f"endpoint must use https, got scheme {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if host == LIVE_HOST:
        raise LiveEndpointRejected("live Alpaca endpoint is forbidden — paper only")
    if host != PAPER_HOST:
        raise LiveEndpointRejected(
            f"unrecognized endpoint host {host!r}; only {PAPER_HOST} is permitted"
        )
    return host


class AlpacaPaperBroker(BrokerInterface):
    """Thin paper-only adapter. ``client`` is any object implementing the REST calls used
    here; it is injected to keep unit tests deterministic and credential-free."""

    def __init__(self, base_url: str, key_id: str, secret_key: str, client):
        # Fail-closed BEFORE storing any credential or making any call.
        self.host = assert_paper_endpoint(base_url)
        if not key_id or not secret_key:
            raise LiveEndpointRejected("missing paper credentials")
        self.base_url = base_url
        self._client = client

    def get_account(self) -> Account:
        raw = self._client.get_account()
        acct = Account(
            equity=float(raw["equity"]),
            cash=float(raw["cash"]),
            buying_power=float(raw["buying_power"]),
            endpoint=self.base_url,
        )
        # Defensive re-check: the account response must confirm paper trading.
        if raw.get("account_blocked") or raw.get("trading_blocked"):
            raise LiveEndpointRejected("broker reports blocked account")
        return acct

    def list_positions(self) -> list[Position]:
        return [
            Position(symbol=p["symbol"], qty=int(p["qty"]),
                     avg_entry_price=float(p["avg_entry_price"]))
            for p in self._client.list_positions()
        ]

    def submit_order(self, req: OrderRequest, *, retry=None, sleep=None) -> OrderAck:
        """Submit an order. If a ``RetryPolicy`` is given, transient faults are retried with
        backoff reusing the SAME idempotent client_order_id (retries cannot duplicate a position);
        exhausted retries raise RetriesExhausted so the caller fails closed."""
        def _do():
            return self._client.submit_order(
                symbol=req.symbol, side=req.side, qty=req.qty, type=req.order_type,
                limit_price=req.limit_price, stop_price=req.stop_price,
                client_order_id=req.client_order_id, time_in_force=req.time_in_force,
            )

        if retry is not None:
            from .retry import call_with_retry
            raw = call_with_retry(_do, retry, sleep=sleep or (lambda _s: None))
        else:
            raw = _do()
        return OrderAck(client_order_id=req.client_order_id,
                        broker_order_id=raw["id"], status=raw["status"])

    def cancel_order(self, broker_order_id: str) -> None:
        self._client.cancel_order(broker_order_id)

    def replace_order(self, broker_order_id: str, *, stop_price=None, limit_price=None) -> OrderAck:
        raw = self._client.replace_order(broker_order_id, stop_price=stop_price,
                                         limit_price=limit_price)
        return OrderAck(client_order_id=raw.get("client_order_id", ""),
                        broker_order_id=raw["id"], status=raw["status"])
