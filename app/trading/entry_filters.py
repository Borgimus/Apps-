"""Fail-closed entry filters for the amended scaled-paper cohort."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence

import pandas as pd


def csv_set(value: str) -> set[str]:
    """Return normalized, non-empty comma-separated tokens."""
    return {token.strip().lower() for token in str(value).split(",") if token.strip()}


def completed_intraday_bars(
    bars: pd.DataFrame,
    now: datetime,
    interval_minutes: int = 5,
) -> pd.DataFrame:
    """Drop a still-forming final bar whose timestamp marks interval start."""
    if bars.empty or not isinstance(bars.index, pd.DatetimeIndex):
        return bars

    index = bars.index
    cutoff = pd.Timestamp(now) - pd.Timedelta(minutes=interval_minutes)
    if index.tz is not None:
        if cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize(index.tz)
        else:
            cutoff = cutoff.tz_convert(index.tz)
    elif cutoff.tzinfo is not None:
        cutoff = cutoff.tz_localize(None)
    return bars.loc[index <= cutoff]


def select_allowed_expiration(
    expirations: Iterable[date],
    today: date,
    preferred_dte: Sequence[int],
    min_dte: int,
    max_dte: int,
) -> Optional[date]:
    """Select only an expiration inside the configured inclusive DTE window."""
    available = set(expirations)
    allowed = sorted(
        expiry
        for expiry in available
        if min_dte <= (expiry - today).days <= max_dte
    )
    if not allowed:
        return None

    for dte in preferred_dte:
        if not min_dte <= int(dte) <= max_dte:
            continue
        candidate = today + timedelta(days=int(dte))
        if candidate in available:
            return candidate
    return allowed[0]


def scaled_entry_block_reason(settings, symbol: str, strategy_id: str) -> Optional[str]:
    """Return the cohort-level broker-entry veto, if any."""
    if not (
        getattr(settings, "paper_scaled_sizing_enabled", False) is True
        and getattr(settings, "paper_scaled_guardrails_enabled", False) is True
    ):
        return None

    blocked_symbols = csv_set(
        getattr(settings, "paper_scaled_blocked_symbols", "")
    )
    if symbol.strip().lower() in blocked_symbols:
        return "symbol_disabled"

    shadow_only = csv_set(
        getattr(settings, "paper_scaled_shadow_only_strategies", "")
    )
    if strategy_id.strip().lower() in shadow_only:
        return "strategy_shadow_only"
    return None
