"""Portfolio-level risk accounting and concurrency/exposure caps."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass
class OpenRisk:
    symbol: str
    shares: int
    entry_price: float
    stop_price: float

    @property
    def committed_risk(self) -> float:
        return max(0.0, (self.entry_price - self.stop_price)) * self.shares

    @property
    def notional(self) -> float:
        return self.entry_price * self.shares


def total_committed_risk(open_positions: Sequence[OpenRisk]) -> float:
    return sum(p.committed_risk for p in open_positions)


def total_exposure(open_positions: Sequence[OpenRisk]) -> float:
    return sum(p.notional for p in open_positions)


def can_open_new_position(
    open_positions: Sequence[OpenRisk],
    new_notional: float,
    cfg_risk: dict,
) -> tuple[bool, str]:
    """Portfolio gate for a new position (before sizing binds the final share count)."""
    max_positions = int(cfg_risk.get("max_concurrent_positions", 5))
    max_exposure = float(cfg_risk.get("max_portfolio_exposure", 100_000))

    if len(open_positions) >= max_positions:
        return False, f"max_concurrent_positions_reached({max_positions})"

    if total_exposure(open_positions) + new_notional > max_exposure:
        return False, "portfolio_exposure_cap_exceeded"

    return True, "ok"
