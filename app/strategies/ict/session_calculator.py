"""
Session-level calculator.

Given a DataFrame of 1-minute OHLCV bars with a UTC-aware DatetimeIndex,
computes the high and low of the Asian and London sessions for each trading day.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .config import ICTConfig, SessionWindow

logger = logging.getLogger(__name__)


@dataclass
class SessionLevels:
    """High/low levels for one trading day's sessions."""

    date: str                        # YYYY-MM-DD
    timestamp: datetime              # UTC datetime of computation / session close

    asian_high: Optional[float]
    asian_low: Optional[float]
    london_high: Optional[float]
    london_low: Optional[float]

    def is_complete(self) -> bool:
        return all(
            v is not None
            for v in (self.asian_high, self.asian_low, self.london_high, self.london_low)
        )

    def contains_price(self, price: float, session: str = "asian") -> bool:
        """Return True if price is inside the named session range."""
        if session == "asian":
            hi, lo = self.asian_high, self.asian_low
        elif session == "london":
            hi, lo = self.london_high, self.london_low
        else:
            raise ValueError(f"Unknown session: {session}")
        if hi is None or lo is None:
            return False
        return lo <= price <= hi

    def session_range(self, session: str = "asian") -> Optional[float]:
        if session == "asian":
            hi, lo = self.asian_high, self.asian_low
        else:
            hi, lo = self.london_high, self.london_low
        if hi is None or lo is None:
            return None
        return hi - lo

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "timestamp": self.timestamp.isoformat(),
            "asian_high": self.asian_high,
            "asian_low": self.asian_low,
            "london_high": self.london_high,
            "london_low": self.london_low,
        }


class SessionCalculator:
    """
    Compute Asian and London session high/low from 1-minute OHLCV bars.

    Parameters
    ----------
    config : ICTConfig
        Strategy configuration; session windows are read from it.
    """

    def __init__(self, config: ICTConfig):
        self._cfg = config

    # ── Public API ────────────────────────────────────────────────────────────

    def compute_sessions(self, bars: pd.DataFrame) -> List[SessionLevels]:
        """
        Compute session levels for every calendar day present in bars.

        Parameters
        ----------
        bars : pd.DataFrame
            1-minute OHLCV with UTC-aware DatetimeIndex.
            Required columns: high, low

        Returns
        -------
        List[SessionLevels] sorted ascending by date.
        """
        bars = self._normalise(bars)
        if bars.empty:
            return []

        results: List[SessionLevels] = []

        # Group by UTC date
        for date_str, day_bars in bars.groupby(bars.index.date):
            asian = self._session_hl(day_bars, self._cfg.asian_session)
            london = self._session_hl(day_bars, self._cfg.london_session)

            ts = pd.Timestamp(str(date_str)).tz_localize("UTC")

            levels = SessionLevels(
                date=str(date_str),
                timestamp=ts.to_pydatetime(),
                asian_high=asian[0],
                asian_low=asian[1],
                london_high=london[0],
                london_low=london[1],
            )
            results.append(levels)

        results.sort(key=lambda x: x.date)
        logger.debug("Computed session levels for %d days", len(results))
        return results

    def get_current_session_levels(
        self, bars: pd.DataFrame, as_of: Optional[datetime] = None
    ) -> Optional[SessionLevels]:
        """
        Return the most recent fully-available session levels.

        as_of defaults to the last bar's timestamp.
        """
        all_levels = self.compute_sessions(bars)
        if not all_levels:
            return None

        if as_of is None:
            return all_levels[-1]

        # Find the latest day whose session close is <= as_of
        as_of_date = as_of.date() if hasattr(as_of, "date") else as_of
        for levels in reversed(all_levels):
            import datetime as dt
            lev_date = dt.date.fromisoformat(levels.date)
            if lev_date <= as_of_date:
                return levels
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _session_hl(
        self, day_bars: pd.DataFrame, window: SessionWindow
    ) -> Tuple[Optional[float], Optional[float]]:
        """Return (high, low) for bars falling inside the session window."""
        mask = day_bars.index.hour.map(
            lambda h: window.contains_hour(h)
        )
        session_bars = day_bars[mask]
        if session_bars.empty:
            return None, None
        high = float(session_bars["high"].max())
        low = float(session_bars["low"].min())
        return high, low

    @staticmethod
    def _normalise(bars: pd.DataFrame) -> pd.DataFrame:
        """Ensure column names are lower-case and index is UTC-aware."""
        df = bars.copy()
        df.columns = [c.lower() for c in df.columns]
        if not hasattr(df.index, "tz") or df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        elif str(df.index.tz) != "UTC":
            df.index = df.index.tz_convert("UTC")
        return df
