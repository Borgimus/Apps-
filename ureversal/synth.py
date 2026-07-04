"""Synthetic SPY/DIA 1-second session generator.

Used by the test suite and pipeline dry-runs: generates correlated random-walk
sessions and can *plant* U-reversal episodes with known ground truth (DIA
reversing a configurable number of seconds before SPY). This lets us verify
that the detector finds what it is designed to find, and — just as important —
that the research pipeline reports NO edge on sessions where none was planted.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .data import BAR_COLUMNS

ET = ZoneInfo("America/New_York")


@dataclass
class PlantedU:
    """Ground truth for one planted episode (offsets in seconds from series start)."""

    decline_start: int
    spy_bottom: int
    dia_bottom: int          # dia_bottom < spy_bottom  →  DIA leads
    recovery_end: int
    depth_bps: float


@dataclass
class SynthSession:
    day: dt.date
    bars: pd.DataFrame       # MultiIndex columns (symbol, field), like DataStore.get_session
    planted: list[PlantedU] = field(default_factory=list)


def make_session(
    day: dt.date,
    seed: int,
    n_seconds: int = 28 * 60,
    start_et: dt.time = dt.time(9, 28),
    base_corr: float = 0.85,
    vol_bps_per_s: float = 0.8,
    plant: list[PlantedU] | None = None,
    spy0: float = 500.0,
    dia0: float = 380.0,
    target: str = "SPY",
    leader: str = "DIA",
) -> SynthSession:
    rng = np.random.default_rng(seed)

    # correlated 1s log returns (common factor + idiosyncratic)
    common = rng.normal(0, 1, n_seconds)
    a = np.sqrt(base_corr)
    b = np.sqrt(1 - base_corr)
    r_spy = (a * common + b * rng.normal(0, 1, n_seconds)) * vol_bps_per_s * 1e-4
    r_dia = (a * common + b * rng.normal(0, 1, n_seconds)) * vol_bps_per_s * 1e-4

    # plant U episodes as drift overlays: down into bottom, flat, up out of it
    for u in plant or []:
        for r, bottom in ((r_spy, u.spy_bottom), (r_dia, u.dia_bottom)):
            down = np.arange(u.decline_start, bottom)
            up = np.arange(bottom, u.recovery_end)
            depth = u.depth_bps * 1e-4
            if len(down):
                r[down] -= depth / len(down)
            if len(up):
                r[up] += depth / len(up)

    x_spy = np.log(spy0) + np.cumsum(r_spy)
    x_dia = np.log(dia0) + np.cumsum(r_dia)

    idx = pd.date_range(
        dt.datetime.combine(day, start_et, tzinfo=ET), periods=n_seconds, freq="1s"
    )

    def frame(x: np.ndarray) -> pd.DataFrame:
        df = pd.DataFrame(index=idx, columns=BAR_COLUMNS, dtype=float)
        df["close"] = np.exp(x)
        df["vwap"] = df["close"]
        df["volume"] = rng.integers(100, 5000, n_seconds).astype(float)
        df["n_trades"] = rng.integers(1, 50, n_seconds).astype(float)
        df["real"] = True
        return df

    bars = pd.concat({target: frame(x_spy), leader: frame(x_dia)}, axis=1)
    return SynthSession(day=day, bars=bars, planted=list(plant or []))


def default_planted_u(offset_s: int = 420, lead_s: int = 8, depth_bps: float = 25.0) -> PlantedU:
    """A canonical detectable episode: 60s decline, DIA bottoms `lead_s` seconds
    before SPY, 60s recovery."""
    return PlantedU(
        decline_start=offset_s,
        spy_bottom=offset_s + 60,
        dia_bottom=offset_s + 60 - lead_s,
        recovery_end=offset_s + 120,
        depth_bps=depth_bps,
    )
