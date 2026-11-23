from datetime import date
import httpx
import pytest

from app.research.history.bars import AlpacaHistory
from app.research.history.daily import daily_closes
from app.research.history.symbols import segments


def test_history_get_pagination_cache_and_session_gaps(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v2/stocks/bars"
        assert request.url.params["feed"] == "sip"
        token = request.url.params.get("page_token")
        stamp = "2026-11-20T14:40:00Z" if token else "2026-11-20T14:30:00Z"
        row = {"t": stamp, "o": 10, "h": 12, "l": 9, "c": 11, "v": 3}
        return httpx.Response(200, json={"bars": {"SPY": [row]}, "next_page_token": None if token else "next"})
    dates = date(2026, 11, 20), date(2026, 11, 21)
    with AlpacaHistory("mock", "mock", tmp_path, transport=httpx.MockTransport(respond)) as source:
        rows = source.bars(["SPY"], *dates, feed="sip")
    with AlpacaHistory(None, None, tmp_path) as source:
        assert source.bars(["SPY"], *dates, feed="sip") == rows
        with pytest.raises(ValueError, match="key pair"):
            source.bars(["SPY"], *dates, feed="iex")
    assert len(calls) == 2
    assert [r["t"][11:16] for r in rows["SPY"]] == ["14:30", "14:40"]


def test_ticker_change_and_listing_cutoff():
    first, last = date(2020, 1, 1), date(2026, 1, 1)
    assert [x[:2] for x in segments("XYZ", first, last)] == [("SQ", first), ("XYZ", date(2025, 1, 21))]
    assert list(segments("ARM", first, last))[0][1] == date(2023, 9, 14)


def test_daily_index_close_and_equity_adjustment(tmp_path):
    calls = []
    def downloader(symbol, start, end, *, adjusted):
        calls.append((symbol, adjusted))
        return {start.isoformat(): 100.0}
    dates = date(2026, 11, 20), date(2026, 11, 21)
    data = daily_closes(["SPY", "^VIX"], *dates, tmp_path, downloader=downloader)
    assert daily_closes(["SPY", "^VIX"], *dates, tmp_path, downloader=downloader) == data
    assert calls == [("SPY", True), ("^VIX", False)]


def test_morning_panels_keep_year_boundaries_and_reuse_full_session_cache(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        year = request.url.params["start"][:4]
        rows = [{"t": f"{year}-01-03T{at}:00Z", "o": 10, "h": 12, "l": 9, "c": 11, "v": 3}
                for at in ("14:30", "17:30")]
        return httpx.Response(200, json={"bars": {"SPY": rows}})

    dates = date(2022, 1, 1), date(2024, 1, 1)
    with AlpacaHistory("mock", "mock", tmp_path, transport=httpx.MockTransport(respond)) as source:
        panels = list(source.morning_panels(["SPY"], *dates, feed="sip"))
    with AlpacaHistory(None, None, tmp_path) as source:
        cached = source.bars(["SPY"], *dates, feed="sip")
    assert len(calls) == 2
    assert [panel["SPY"][0]["t"][:4] for panel in panels] == ["2022", "2023"]
    assert [len(panel["SPY"]) for panel in panels] == [1, 1]
    assert len(cached["SPY"]) == 4
