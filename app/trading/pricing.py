"""
Limit price computation for option buy-to-open orders.

Modes
-----
bid              - bid price (passive; unlikely to fill quickly)
mid              - midpoint (standard default)
ask              - ask price (aggressive; fills when a seller exists at ask)
marketable_limit - ask + spread * offset_pct (sweeps the offer; fills like a market order)

For marketable_limit the offset_pct is a fraction of the spread width, not the
ask price.  E.g. bid=1.00 ask=1.20 offset_pct=0.01 → limit = 1.20 + 0.002 = 1.202 ≈ 1.20.
At typical option spreads a 1% spread-width offset adds $0.00-$0.02 which is
enough to sweep the ask queue without significantly worsening the fill price.
"""

from __future__ import annotations


def compute_limit_price(
    mode: str,
    bid: float,
    ask: float,
    offset_pct: float = 0.01,
) -> float:
    """
    Return a limit price (rounded to 2 decimal places) for a BUY order.

    Parameters
    ----------
    mode        : "bid" | "mid" | "ask" | "marketable_limit"
    bid         : current best bid
    ask         : current best ask
    offset_pct  : fraction of spread width added above the ask in
                  marketable_limit mode (default 1 %)
    """
    if mode == "bid":
        return round(bid, 2)
    elif mode == "mid":
        return round((bid + ask) / 2, 2)
    elif mode == "ask":
        return round(ask, 2)
    elif mode == "marketable_limit":
        spread = ask - bid
        return round(ask + spread * offset_pct, 2)
    # Unknown mode → fall back to mid
    return round((bid + ask) / 2, 2)
