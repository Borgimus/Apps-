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


def scaled_guardrails_active(settings) -> bool:
    """Return whether the amended scaled-paper selection policy is active."""
    return (
        getattr(settings, "paper_scaled_sizing_enabled", False) is True
        and getattr(settings, "paper_scaled_guardrails_enabled", False) is True
    )


def expiration_policy(settings) -> tuple[int, int]:
    """Return the inclusive DTE bounds used by every selection layer."""
    if not scaled_guardrails_active(settings):
        return 0, 365
    return (
        int(getattr(settings, "paper_scaled_min_dte", 2)),
        int(getattr(settings, "paper_scaled_max_dte", 8)),
    )


def select_expiration_for_settings(
    expirations: Iterable[date],
    today: date,
    settings,
) -> Optional[date]:
    """Apply one expiration policy across scan, shadow, and order paths."""
    available = list(expirations)
    min_dte, max_dte = expiration_policy(settings)
    if scaled_guardrails_active(settings):
        return select_allowed_expiration(
            available,
            today,
            settings.options.preferred_dte,
            min_dte,
            max_dte,
        )

    for dte in settings.options.preferred_dte:
        candidate = today + timedelta(days=int(dte))
        if candidate in available:
            return candidate
    future = [expiry for expiry in available if expiry >= today]
    return min(future) if future else None


def liquidity_filter_params(settings) -> dict:
    """Build the shared contract-liquidity policy for all selection layers."""
    strict_delta = (
        scaled_guardrails_active(settings)
        and getattr(settings, "paper_scaled_require_delta", False) is True
    )
    return {
        "min_open_interest": settings.risk.min_open_interest,
        "min_volume": settings.risk.min_volume,
        "max_spread_pct": settings.risk.max_spread_pct,
        "delta_target_min": settings.options.delta_target_min,
        "delta_target_max": settings.options.delta_target_max,
        "require_delta": strict_delta,
        "strict_delta_range": strict_delta,
    }


def scaled_entry_block_reason(settings, symbol: str, strategy_id: str) -> Optional[str]:
    """Return the cohort-level broker-entry veto, if any."""
    if not scaled_guardrails_active(settings):
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
