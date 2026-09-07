"""Quote provenance for evaluation. Missing evidence is never replaced with now."""

import math
from datetime import datetime


def parse_quote_timestamp(value) -> datetime | None:
    try:
        ts = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return ts if ts.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def quote_is_fresh(timestamp, now: datetime, max_age_seconds: float = 60.0) -> bool:
    ts = parse_quote_timestamp(timestamp)
    return ts is not None and now.tzinfo is not None and 0 <= (now - ts).total_seconds() <= max_age_seconds


def valid_quote(bid: float, ask: float) -> bool:
    return math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask


def fill_evidence_valid(*, bid, ask, timestamp, feed, now) -> bool:
    # Indicative prices and automatic, unreported feed selection cannot prove a fill.
    return valid_quote(bid, ask) and feed == "opra" and quote_is_fresh(timestamp, now)
