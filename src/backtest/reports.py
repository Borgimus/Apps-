"""Result breakdowns by bucket (regime, liquidity, scan agreement, stop width, component).

Each trade may carry a ``tags`` mapping (attached by the caller from the decision record) so
results can be sliced without the engine needing to know about regimes or agreement modes.
"""
from __future__ import annotations

from typing import Callable, Sequence

from src.backtest.engine import Trade
from src.backtest.metrics import Metrics, compute_metrics


def stop_width_bucket(trade: Trade, edges: Sequence[float] = (2.0, 5.0, 10.0)) -> str:
    """Bucket a trade by initial stop distance percent."""
    if trade.entry_price <= 0:
        return "unknown"
    pct = (trade.entry_price - trade.initial_stop) / trade.entry_price * 100.0
    lo = 0.0
    for e in edges:
        if pct <= e:
            return f"{lo:g}-{e:g}%"
        lo = e
    return f">{edges[-1]:g}%"


def breakdown(trades: Sequence[Trade], key_fn: Callable[[Trade], str]) -> dict[str, Metrics]:
    """Group trades by ``key_fn`` and compute metrics per bucket."""
    buckets: dict[str, list[Trade]] = {}
    for t in trades:
        buckets.setdefault(key_fn(t), []).append(t)
    return {k: compute_metrics(v, n_signals=len(v)) for k, v in buckets.items()}


def by_stop_width(trades: Sequence[Trade]) -> dict[str, Metrics]:
    return breakdown(trades, stop_width_bucket)


def by_tag(trades: Sequence[Trade], tag: str, tags_of: Callable[[Trade], dict]) -> dict[str, Metrics]:
    """Group by an arbitrary decision-record tag (e.g. 'agreement', 'regime', 'liquidity')."""
    return breakdown(trades, lambda t: str(tags_of(t).get(tag, "unknown")))
