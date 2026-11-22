"""Alpaca five-minute stock history. The only network operation is a GET."""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from app.research.equity_observer import ET, INTERVAL, stamp
from .symbols import segments

ENDPOINT = "https://data.alpaca.markets"
MAX_PAGES = 100
CACHE_SCHEMA = 1


def cache_root() -> Path:
    return Path(os.getenv("TRADER_DATA_CACHE", "~/.cache/trader-history")).expanduser()


def utc(value: str | datetime) -> datetime:
    return stamp(value).astimezone(timezone.utc)


def normalize_bar(row: dict, *, short_names=False) -> dict:
    t = utc(row["t"])
    if t.minute % 5 or t.second or t.microsecond:
        raise ValueError("Bar start is not aligned to the five-minute grid")
    keys = ("open", "high", "low", "close", "volume")
    fields = ("o", "h", "l", "c", "v") if short_names else keys
    if any(isinstance(row[field], bool) for field in fields):
        raise ValueError("Boolean OHLCV values are invalid")
    values = [float(row[field]) for field in fields]
    o, h, l, c, v = values
    if (not all(math.isfinite(x) for x in values) or min(o, h, l, c) <= 0
            or v < 0 or h < max(o, c) or l > min(o, c) or h < l):
        raise ValueError("Malformed OHLCV prices or volume")
    return {"t": t.isoformat(), **dict(zip(keys, values))}


def cache_path(cache: Path, endpoint: str, manifest: dict) -> Path:
    key = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return cache / endpoint / f"{key}.json.gz"


def read_cache(path: Path, manifest: dict):
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload["manifest"] != manifest:
        raise ValueError("History cache manifest mismatch")
    return payload["rows"]


def write_cache(path: Path, manifest: dict, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        tmp = Path(temporary.name)
    try:
        data = json.dumps({"manifest": manifest, "rows": rows}, sort_keys=True, allow_nan=False)
        tmp.write_bytes(gzip.compress(data.encode(), mtime=0))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class AlpacaHistory:
    def __init__(self, key: str | None, secret: str | None, cache: Path,
                 *, transport=None):
        self.authorized = bool(key and secret)
        self.cache = cache
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret} if self.authorized else {}
        self.client = httpx.Client(base_url=ENDPOINT, headers=headers, timeout=25, transport=transport)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def _request(self, symbol: str, start: date, end: date, feed: str, asof: str) -> list[dict]:
        lower = datetime.combine(start, time(9, 30), ET).astimezone(timezone.utc)
        upper = datetime.combine(end, time.min, ET).astimezone(timezone.utc)
        manifest = {"schema": CACHE_SCHEMA, "feed": feed, "symbol": symbol,
                    "start": lower.isoformat(), "end": upper.isoformat(),
                    "asof": asof, "timeframe": "5Min", "adjustment": "raw"}
        path = cache_path(self.cache, "alpaca", manifest)
        cached = read_cache(path, manifest)
        if cached is not None:
            return [normalize_bar(r) for r in cached]
        if not self.authorized:
            raise ValueError("Historical bars require an existing Alpaca key pair or a matching local cache")
        params = {k: v for k, v in manifest.items() if k not in {"schema", "symbol"}}
        params.update(symbols=symbol, limit=10000)
        result: dict[datetime, dict] = {}
        tokens: set[str] = set()
        for _ in range(MAX_PAGES):
            response = self.client.get("/v2/stocks/bars", params=params)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("bars")
            if not isinstance(data, dict) or set(data) - {symbol}:
                raise ValueError("Invalid or unexpected historical stock symbols")
            rows = data.get(symbol, [])
            if not isinstance(rows, list):
                raise ValueError("Historical bars must be a list")
            for item in rows:
                bar = normalize_bar(item, short_names=True)
                t = utc(bar["t"])
                if not lower <= t <= upper:
                    raise ValueError("Historical bar outside the requested range")
                local = t.astimezone(ET)
                if (local.weekday() >= 5 or not time(9, 30) <= local.time() < time(16)
                        or t + INTERVAL > upper):
                    continue
                if t in result and result[t] != bar:
                    raise ValueError("Conflicting historical bar revision")
                result[t] = bar
            token = payload.get("next_page_token")
            if token is None or token == "":
                ordered = [result[t] for t in sorted(result)]
                write_cache(path, manifest, ordered)
                return ordered
            if not isinstance(token, str) or token in tokens:
                raise ValueError("Invalid or repeated historical page token")
            tokens.add(token)
            params["page_token"] = token
        raise ValueError("Historical page limit exceeded")

    def bars(self, symbols: list[str], start: date, end: date, *, feed: str) -> dict[str, list[dict]]:
        if feed not in {"iex", "sip"} or not symbols or len(symbols) != len(set(symbols)):
            raise ValueError("Select an explicit SIP or IEX feed and distinct symbols")
        panel = {}
        for symbol in symbols:
            rows = []
            for provider, first, last, asof in segments(symbol, start, end):
                while first < last:
                    # Bound request volume and make retries resume from completed chunks.
                    stop = min(last, date(first.year + 1, 1, 1))
                    rows.extend(self._request(provider, first, stop, feed, asof))
                    first = stop
            panel[symbol] = rows
        return panel
