"""Retry / backoff policy for broker (Alpaca) faults.

Classifies transient faults — timeouts, HTTP 429 (rate limit), 5xx, and connection
drops — as retryable with capped exponential backoff, and treats 4xx (other than 429) as
permanent. When retries are exhausted the caller FAILS CLOSED: an order whose fate is
unknown is never assumed filled. A provider ``Retry-After`` hint is honored.

``sleep`` is injected so tests never actually wait, and order-submission retries reuse the
SAME idempotent client_order_id (see execution/orders.py) so a retry cannot duplicate a position.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class BrokerTimeout(Exception):
    pass


class BrokerConnectionError(Exception):
    pass


class BrokerHTTPError(Exception):
    def __init__(self, status_code: int, message: str = "", retry_after: float | None = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.retry_after = retry_after


class RetriesExhausted(Exception):
    """Raised after the final attempt fails — the caller must fail closed."""


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 2.0
    max_delay: float = 30.0
    multiplier: float = 2.0

    def backoff_seconds(self, attempt: int, retry_after: float | None = None) -> float:
        """Delay before the ``attempt``-th retry (1-indexed). Honors a Retry-After hint."""
        exp = self.base_delay * (self.multiplier ** (attempt - 1))
        delay = min(exp, self.max_delay)
        if retry_after is not None:
            delay = max(delay, retry_after)
        return delay


def is_retryable(exc: Exception) -> tuple[bool, float | None]:
    """Return (retryable, retry_after_hint) for a broker exception."""
    if isinstance(exc, (BrokerTimeout, BrokerConnectionError)):
        return True, None
    if isinstance(exc, BrokerHTTPError):
        if exc.status_code in RETRYABLE_STATUS:
            return True, exc.retry_after
        return False, None
    return False, None


def call_with_retry(
    fn: Callable[[], object],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None],
    on_retry: Callable[[int, Exception, float], None] | None = None,
):
    """Call ``fn`` with retry/backoff. Raises RetriesExhausted (fail-closed) if all attempts
    fail on retryable errors; re-raises immediately on a non-retryable error."""
    last: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classification below decides
            retryable, hint = is_retryable(exc)
            if not retryable:
                raise
            last = exc
            if attempt == policy.max_attempts:
                break
            delay = policy.backoff_seconds(attempt, hint)
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)
    raise RetriesExhausted(f"exhausted {policy.max_attempts} attempts; last error: {last}") from last
