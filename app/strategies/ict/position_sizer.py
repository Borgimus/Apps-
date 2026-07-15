"""
Position sizer for the ICT strategy.

Calculates position size (shares / contracts) from account parameters and
the risk defined by the entry / stop-loss spread.

Formula
───────
  risk_amount   = account_size * risk_pct
  dollar_risk   = |entry - stop_loss|    (per unit)
  position_size = risk_amount / dollar_risk

The caller decides what "unit" means (share, contract, lot).  For futures
contracts a point_value multiplier is supported.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SizeResult:
    position_size: float   # number of units (contracts / shares)
    risk_amount: float     # dollars at risk
    dollar_risk_per_unit: float
    account_size: float
    risk_pct: float

    def to_dict(self) -> dict:
        return {
            "position_size": round(self.position_size, 4),
            "risk_amount": round(self.risk_amount, 2),
            "dollar_risk_per_unit": round(self.dollar_risk_per_unit, 4),
            "account_size": self.account_size,
            "risk_pct": self.risk_pct,
        }


class PositionSizer:
    """
    Compute position size from account parameters.

    Parameters
    ----------
    account_size : float
        Total account equity in dollars.
    risk_pct : float
        Fraction of account to risk per trade (e.g. 0.01 = 1 %).
    point_value : float
        Dollar value of one point move per contract (default 1.0 for stocks).
        For ES futures this would be 50.0.
    min_size : float
        Minimum position size (floor).
    max_size : float | None
        Optional position size cap.
    """

    def __init__(
        self,
        account_size: float,
        risk_pct: float,
        point_value: float = 1.0,
        min_size: float = 1.0,
        max_size: float | None = None,
    ):
        if account_size <= 0:
            raise ValueError("account_size must be positive")
        if not (0 < risk_pct <= 1):
            raise ValueError("risk_pct must be in (0, 1]")
        if point_value <= 0:
            raise ValueError("point_value must be positive")

        self.account_size = account_size
        self.risk_pct = risk_pct
        self.point_value = point_value
        self.min_size = min_size
        self.max_size = max_size

    # ── Public API ────────────────────────────────────────────────────────────

    def calculate(self, entry: float, stop_loss: float) -> SizeResult:
        """
        Compute position size given entry and stop-loss prices.

        Parameters
        ----------
        entry : float      Entry price.
        stop_loss : float  Stop-loss price.

        Returns
        -------
        SizeResult dataclass.

        Raises
        ------
        ValueError if entry == stop_loss (zero risk is invalid).
        """
        dollar_risk_per_unit = abs(entry - stop_loss) * self.point_value
        if dollar_risk_per_unit == 0:
            raise ValueError(
                f"Entry ({entry}) equals stop-loss ({stop_loss}); cannot size position."
            )

        risk_amount = self.account_size * self.risk_pct
        raw_size = risk_amount / dollar_risk_per_unit

        # Apply floor / ceiling
        size = max(self.min_size, raw_size)
        if self.max_size is not None:
            size = min(self.max_size, size)

        # Truncate to whole units (floor for safety)
        size = int(size) if size >= 1 else size

        logger.debug(
            "PositionSizer: entry=%.4f sl=%.4f risk_amt=%.2f size=%.4f",
            entry,
            stop_loss,
            risk_amount,
            size,
        )

        return SizeResult(
            position_size=float(size),
            risk_amount=round(float(size) * dollar_risk_per_unit, 2),
            dollar_risk_per_unit=dollar_risk_per_unit,
            account_size=self.account_size,
            risk_pct=self.risk_pct,
        )

    @classmethod
    def from_config(cls, config) -> "PositionSizer":
        """Construct from ICTConfig."""
        return cls(
            account_size=config.account_size,
            risk_pct=config.risk_per_trade_pct,
        )
