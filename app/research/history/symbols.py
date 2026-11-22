"""Earliest eligible session and effective ticker changes for the fixed panel."""
from datetime import date
import re

LISTED = {
    "ARM": date(2023, 9, 14), "RIVN": date(2021, 11, 10),
    "HOOD": date(2021, 7, 29), "COIN": date(2021, 4, 14),
    "AFRM": date(2021, 1, 13), "XLRE": date(2015, 10, 7),
    "XLC": date(2018, 6, 19),
}
SQ_RENAME = date(2025, 1, 21)
SYMBOL = re.compile(r"[A-Z^][A-Z0-9.^-]{0,11}")


def segments(symbol: str, start: date, end: date):
    """Yield (provider symbol, first session, exclusive last session, asof)."""
    if not SYMBOL.fullmatch(symbol) or start >= end:
        raise ValueError("Invalid symbol or session range")
    start = max(start, LISTED.get(symbol, start))
    if start >= end:
        return
    if symbol == "XYZ" and start < SQ_RENAME:
        yield "SQ", start, min(end, SQ_RENAME), "-"
        start = SQ_RENAME
    if start < end:
        yield symbol, start, end, "-"
