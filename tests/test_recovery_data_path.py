"""Provider-switch failures that mocked snapshots previously concealed."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from app.brokers.alpaca_broker import AlpacaBroker
from app.operations.provider_health import ProviderHealth, InstrumentedBroker, ProviderUnavailable
from app.operations.monitor import options_data_canary, ET


def client(handler):
    b = AlpacaBroker('PKTEST', 'secret', 'https://paper-api.alpaca.markets', options_feed='opra')
    b._data_client = httpx.AsyncClient(base_url='https://data.alpaca.markets', transport=httpx.MockTransport(handler))
    return b


@pytest.mark.asyncio
async def test_actual_snapshot_shape_gets_liquidity_volume_from_bars():
    now = datetime.now(timezone.utc)
    symbol = 'SPY261030C00600000'
    def route(r):
        if r.url.path.endswith('/snapshots'):
            return httpx.Response(200, json={'snapshots': {symbol: {
                'latestQuote': {'bp': 1, 'ap': 1.05, 't': now.isoformat()},
                'latestTrade': {'p': 1}, 'greeks': {'delta': .4}}}})
        assert r.url.path.endswith('/bars')
        assert r.url.params['timeframe'] == '1Day'
        return httpx.Response(200, json={'bars': {symbol: [{'t': r.url.params['start'], 'v': 80}]}})
    b = client(route)
    b._get_option_contracts = AsyncMock(return_value=[{'symbol': symbol, 'strike_price': '600', 'type': 'call', 'open_interest': '200'}])
    b.get_quote = AsyncMock(return_value=SimpleNamespace(mid=600))
    try:
        chain = await b.get_option_chain('SPY', now.date())
        assert len(chain.calls) == 1 and chain.calls[0].volume == 80
        assert chain.calls[0].delta == .4
    finally:
        await b.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['403', 'malformed', 'yesterday', 'pagination_loop'])
async def test_volume_failure_propagates_instead_of_becoming_zero(failure):
    def route(r):
        if failure == '403': return httpx.Response(403, json={})
        if failure == 'malformed': return httpx.Response(200, json={})
        stamp = datetime.now(ET) - timedelta(days=1)
        if failure == 'yesterday': return httpx.Response(200, json={'bars': {'TEST': [{'t': stamp.isoformat(), 'v': 50}]}})
        return httpx.Response(200, json={'bars': {}, 'next_page_token': 'same'})
    b = client(route)
    try:
        with pytest.raises((ValueError, RuntimeError, httpx.HTTPStatusError)):
            await b._fetch_option_volumes(['TEST'])
    finally: await b.close()


@pytest.mark.asyncio
async def test_snapshot_and_volume_pagination_and_chunk_limits():
    calls = []
    def route(r):
        calls.append(r)
        symbols = r.url.params['symbols'].split(',')
        assert len(symbols) <= 100
        if r.url.path.endswith('/snapshots'):
            if 'page_token' not in r.url.params:
                return httpx.Response(200, json={'snapshots': {symbols[0]: {'x': 1}}, 'next_page_token': 'next'})
            return httpx.Response(200, json={'snapshots': {symbols[-1]: {'x': 2}}})
        return httpx.Response(200, json={'bars': {s: [{'t': r.url.params['start'], 'v': 3}] for s in symbols}})
    b = client(route)
    try:
        assert set(await b._fetch_snapshots(['A', 'B'])) == {'A', 'B'}
        assert len(await b._fetch_option_volumes([f'T{i}' for i in range(101)])) == 101
    finally: await b.close()


@pytest.mark.asyncio
async def test_working_expiration_metadata_cannot_reset_dead_quote_feed():
    now = [datetime.now(ET)]
    health = ProviderHealth(clock=lambda: now[0])
    def rejected():
        r = httpx.Response(401, request=httpx.Request('GET', 'https://example.test'))
        return httpx.HTTPStatusError('rejected', request=r.request, response=r)
    raw = SimpleNamespace(get_available_expirations=AsyncMock(return_value=[now[0].date()]),
                          get_option_chain=AsyncMock(side_effect=rejected()))
    b = InstrumentedBroker(raw, health)
    for _ in range(3):
        await b.get_available_expirations('SPY')
        with pytest.raises(httpx.HTTPStatusError): await b.get_option_chain('SPY', None)
    assert health.breaker_open
    await b.get_available_expirations('SPY')
    with pytest.raises(ProviderUnavailable): await b.get_option_chain('SPY', None)
    now[0] += timedelta(minutes=15)
    await b.get_available_expirations('SPY')
    raw.get_option_chain.side_effect = None
    raw.get_option_chain.return_value = SimpleNamespace(calls=[1], puts=[])
    await b.get_option_chain('SPY', None)
    assert not health.breaker_open


@pytest.mark.asyncio
async def test_empty_options_canary_fails_during_the_session(monkeypatch):
    settings = SimpleNamespace(live_trading_enabled=False, options_data_provider='alpaca', alpaca_options_feed='opra')
    b = SimpleNamespace(get_available_expirations=AsyncMock(return_value=[datetime(2026,10,26).date()]),
                        get_option_chain=AsyncMock(return_value=SimpleNamespace(calls=[], puts=[])), close=AsyncMock())
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    monkeypatch.setattr('app.brokers.factory.get_broker', lambda s: b)
    with pytest.raises(ValueError, match='No fresh'):
        await options_data_canary(now=datetime(2026,10,26,10,0,tzinfo=ET))
