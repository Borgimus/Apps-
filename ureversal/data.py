"""Data layer: Alpaca historical trades → 1-second bars, with parquet caching.

1-second bars are *reconstructed from consolidated trades* (Alpaca serves
second-level aggregates only via trades), per §2 of the spec:

- close  = last trade price in [t, t+1)
- vwap   = volume-weighted price in [t, t+1)
- volume = summed size
- real   = True if the instrument printed in that second (False → forward-fill,
           capped at cfg.max_ffill_s; correlation windows exclude fill-only rows)
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import Config

log = logging.getLogger("ureversal.data")
ET = ZoneInfo("America/New_York")

BAR_COLUMNS = ["close", "vwap", "volume", "n_trades", "real"]


# ── bar reconstruction ─────────────────────────────────────────────────────


def trades_to_second_bars(
    trades: pd.DataFrame,
    start: dt.datetime,
    end: dt.datetime,
    max_ffill_s: int,
) -> pd.DataFrame:
    """Aggregate a trades frame (index: ET timestamps, cols: price, size) into
    a dense 1-second grid [start, end) with forward-fill capped at max_ffill_s."""
    idx = pd.date_range(start, end, freq="1s", inclusive="left", tz=ET)
    if trades.empty:
        out = pd.DataFrame(index=idx, columns=BAR_COLUMNS, dtype=float)
        out["real"] = False
        return out

    sec = trades.index.floor("1s")
    g = trades.groupby(sec)
    px, sz = trades["price"], trades["size"]
    bars = pd.DataFrame(
        {
            "close": g["price"].last(),
            "vwap": (px * sz).groupby(sec).sum() / g["size"].sum().replace(0, np.nan),
            "volume": g["size"].sum(),
            "n_trades": g["price"].count(),
        }
    )
    bars["vwap"] = bars["vwap"].fillna(bars["close"])
    out = bars.reindex(idx)
    out["real"] = out["close"].notna()
    out["close"] = out["close"].ffill(limit=max_ffill_s)
    out["vwap"] = out["vwap"].ffill(limit=max_ffill_s)
    out[["volume", "n_trades"]] = out[["volume", "n_trades"]].fillna(0)
    return out


def clean_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """Drop conditions that must not form bars: exclude out-of-sequence and
    non-regular-way prints where flagged. Alpaca marks conditions in 'conditions';
    we drop the classic exclusions (Z: sold out of sequence, U/H: extended-hours
    prints inside our fetch buffer are impossible, C: cash sale, W: average price)."""
    if trades.empty:
        return trades
    if "conditions" in trades.columns:
        bad = {"Z", "C", "W", "4"}
        keep = trades["conditions"].apply(
            lambda c: not bad.intersection(c) if isinstance(c, (list, tuple)) else True
        )
        trades = trades[keep]
    return trades[trades["price"] > 0]


# ── Alpaca fetch + cache ───────────────────────────────────────────────────


class DataStore:
    """Fetches and caches per-(symbol, session, feed) 1-second bars."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache = Path(cfg.cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self._client = None

    def _cache_path(self, symbol: str, day: dt.date) -> Path:
        return self.cache / f"{symbol}_{day.isoformat()}_{self.cfg.feed}.parquet"

    @property
    def client(self):
        if self._client is None:
            if not self.cfg.alpaca_key:
                raise RuntimeError(
                    "ALPACA_API_KEY / ALPACA_SECRET_KEY not set — cannot fetch "
                    "historical data. Set them in the environment or .env."
                )
            from alpaca.data.historical import StockHistoricalDataClient

            self._client = StockHistoricalDataClient(
                self.cfg.alpaca_key, self.cfg.alpaca_secret
            )
        return self._client

    def _session_bounds(self, day: dt.date) -> tuple[dt.datetime, dt.datetime]:
        s, e = self.cfg.session_start, self.cfg.session_end
        return (
            dt.datetime.combine(day, s, tzinfo=ET),
            dt.datetime.combine(day, e, tzinfo=ET),
        )

    def get_bars(self, symbol: str, day: dt.date) -> pd.DataFrame | None:
        """1-second bars for the opening session window. None if the market
        did not trade that day (holiday/weekend). Cached after first fetch."""
        p = self._cache_path(symbol, day)
        if p.exists():
            df = pd.read_parquet(p)
            df.index = pd.DatetimeIndex(df.index).tz_convert(ET)
            return None if df["real"].sum() == 0 else df

        start, end = self._session_bounds(day)
        trades = self._fetch_trades(symbol, start, end)
        bars = trades_to_second_bars(clean_trades(trades), start, end, self.cfg.max_ffill_s)
        bars.to_parquet(p)  # cache even empty days so holidays aren't re-fetched
        return None if bars["real"].sum() == 0 else bars

    def _fetch_trades(self, symbol: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockTradesRequest

        req = StockTradesRequest(
            symbol_or_symbols=symbol,
            start=start.astimezone(dt.timezone.utc),
            end=end.astimezone(dt.timezone.utc),
            feed=DataFeed(self.cfg.feed),
            limit=None,
        )
        try:
            resp = self.client.get_stock_trades(req)
        except Exception as exc:  # holidays raise nothing; API errors do
            log.warning("fetch %s %s failed: %s", symbol, start.date(), exc)
            raise
        rows = resp.data.get(symbol, [])
        if not rows:
            return pd.DataFrame(columns=["price", "size", "conditions"])
        df = pd.DataFrame(
            {
                "price": [t.price for t in rows],
                "size": [t.size for t in rows],
                "conditions": [t.conditions or [] for t in rows],
            },
            index=pd.DatetimeIndex([t.timestamp for t in rows]).tz_convert(ET),
        )
        return df.sort_index()

    # ── session-level API used by research/backtest ──

    def get_session(self, day: dt.date) -> pd.DataFrame | None:
        """Joined frame for target+leader with column MultiIndex (symbol, field).
        None if either instrument has no data (holiday or fetch gap)."""
        tgt = self.get_bars(self.cfg.target, day)
        led = self.get_bars(self.cfg.leader, day)
        if tgt is None or led is None:
            return None
        joined = pd.concat({self.cfg.target: tgt, self.cfg.leader: led}, axis=1)
        return joined

    def sessions(self, start: dt.date, end: dt.date) -> list[dt.date]:
        """Candidate trading days (weekdays; holidays fall out as empty fetches)."""
        return [d.date() for d in pd.bdate_range(start, end)]
