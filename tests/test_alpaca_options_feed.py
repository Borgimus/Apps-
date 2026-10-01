"""Alpaca options-data adapter hygiene, required before it replaces Tradier.

The adapter silently skipped snapshot chunks that did not return 200, listed
contracts without a usable quote at bid=ask=0, and stamped chains with naive
UTC time that Eastern-time hosts read as four hours in the future.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.brokers.alpaca_broker import AlpacaBroker

EXP = date(2026, 10, 2)


def _broker(handler):
    b = AlpacaBroker("PKTEST", "secret", "https://paper-api.alpaca.markets", options_feed="opra")
    b._data_client = httpx.AsyncClient(base_url="https://data.alpaca.markets",
                                       transport=httpx.MockTransport(handler))
    b._get_option_contracts = AsyncMock(return_value=[
        {"symbol": "SPY261002C00600000", "strike_price": 600, "type": "call", "tradable": True},
        {"symbol": "SPY261002C00610000", "strike_price": 610, "type": "call", "tradable": True},
        {"symbol": "SPY261002P00590000", "strike_price": 590, "type": "put", "tradable": True},
        {"symbol": "SPY261002P00580000", "strike_price": 580, "type": "put", "tradable": False},
    ])
    b.get_quote = AsyncMock(return_value=SimpleNamespace(mid=Decimal("600")))
    return b


def _snap(bid, ask, age_seconds=5):
    t = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {"latestQuote": {"bp": bid, "ap": ask, "t": t},
            "greeks": {"delta": 0.4}, "latestTrade": {"p": ask}, "dailyBar": {"v": 100}}


@pytest.mark.asyncio
async def test_failed_snapshot_request_raises_instead_of_returning_an_empty_chain():
    broker = _broker(lambda r: httpx.Response(401, json={"message": "unauthorized"}))
    with pytest.raises(httpx.HTTPStatusError):
        await broker.get_option_chain("SPY", EXP)


@pytest.mark.asyncio
async def test_only_contracts_with_valid_fresh_quotes_are_listed():
    snaps = {
        "SPY261002C00600000": _snap(1.00, 1.05),             # valid, fresh
        "SPY261002C00610000": _snap(0.00, 0.00),             # no market
        "SPY261002P00590000": _snap(1.10, 1.20, age_seconds=600),  # stale
        "SPY261002P00580000": _snap(1.00, 1.10),             # not tradable
    }
    broker = _broker(lambda r: httpx.Response(200, json={"snapshots": snaps}))
    chain = await broker.get_option_chain("SPY", EXP)
    assert [c.option_symbol for c in chain.calls + chain.puts] == ["SPY261002C00600000"]
    listed = chain.calls[0]
    assert (listed.bid, listed.ask) == (Decimal("1.0"), Decimal("1.05"))
    assert listed.quote_feed == "opra"


@pytest.mark.asyncio
async def test_chain_fetch_time_is_timezone_aware():
    broker = _broker(lambda r: httpx.Response(200, json={"snapshots": {}}))
    chain = await broker.get_option_chain("SPY", EXP)
    assert chain.fetched_at.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - chain.fetched_at).total_seconds()) < 5
    assert chain.calls == [] and chain.puts == []
