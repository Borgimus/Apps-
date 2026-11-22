"""Explicit adjusted stock closes and unadjusted volatility-index closes."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import math

from .bars import CACHE_SCHEMA, cache_path, read_cache, write_cache
from .symbols import LISTED, SYMBOL


def download_close(symbol: str, start: date, end: date, *, adjusted: bool):
    import yfinance as yf

    frame = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                        auto_adjust=adjusted, progress=False, threads=False, multi_level_index=False,
                        timeout=15)
    if frame.empty or "Close" not in frame:
        raise ValueError(f"yfinance daily closes unavailable for {symbol}")
    return {t.date().isoformat(): float(v) for t, v in frame["Close"].items()}


def daily_closes(symbols: list[str], start: date, end: date, cache: Path, *, downloader=download_close):
    panel = {}
    for symbol in symbols:
        if not SYMBOL.fullmatch(symbol) or start >= end:
            raise ValueError("Invalid daily symbol or session range")
        first = max(start, LISTED.get(symbol, start))
        if first >= end:
            panel[symbol] = {}
            continue
        adjusted = not symbol.startswith("^")
        manifest = {"schema": CACHE_SCHEMA, "symbol": symbol, "start": first.isoformat(),
                    "end": end.isoformat(), "auto_adjust": adjusted, "provider": "yfinance"}
        path = cache_path(cache, "daily", manifest)
        cached = read_cache(path, manifest)
        rows = cached if cached is not None else downloader(symbol, first, end, adjusted=adjusted)
        clean = {}
        for t, price in rows.items():
            day = date.fromisoformat(t)
            if not first <= day < end or not math.isfinite(price) or price <= 0:
                raise ValueError(f"Invalid daily close for {symbol} on {t}")
            clean[day] = float(price)
        if not clean:
            raise ValueError(f"No daily bars for {symbol}")
        if cached is None:
            write_cache(path, manifest, {day.isoformat(): value for day, value in clean.items()})
        panel[symbol] = dict(sorted(clean.items()))
    return panel
