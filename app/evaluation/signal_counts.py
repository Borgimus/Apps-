"""Separate polling observations from distinct entry signals and diagnostics."""

import math
from datetime import timedelta


def is_diagnostic(row) -> bool:
    return (row.exact_block_reason or "").endswith("_diagnostic_only")


def distinct_entry_signals(rows):
    """Use observation time minus signal age to recover the strategy timestamp.

    Keep the earliest observation of each symbol/strategy/direction/timestamp.
    Missing ages cannot establish identity, so those rows remain separate.
    This counts strategy events; it does not assert independence or eligibility.
    """
    seen = set()
    result = []
    for row in sorted(rows, key=lambda r: (r.timestamp, r.id or 0)):
        if is_diagnostic(row):
            continue
        age = row.signal_age_seconds
        if isinstance(age, (int, float)) and math.isfinite(age) and age >= 0:
            try:
                signal_time = (row.timestamp - timedelta(seconds=age)).replace(microsecond=0)
            except OverflowError:
                signal_time = ("unknown", id(row))
        else:
            signal_time = ("unknown", id(row))
        key = (row.strategy_id, row.symbol, row.signal_direction, signal_time)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result
