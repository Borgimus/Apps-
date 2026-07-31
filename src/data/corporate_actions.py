"""Corporate-action and data-integrity detection: splits, symbol changes, delistings,
trading halts, and stale data.

These are heuristics that flag events for deterministic handling and operator review — they
never silently rewrite prices. Research uses adjusted bars where appropriate while unadjusted
prices are preserved to reconstruct actual orders (see docs/db_schema.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

# Common split ratios to snap a suspected factor to (forward and reverse).
_KNOWN_RATIOS = [2, 3, 4, 5, 6, 7, 8, 10, 15, 20]


class ActionKind(str, Enum):
    SPLIT = "SPLIT"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    DELISTING = "DELISTING"
    HALT = "HALT"
    STALE = "STALE"


@dataclass
class CorporateActionEvent:
    kind: ActionKind
    symbol: str
    detail: str
    factor: float | None = None


def suspected_split(prev_close: float, today_open: float, tolerance: float = 0.05
                    ) -> CorporateActionEvent | None:
    """Flag a suspected split when the overnight price change snaps to a known ratio.

    A ~2:1 forward split roughly halves the price (factor 2 -> ratio ≈ 0.5); a 1:10 reverse
    split multiplies it ~10x. ``tolerance`` is the fractional distance allowed to a known ratio.
    Ordinary gaps (a few percent) are ignored.
    """
    if prev_close <= 0 or today_open <= 0:
        return None
    ratio = prev_close / today_open  # >1 forward split, <1 reverse split

    for r in _KNOWN_RATIOS:
        # Forward split: price divided by r.
        if abs(ratio - r) <= r * tolerance:
            return CorporateActionEvent(ActionKind.SPLIT, "", f"suspected {r}:1 forward split",
                                        factor=float(r))
        # Reverse split: price multiplied by r.
        if abs((1 / ratio) - r) <= r * tolerance:
            return CorporateActionEvent(ActionKind.SPLIT, "", f"suspected 1:{r} reverse split",
                                        factor=1.0 / r)
    return None


def detect_split_from_adjustment(raw_ratio: float, adj_ratio: float, tolerance: float = 0.02
                                 ) -> CorporateActionEvent | None:
    """Detect a split by disagreement between the raw and adjusted close ratios.

    ``raw_ratio`` = raw_close_t / raw_close_{t-1}; ``adj_ratio`` = adj equivalent.
    If adjusted and raw disagree materially, an adjustment (split/large dividend) occurred.
    """
    if raw_ratio <= 0 or adj_ratio <= 0:
        return None
    factor = raw_ratio / adj_ratio
    if abs(factor - 1.0) <= tolerance:
        return None
    return CorporateActionEvent(ActionKind.SPLIT, "",
                                f"raw/adjusted ratio disagreement (factor {factor:.3f})",
                                factor=factor)


def detect_symbol_change(old_symbol: str, new_symbol: str) -> CorporateActionEvent | None:
    if old_symbol and new_symbol and old_symbol.upper() != new_symbol.upper():
        return CorporateActionEvent(ActionKind.SYMBOL_CHANGE, new_symbol.upper(),
                                    f"{old_symbol.upper()} -> {new_symbol.upper()}")
    return None


def detect_delisting(*, has_recent_bars: bool, tradable: bool, symbol: str
                     ) -> CorporateActionEvent | None:
    """Flag a likely delisting when the broker marks the asset untradable or no recent bars exist."""
    if not tradable or not has_recent_bars:
        reason = "asset not tradable" if not tradable else "no recent bars"
        return CorporateActionEvent(ActionKind.DELISTING, symbol, reason)
    return None


def detect_halt(*, halted_flag: bool, symbol: str) -> CorporateActionEvent | None:
    if halted_flag:
        return CorporateActionEvent(ActionKind.HALT, symbol, "trading halted")
    return None


def detect_stale(last_bar_ts: datetime, now: datetime, max_age: timedelta, symbol: str
                 ) -> CorporateActionEvent | None:
    if last_bar_ts.tzinfo is None or now.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    age = now.astimezone(timezone.utc) - last_bar_ts.astimezone(timezone.utc)
    if age > max_age:
        return CorporateActionEvent(ActionKind.STALE, symbol,
                                    f"latest bar {age.total_seconds():.0f}s old")
    return None
