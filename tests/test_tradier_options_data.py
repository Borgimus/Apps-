from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.brokers.alpaca_tradier_data import AlpacaTradierDataBroker
from app.brokers.factory import get_broker
from app.trading.quote_evidence import fill_evidence_valid
from scripts.capture_session_fingerprint import _static_issues


def make(**overrides):
    args = dict(api_key="PKtest", secret_key="test", base_url="https://paper-api.alpaca.markets",
                is_paper=True, market_data_token="test")
    args.update(overrides)
    return AlpacaTradierDataBroker(**args)


def row(**changes):
    ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    q = dict(symbol="SPY_TEST", bid=1, ask=1.01, bid_date=ms, ask_date=ms,
             last=1, volume=0, open_interest=123, greeks={"delta": 0.0})
    q.update(changes)
    return q


async def mock_data(broker, payload, status=200):
    await broker._tradier_data.aclose()
    broker._tradier_data = httpx.AsyncClient(base_url="https://api.tradier.com/v1/",
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)),
        event_hooks={"request": [broker._check_data_request]})


@pytest.mark.asyncio
async def test_quote_and_orders_use_separate_hosts():
    b = make()
    try:
        await mock_data(b, {"quotes": {"quote": row()}})
        q = await b.get_option_quote("SPY_TEST")
        assert q.feed == "tradier_opra" and q.delta == 0
        assert fill_evidence_valid(bid=float(q.bid), ask=float(q.ask), timestamp=q.timestamp,
                                   feed=q.feed, now=datetime.now(timezone.utc))
        assert b.verify_paper_endpoint()[0]
        assert b._client.base_url.host == "paper-api.alpaca.markets"
        with pytest.raises(ValueError):
            await b._tradier_data.post("accounts/123/orders")
        with pytest.raises(ValueError):
            await b._tradier_data.get("https://example.com/markets/quotes")
    finally:
        await b.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"bid_date": None}, {"ask_date": None}, {"bid": 0}, {"bid": 2},
    {"ask": float("inf")}, {"bid_date": 1}, {"ask_date": 1},
    {"ask_date": 9999999999999}, {"symbol": "WRONG"},
])
async def test_invalid_quotes_cannot_enter_execution(changes):
    b = make()
    try:
        # MockTransport JSON serialization disallows Infinity, use string input.
        if changes.get("ask") == float("inf"):
            changes = {"ask": "Infinity"}
        await mock_data(b, {"quotes": {"quote": row(**changes)}})
        with pytest.raises(ValueError, match="No fresh"):
            await b.get_option_quote("SPY_TEST")
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_chain_no_metadata_price_fallback_and_zero_delta_preserved():
    b = make()
    try:
        await mock_data(b, {"quotes": {"quote": row()}})
        c = dict(symbol="SPY_TEST", strike_price="100", type="call", tradable=True)
        b._get_option_contracts = AsyncMock(return_value=[c, dict(c, symbol="MISSING"), dict(c, symbol="INACTIVE", tradable=False)])
        b.get_quote = AsyncMock(return_value=SimpleNamespace(mid=Decimal("100")))
        chain = await b.get_option_chain("SPY", date(2026, 9, 8))
        assert len(chain.calls) == 1
        assert chain.calls[0].delta == 0
        assert chain.calls[0].volume == 0
        assert chain.calls[0].open_interest == 123
        assert chain.calls[0].quote_feed == "tradier_opra"
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_provider_error_propagates_without_fallback():
    b = make()
    try:
        await mock_data(b, {}, 403)
        with pytest.raises(httpx.HTTPStatusError):
            await b.get_option_quote("SPY_TEST")
    finally:
        await b.close()


@pytest.mark.parametrize("args", [{"is_paper": False}, {"base_url": "https://api.alpaca.markets"},
                                  {"api_key": "AKtest"}, {"market_data_token": ""}])
def test_reject_live_or_missing_credentials(args):
    with pytest.raises(ValueError):
        make(**args)


def test_older_side_is_used():
    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=30)
    q = row(bid_date=old.timestamp()*1000, ask_date=now.timestamp()*1000)
    assert abs((AlpacaTradierDataBroker._timestamp(q, now)-old).total_seconds()) < .001


def test_provider_switch_blocks_old_cohort():
    issues = _static_issues({"paper_account_identifier": "test"},
        {"options_data_provider": "tradier", "options_data_adapter_hash": "123"})
    assert any("provider changed" in s for s in issues)
    assert any("hash missing" in s for s in issues)


def test_factory_rejects_tradier_execution_for_data_mode():
    with pytest.raises(ValueError):
        get_broker(SimpleNamespace(broker="tradier", live_trading_enabled=False, options_data_provider="tradier"))
