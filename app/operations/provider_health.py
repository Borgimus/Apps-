"""Options-data provider health, measured at the broker boundary.

Every options-data request made during a session (scanner confirmation, entry
selection, exit monitoring, shadow observation) passes through one broker
object. Wrapping that object meters all of them in one place, so a provider
outage cannot hide behind per-call ``except Exception`` handlers, and an HTTP
200 that carries no data counts as a failure rather than a quiet market.

The session is judged on what it received, not on whether anything raised:
a session with no options data is degraded even when every call "succeeded".
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

METERED_OPERATIONS = ("get_available_expirations", "get_option_chain", "get_option_quote")

# Failures above this share of options requests make the session's evidence
# unreliable even if some data arrived.
MAX_FAILURE_RATE = 0.10


class ProviderUnavailable(RuntimeError):
    """Raised instead of calling a provider whose credentials were rejected."""


def is_auth_error(exc: BaseException) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return True
    text = str(exc)
    return "401 Unauthorized" in text or "403 Forbidden" in text


def _is_empty(operation: str, result: Any) -> bool:
    if operation == "get_available_expirations":
        return not result
    if operation == "get_option_chain":
        return result is None or not (getattr(result, "calls", None) or getattr(result, "puts", None))
    if operation == "get_option_quote":
        try:
            return float(result.bid) <= 0 and float(result.ask) <= 0
        except (AttributeError, TypeError, ValueError):
            return True
    return False


class ProviderHealth:
    """Counts options-data outcomes and stops calling a provider that rejects auth.

    After ``auth_breaker_threshold`` consecutive authorization failures the
    breaker opens and requests fail immediately with ``ProviderUnavailable``.
    One probe request is allowed every ``probe_interval_seconds`` so a
    transient rejection does not blind the rest of the session.
    """

    def __init__(self, *, auth_breaker_threshold: int = 3, probe_interval_seconds: float = 900,
                 clock: Optional[Callable[[], datetime]] = None):
        self._threshold = auth_breaker_threshold
        self._probe_interval = probe_interval_seconds
        self._clock = clock or datetime.now
        self.reset()

    def reset(self) -> None:
        self.requests: Counter = Counter()
        self.successes: Counter = Counter()
        self.empty: Counter = Counter()
        self.failures: Counter = Counter()
        self.auth_failures = 0
        self.consecutive_auth_failures = 0
        self.short_circuited = 0
        self.breaker_opened_at: Optional[datetime] = None
        self.breaker_ever_opened = False
        self._last_probe_at: Optional[datetime] = None
        self.failure_samples: List[str] = []

    # ── Breaker ────────────────────────────────────────────────────────────

    @property
    def breaker_open(self) -> bool:
        return self.breaker_opened_at is not None

    def before(self, operation: str) -> None:
        if not self.breaker_open:
            return
        now = self._clock()
        last = self._last_probe_at or self.breaker_opened_at
        if (now - last).total_seconds() >= self._probe_interval:
            self._last_probe_at = now
            logger.warning("Options provider breaker: probing with %s", operation)
            return
        self.short_circuited += 1
        raise ProviderUnavailable(
            f"options provider rejected authorization; {operation} not attempted"
        )

    # ── Outcomes ───────────────────────────────────────────────────────────

    def record_success(self, operation: str, result: Any) -> None:
        self.requests[operation] += 1
        if _is_empty(operation, result):
            self.empty[operation] += 1
            return
        self.successes[operation] += 1
        self.consecutive_auth_failures = 0
        if self.breaker_open:
            logger.warning("Options provider breaker closed: %s succeeded", operation)
            self.breaker_opened_at = None
            self._last_probe_at = None

    def record_failure(self, operation: str, exc: BaseException) -> None:
        self.requests[operation] += 1
        self.failures[operation] += 1
        if len(self.failure_samples) < 20:
            self.failure_samples.append(f"{operation}: {exc}"[:240])
        if not is_auth_error(exc):
            return
        self.auth_failures += 1
        self.consecutive_auth_failures += 1
        if self.consecutive_auth_failures >= self._threshold and not self.breaker_open:
            self.breaker_opened_at = self._clock()
            self.breaker_ever_opened = True
            logger.critical(
                "OPTIONS PROVIDER AUTHORIZATION REJECTED %d times; requests stopped: %s",
                self.consecutive_auth_failures, exc,
            )

    # ── Judgement ──────────────────────────────────────────────────────────

    def degraded_reasons(self) -> List[str]:
        """Why this session's options evidence is unusable; empty when healthy."""
        reasons = []
        if self.auth_failures:
            reasons.append("options_provider_authorization_rejected")
        chain_requests = self.requests["get_option_chain"]
        if chain_requests == 0:
            reasons.append("no_options_chain_requests")
        elif self.successes["get_option_chain"] == 0:
            reasons.append("no_options_chains_received")
        total = sum(self.requests.values())
        failed = sum(self.failures.values()) + sum(self.empty.values())
        if total and failed / total > MAX_FAILURE_RATE:
            reasons.append("options_failure_rate_above_threshold")
        return reasons

    def snapshot(self) -> Dict[str, Any]:
        return {
            "requests": dict(self.requests),
            "with_data": dict(self.successes),
            "empty_responses": dict(self.empty),
            "failures": dict(self.failures),
            "authorization_failures": self.auth_failures,
            "short_circuited": self.short_circuited,
            "breaker_opened": self.breaker_ever_opened,
            "failure_samples": list(self.failure_samples),
            "degraded_reasons": self.degraded_reasons(),
        }


class InstrumentedBroker:
    """Delegates to a broker; options-data calls are metered and breaker-guarded."""

    def __init__(self, broker, health: ProviderHealth):
        self._broker = broker
        self._health = health

    @property
    def provider_health(self) -> ProviderHealth:
        return self._health

    def __getattr__(self, name):
        attr = getattr(self._broker, name)
        if name not in METERED_OPERATIONS:
            return attr
        health = self._health

        async def metered(*args, **kwargs):
            health.before(name)
            try:
                result = await attr(*args, **kwargs)
            except Exception as exc:
                health.record_failure(name, exc)
                raise
            health.record_success(name, result)
            return result

        return metered
