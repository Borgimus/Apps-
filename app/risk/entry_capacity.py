"""Pure helpers for enforcing global active-entry capacity."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional


def _symbols(values: Iterable[Optional[str]]) -> set[str]:
    return {
        value.strip()
        for value in values
        if isinstance(value, str) and value.strip()
    }


def reserved_entry_slots(
    open_option_symbols: Iterable[Optional[str]],
    pending_option_symbols: Iterable[Optional[str]],
) -> int:
    """Count unique option positions that are open or awaiting entry fill.

    De-duplication matters for partial fills because the same option symbol is
    simultaneously open in PositionManager and pending in FillTracker.
    """
    return len(_symbols(open_option_symbols) | _symbols(pending_option_symbols))


def has_entry_capacity(
    open_option_symbols: Iterable[Optional[str]],
    pending_option_symbols: Iterable[Optional[str]],
    max_active_positions: int,
) -> bool:
    """Return whether another entry may be submitted, failing closed on bad caps."""
    try:
        cap = int(max_active_positions)
    except (TypeError, ValueError):
        return False
    if cap < 1:
        return False
    return reserved_entry_slots(open_option_symbols, pending_option_symbols) < cap
