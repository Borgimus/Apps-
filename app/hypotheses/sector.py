"""Shared sector map and synchronized clock inputs."""
from datetime import datetime, time
import yaml

from app.research.equity_observer import ET, INTERVAL


def sector_map(root):
    doc = yaml.safe_load((root / "config/research/sector_map.yaml").read_text())
    mapping = {}
    for sector, members in doc["sectors"].items():
        for symbol in members:
            if symbol in mapping or symbol in doc["sectors"]:
                raise ValueError(f"Repeated or self-referenced sector match for {symbol}")
            mapping[symbol] = sector
    return mapping


def session_start(day):
    return datetime.combine(day, time(9, 30), ET)


def pair_at(view, symbol, sector, start):
    if start + INTERVAL > view.at:
        return None
    # Some feeds deliver the pair in separate polls. A missing member remains
    # unobserved until its own event, and is never filled from a later event.
    stock = {b.start: b for b in view.bars(symbol)}.get(start)
    peer = {b.start: b for b in view.bars(sector)}.get(start)
    return (stock, peer) if stock is not None and peer is not None else None
