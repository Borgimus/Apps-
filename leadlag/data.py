"""Data layer: Databento ES trades + Alpaca SPY trades → aligned sub-second grids.

Raw trades are cached per (instrument, session) as parquet with int64 ns UTC
timestamps. Analysis operates on a fixed 50ms master grid (last trade price,
forward-filled), built per session by `session_grid`.

Clock caveat (documented in the report): ES timestamps are CME MDP3
`ts_event` (matching-engine time); SPY timestamps are SIP consolidated-feed
time. Cross-feed skew is sub-millisecond to low-millisecond — negligible at
the 50ms grid, but sub-50ms conclusions are out of scope for this study.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import Config

log = logging.getLogger("leadlag.data")
ET = ZoneInfo("America/New_York")
NS = 1_000_000_000


def _ns(day: dt.date, t: dt.time) -> int:
    return int(dt.datetime.combine(day, t, tzinfo=ET).timestamp() * NS)


class Store:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache = Path(cfg.cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self._db = None
        self._alpaca = None

    # ── clients ──

    @property
    def db(self):
        if self._db is None:
            if not self.cfg.databento_key:
                raise RuntimeError("DATABENTO_API_KEY not set")
            import databento

            self._db = databento.Historical(self.cfg.databento_key)
        return self._db

    @property
    def alpaca(self):
        if self._alpaca is None:
            if not self.cfg.alpaca_key:
                raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
            from alpaca.data.historical import StockHistoricalDataClient

            self._alpaca = StockHistoricalDataClient(
                self.cfg.alpaca_key, self.cfg.alpaca_secret)
        return self._alpaca

    # ── cost estimation ──

    def estimate_cost(self, days: list[dt.date], symbol: str | None = None,
                      sample: int = 20, seed: int = 0) -> dict:
        """Estimate Databento cost for fetching `days` opening windows by
        sampling `sample` days and extrapolating."""
        symbol = symbol or self.cfg.fut_symbol
        rng = np.random.default_rng(seed)
        picks = sorted(rng.choice(len(days), size=min(sample, len(days)),
                                  replace=False))
        total = 0.0
        for i in picks:
            d = days[i]
            total += float(self.db.metadata.get_cost(
                dataset=self.cfg.dataset, symbols=[symbol],
                stype_in="continuous", schema="trades",
                start=pd.Timestamp(_ns(d, self.cfg.fetch_start), unit="ns", tz="UTC"),
                end=pd.Timestamp(_ns(d, self.cfg.fetch_end), unit="ns", tz="UTC"),
            ))
        per_day = total / len(picks)
        return {"sampled_days": len(picks), "usd_per_day": per_day,
                "estimated_total_usd": per_day * len(days), "n_days": len(days)}

    # ── fetching ──

    def _path(self, tag: str, day: dt.date) -> Path:
        return self.cache / f"{tag}_{day.isoformat()}.parquet"

    def get_futures_trades(self, day: dt.date, symbol: str | None = None,
                           tag: str = "ES") -> pd.DataFrame | None:
        """Columns: ts (int64 ns UTC), price, size, sign (+1 buy aggressor,
        -1 sell aggressor, 0 unknown). None on holidays."""
        p = self._path(tag, day)
        if p.exists():
            df = pd.read_parquet(p)
            return None if df.empty else df
        symbol = symbol or self.cfg.fut_symbol
        try:
            data = self.db.timeseries.get_range(
                dataset=self.cfg.dataset, symbols=[symbol],
                stype_in="continuous", schema="trades",
                start=pd.Timestamp(_ns(day, self.cfg.fetch_start), unit="ns", tz="UTC"),
                end=pd.Timestamp(_ns(day, self.cfg.fetch_end), unit="ns", tz="UTC"),
            )
            df = data.to_df()
        except Exception as exc:
            msg = str(exc)
            if "422" in msg or "no data" in msg.lower():
                df = pd.DataFrame()
            else:
                raise
        if df.empty:
            out = pd.DataFrame(columns=["ts", "price", "size", "sign"])
        else:
            sign = np.where(df["side"] == "B", 1, np.where(df["side"] == "A", -1, 0))
            out = pd.DataFrame({
                "ts": df.index.view("int64"),
                "price": df["price"].to_numpy(dtype=float),
                "size": df["size"].to_numpy(dtype=float),
                "sign": sign.astype(np.int8),
            }).sort_values("ts").reset_index(drop=True)
        out.to_parquet(p)
        return None if out.empty else out

    def get_equity_trades(self, day: dt.date) -> pd.DataFrame | None:
        """SPY SIP trades. Columns: ts (int64 ns UTC), price, size, sign=0."""
        p = self._path(self.cfg.equity, day)
        if p.exists():
            df = pd.read_parquet(p)
            return None if df.empty else df
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockTradesRequest

        start = pd.Timestamp(_ns(day, self.cfg.fetch_start), unit="ns", tz="UTC")
        end = pd.Timestamp(_ns(day, self.cfg.fetch_end), unit="ns", tz="UTC")
        resp = self.alpaca.get_stock_trades(StockTradesRequest(
            symbol_or_symbols=self.cfg.equity, start=start.to_pydatetime(),
            end=end.to_pydatetime(), feed=DataFeed.SIP, limit=None))
        rows = resp.data.get(self.cfg.equity, [])
        bad = {"Z", "C", "W", "4"}
        recs = [(int(t.timestamp.timestamp() * NS), t.price, t.size or 0)
                for t in rows
                if t.price > 0 and not bad.intersection(t.conditions or [])]
        out = pd.DataFrame(recs, columns=["ts", "price", "size"])
        out["sign"] = np.int8(0)
        out = out.sort_values("ts").reset_index(drop=True)
        out.to_parquet(p)
        return None if out.empty else out

    def sessions(self, start: dt.date, end: dt.date) -> list[dt.date]:
        return [d.date() for d in pd.bdate_range(start, end)]


# ── grids ──────────────────────────────────────────────────────────────────


@dataclass
class Grid:
    """Aligned master grid for one session (base_dt_ms spacing)."""

    day: dt.date
    dt_ms: int
    t0_ns: int
    x_es: np.ndarray       # log last-trade price, ffilled (NaN before first print)
    x_spy: np.ndarray
    real_es: np.ndarray    # a trade printed in this bin
    real_spy: np.ndarray
    vol_es: np.ndarray
    vol_spy: np.ndarray
    flow_es: np.ndarray    # signed aggressor volume in bin
    study: np.ndarray      # bool mask: 09:30–10:00 ET

    def __len__(self) -> int:
        return len(self.x_es)

    def coarsen(self, dt_ms: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(x_es, x_spy, study) sampled every dt_ms (must be a multiple)."""
        step = dt_ms // self.dt_ms
        return self.x_es[::step], self.x_spy[::step], self.study[::step]


def _bin_last(ts: np.ndarray, vals: np.ndarray, t0: int, dt_ns: int, n: int,
              reduce: str = "last") -> tuple[np.ndarray, np.ndarray]:
    idx = ((ts - t0) // dt_ns).astype(np.int64)
    ok = (idx >= 0) & (idx < n)
    idx, vals = idx[ok], vals[ok]
    out = np.full(n, np.nan)
    hit = np.zeros(n, dtype=bool)
    if reduce == "last":
        out[idx] = vals          # later duplicates overwrite → last in bin
        hit[idx] = True
    else:  # sum
        out = np.zeros(n)
        np.add.at(out, idx, vals)
        hit[idx] = True
    return out, hit


def session_grid(cfg: Config, day: dt.date, es: pd.DataFrame,
                 spy: pd.DataFrame) -> Grid:
    dt_ms = cfg.grid["base_dt_ms"]
    dt_ns = dt_ms * 1_000_000
    t0 = _ns(day, cfg.fetch_start)
    t1 = _ns(day, cfg.fetch_end)
    n = int((t1 - t0) // dt_ns)

    p_es, hit_es = _bin_last(es["ts"].to_numpy(), es["price"].to_numpy(dtype=float), t0, dt_ns, n)
    p_spy, hit_spy = _bin_last(spy["ts"].to_numpy(), spy["price"].to_numpy(dtype=float), t0, dt_ns, n)
    v_es, _ = _bin_last(es["ts"].to_numpy(), es["size"].to_numpy(dtype=float), t0, dt_ns, n, "sum")
    v_spy, _ = _bin_last(spy["ts"].to_numpy(), spy["size"].to_numpy(dtype=float), t0, dt_ns, n, "sum")
    f_es, _ = _bin_last(es["ts"].to_numpy(),
                        (es["size"] * es["sign"]).to_numpy(dtype=float), t0, dt_ns, n, "sum")

    x_es = np.log(pd.Series(p_es).ffill().to_numpy())
    x_spy = np.log(pd.Series(p_spy).ffill().to_numpy())

    s0 = (_ns(day, cfg.study_start) - t0) // dt_ns
    s1 = (_ns(day, cfg.study_end) - t0) // dt_ns
    study = np.zeros(n, dtype=bool)
    study[int(s0):int(s1)] = True

    return Grid(day=day, dt_ms=dt_ms, t0_ns=t0, x_es=x_es, x_spy=x_spy,
                real_es=hit_es, real_spy=hit_spy, vol_es=v_es, vol_spy=v_spy,
                flow_es=f_es, study=study)


def load_grid(cfg: Config, store: Store, day: dt.date) -> Grid | None:
    es = store.get_futures_trades(day)
    spy = store.get_equity_trades(day)
    if es is None or spy is None or len(es) < 100 or len(spy) < 100:
        return None
    return session_grid(cfg, day, es, spy)
