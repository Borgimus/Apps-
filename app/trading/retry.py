"""Bounded retry for broker and data calls."""
from __future__ import annotations

import asyncio
import logging

from app.operations.provider_health import ProviderUnavailable, is_auth_error

logger = logging.getLogger("session_runner")


async def retry_async(coro_fn, label: str = "", max_retries: int = 3):
    delay = 2
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            # Rejected credentials do not recover within seconds; retrying only
            # multiplies unauthorized requests and stalls the poll loop.
            if isinstance(exc, ProviderUnavailable) or is_auth_error(exc):
                logger.error("[%s] not retried: %s", label, exc)
                raise
            if attempt < max_retries:
                logger.warning("[%s] attempt %d failed: %s — retrying in %ds", label, attempt, exc, delay)
                await asyncio.sleep(delay)
                delay *= 2
    logger.error("[%s] all %d attempts failed: %s", label, max_retries, last_exc)
    raise last_exc
