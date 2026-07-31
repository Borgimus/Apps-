"""Retry/backoff classification, Retry-After honoring, and fail-closed exhaustion.

Also proves an order submission that hits transient 503s is retried with the SAME
idempotent client_order_id and eventually succeeds (no duplicate positions)."""
import pytest

from src.broker.alpaca_paper import AlpacaPaperBroker, PAPER_HOST
from src.broker.interface import OrderRequest
from src.broker.retry import (
    BrokerConnectionError,
    BrokerHTTPError,
    BrokerTimeout,
    RetriesExhausted,
    RetryPolicy,
    call_with_retry,
    is_retryable,
)


def test_classification():
    assert is_retryable(BrokerTimeout())[0] is True
    assert is_retryable(BrokerConnectionError())[0] is True
    assert is_retryable(BrokerHTTPError(429))[0] is True
    assert is_retryable(BrokerHTTPError(503))[0] is True
    assert is_retryable(BrokerHTTPError(400))[0] is False   # client error, permanent
    assert is_retryable(ValueError("x"))[0] is False


def test_backoff_caps_and_honors_retry_after():
    p = RetryPolicy(base_delay=2.0, multiplier=2.0, max_delay=10.0)
    assert p.backoff_seconds(1) == 2.0
    assert p.backoff_seconds(2) == 4.0
    assert p.backoff_seconds(10) == 10.0                    # capped
    assert p.backoff_seconds(1, retry_after=7.5) == 7.5     # hint wins when larger


def test_non_retryable_reraises_immediately():
    calls = []

    def fn():
        calls.append(1)
        raise BrokerHTTPError(422, "unprocessable")

    with pytest.raises(BrokerHTTPError):
        call_with_retry(fn, RetryPolicy(max_attempts=5), sleep=lambda _s: None)
    assert len(calls) == 1  # not retried


def test_retryable_then_success():
    slept = []
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] < 3:
            raise BrokerHTTPError(503)
        return "ok"

    out = call_with_retry(fn, RetryPolicy(max_attempts=5), sleep=slept.append)
    assert out == "ok" and len(slept) == 2  # two backoffs before success


def test_exhaustion_fails_closed():
    def fn():
        raise BrokerTimeout()

    with pytest.raises(RetriesExhausted):
        call_with_retry(fn, RetryPolicy(max_attempts=3), sleep=lambda _s: None)


class FlakyClient:
    """Fails twice with 503, then accepts — recording client_order_ids seen."""
    def __init__(self):
        self.attempts = 0
        self.seen_ids = []

    def submit_order(self, **kw):
        self.attempts += 1
        self.seen_ids.append(kw["client_order_id"])
        if self.attempts < 3:
            raise BrokerHTTPError(503)
        return {"id": "brk-9", "status": "accepted"}

    def get_account(self):
        return {"equity": "1", "cash": "1", "buying_power": "1"}


def test_order_retry_reuses_same_client_order_id():
    broker = AlpacaPaperBroker(f"https://{PAPER_HOST}", "k", "s", FlakyClient())
    req = OrderRequest(symbol="AAA", side="buy", qty=10, order_type="stop_limit",
                       limit_price=50.5, stop_price=50.0, client_order_id="entry-abc")
    ack = broker.submit_order(req, retry=RetryPolicy(max_attempts=5), sleep=lambda _s: None)
    assert ack.broker_order_id == "brk-9"
    # Every retry used the identical idempotency key -> no duplicate position.
    assert set(broker._client.seen_ids) == {"entry-abc"}
    assert broker._client.attempts == 3
