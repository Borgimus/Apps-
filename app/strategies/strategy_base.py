"""
Base class for all trading strategies.

Each strategy receives OHLCV + indicator data and emits Signal objects.
Strategies are decoupled from order placement — they only produce signals.
The paper_trader or live engine consumes signals and decides on execution.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class SignalDirection(str, Enum):
    LONG = "long"    # buy call / sell put
    SHORT = "short"  # buy put / sell call
    EXIT = "exit"    # close existing position
    NONE = "none"    # no signal


@dataclass
class Signal:
    strategy_id: str
    symbol: str
    direction: SignalDirection
    timestamp: datetime
    price: float                        # underlying price at signal time
    confidence: float = 0.5             # 0-1, used for position sizing
    notes: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_actionable(self) -> bool:
        return self.direction not in (SignalDirection.NONE, SignalDirection.EXIT)


class StrategyBase(abc.ABC):
    """
    All strategies must implement generate_signals().

    Signals are generated from historical bar data (research/backtest) or
    from a live bar snapshot (paper/live trading).  The strategy must NOT
    place orders — it only emits Signal objects.
    """

    def __init__(self, strategy_id: str, params: Dict[str, Any] | None = None):
        self.strategy_id = strategy_id
        self.params = params or {}
        self.logger = logging.getLogger(f"strategy.{strategy_id}")

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""

    @property
    def symbols(self) -> List[str]:
        """Symbols this strategy trades.  Override to restrict."""
        return self.params.get("symbols", [])

    @abc.abstractmethod
    def generate_signals(self, bars: pd.DataFrame, symbol: str) -> List[Signal]:
        """
        Generate signals from OHLCV bars.

        Parameters
        ----------
        bars : DataFrame with columns [open, high, low, close, volume] and
               a DatetimeIndex (UTC or timezone-aware).
        symbol : ticker symbol being analysed.

        Returns list of Signal (may be empty).
        """

    @property
    def min_bars_required(self) -> int:
        """Minimum bars needed before this strategy can generate signals."""
        return 2

    def get_readiness_info(self, bars: pd.DataFrame) -> dict:
        """Return readiness diagnostics for this strategy."""
        n = len(bars) if bars is not None and not bars.empty else 0
        required = self.min_bars_required
        ready = n >= required
        return {
            "strategy_id": self.strategy_id,
            "min_bars_required": required,
            "bars_available": n,
            "ready": ready,
            "reason": "sufficient bars" if ready else f"need {required - n} more bars",
        }

    def validate_bars(self, bars: pd.DataFrame, min_rows: int = 2) -> bool:
        """Return True if bars are sufficient to run this strategy."""
        required = {"open", "high", "low", "close", "volume"}
        if bars is None or bars.empty:
            self.logger.warning("Empty bars for %s", self.strategy_id)
            return False
        missing = required - set(bars.columns.str.lower())
        if missing:
            self.logger.warning("Missing columns %s for %s", missing, self.strategy_id)
            return False
        if len(bars) < min_rows:
            self.logger.warning(
                "Insufficient bars (%d < %d) for %s", len(bars), min_rows, self.strategy_id
            )
            return False
        return True

    def __repr__(self) -> str:
        return f"<Strategy id={self.strategy_id} name={self.name}>"
