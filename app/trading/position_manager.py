"""Position manager for open option positions.

Tracks each open position and evaluates exit conditions on every price update:
  - Stop loss   : exit if price drops stop_loss_pct from entry
  - Take profit : exit if price rises take_profit_pct from entry
  - Trailing stop: after activation, exit if price drops trailing_stop_pct from peak
  - Max hold    : exit after max_hold_minutes regardless of price
  - EOD exit    : force-close at eod_exit_time ET before market close

Also manages:
  - Dedup guard  : rejects a new signal if a position for the same underlying is open
  - Loss cooldown: blocks new entries after a losing trade is closed
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def _numeric_setting(settings: object, name: str, default: float) -> float:
    """Read a real numeric setting without accepting MagicMock-like placeholders."""
    value = getattr(settings, name, None)
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return float(default)


@dataclass
class OpenPosition:
    option_symbol: str
    symbol: str
    strategy_id: str
    direction: str
    entry_time: datetime
    entry_price: float
    quantity: int
    current_price: float = 0.0
    peak_price: float = 0.0
    trough_price: float = 0.0
    stop_loss_pct: float = 0.50
    take_profit_pct: float = 1.00
    trailing_stop_pct: float = 0.25
    trailing_activation_pct: float = 0.25
    trailing_stop_armed: bool = False
    max_hold_minutes: int = 120
    eod_exit_time: time = time(15, 45)
    journal_id: Optional[int] = None

    # Exit state machine (EXIT_PENDING)
    exit_pending: bool = False
    exit_order_id: Optional[str] = None
    exit_order_limit_price: float = 0.0
    exit_order_placed_at: Optional[datetime] = None
    exit_triggered_reason: Optional[str] = None
    exit_is_mandatory: bool = False
    exit_quote_bid: Optional[float] = None
    confirmed_fill_qty: int = 0
    confirmed_fill_value: float = 0.0

    def __post_init__(self) -> None:
        if self.current_price == 0.0:
            self.current_price = self.entry_price
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price
        if self.trough_price == 0.0:
            self.trough_price = self.entry_price
        if self.peak_price >= self.trailing_activation_price:
            self.trailing_stop_armed = True

    @property
    def trailing_activation_price(self) -> float:
        return self.entry_price * (1.0 + self.trailing_activation_pct)

    @property
    def trailing_stop_level(self) -> Optional[float]:
        if not self.trailing_stop_armed:
            return None
        return self.peak_price * (1.0 - self.trailing_stop_pct)


class PositionManager:
    """Stateful manager for open positions within one trading session."""

    def __init__(self, settings: Settings | None = None):
        self._s = (settings or get_settings()).position
        self._positions: Dict[str, OpenPosition] = {}
        self._last_loss_time: Optional[datetime] = None

    # ── Query ─────────────────────────────────────────────────────────────────

    def has_position(self, option_symbol: str) -> bool:
        return option_symbol in self._positions

    def get_position(self, option_symbol: str) -> Optional[OpenPosition]:
        return self._positions.get(option_symbol)

    def has_position_for_symbol(self, symbol: str) -> bool:
        return any(
            position.symbol == symbol
            for position in self._positions.values()
        )

    def open_positions(self) -> List[OpenPosition]:
        return list(self._positions.values())

    def is_in_cooldown(self, now: datetime) -> bool:
        if self._last_loss_time is None:
            return False
        elapsed = (now - self._last_loss_time).total_seconds() / 60
        return elapsed < self._s.cooldown_after_loss_minutes

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def open(
        self,
        option_symbol: str,
        symbol: str,
        strategy_id: str,
        direction: str,
        entry_time: datetime,
        entry_price: float,
        quantity: int,
        journal_id: Optional[int] = None,
    ) -> OpenPosition:
        """Open a new position using configured exit thresholds."""
        hour, minute = map(int, self._s.eod_exit_time.split(":"))

        activation_default = _numeric_setting(
            self._s,
            "trailing_activation_pct",
            0.25,
        )
        activation_pct = float(
            os.getenv(
                "POSITION_TRAILING_ACTIVATION_PCT",
                str(activation_default),
            )
        )
        activation_pct = max(0.0, activation_pct)

        position = OpenPosition(
            option_symbol=option_symbol,
            symbol=symbol,
            strategy_id=strategy_id,
            direction=direction,
            entry_time=entry_time,
            entry_price=entry_price,
            quantity=quantity,
            peak_price=entry_price,
            trough_price=entry_price,
            stop_loss_pct=self._s.stop_loss_pct,
            take_profit_pct=self._s.take_profit_pct,
            trailing_stop_pct=self._s.trailing_stop_pct,
            trailing_activation_pct=activation_pct,
            max_hold_minutes=self._s.max_hold_minutes,
            eod_exit_time=time(hour, minute),
            journal_id=journal_id,
        )
        self._positions[option_symbol] = position
        logger.info(
            "Position opened | %s | entry=%.4f | qty=%d | "
            "trail_activation=+%.1f%%",
            option_symbol,
            entry_price,
            quantity,
            activation_pct * 100,
        )
        return position

    def update_price(self, option_symbol: str, current_price: float) -> None:
        """Update current, peak, trough, and trailing-stop activation state."""
        position = self._positions.get(option_symbol)
        if position is None:
            return

        position.current_price = current_price
        if current_price > position.peak_price:
            position.peak_price = current_price
        if current_price < position.trough_price:
            position.trough_price = current_price

        if (
            not position.trailing_stop_armed
            and position.peak_price >= position.trailing_activation_price
        ):
            position.trailing_stop_armed = True
            logger.info(
                "Trailing stop armed | %s | peak=%.4f | activation=%.4f | "
                "trail=%.1f%%",
                option_symbol,
                position.peak_price,
                position.trailing_activation_price,
                position.trailing_stop_pct * 100,
            )

    def should_exit(
        self,
        option_symbol: str,
        current_price: float,
        now: datetime,
    ) -> Optional[str]:
        """Return an exit reason when a configured condition is met."""
        position = self._positions.get(option_symbol)
        if position is None:
            return None
        if position.exit_pending:
            return None

        if current_price <= position.entry_price * (
            1.0 - position.stop_loss_pct
        ):
            return "stop_loss"

        if current_price >= position.entry_price * (
            1.0 + position.take_profit_pct
        ):
            return "take_profit"

        trailing_level = position.trailing_stop_level
        if (
            position.trailing_stop_armed
            and trailing_level is not None
            and current_price <= trailing_level
        ):
            return "trailing_stop"

        hold_minutes = (
            now - position.entry_time
        ).total_seconds() / 60.0
        if hold_minutes >= position.max_hold_minutes:
            return "max_hold"

        now_et = (
            now.astimezone(ET)
            if now.tzinfo
            else now.replace(tzinfo=ET)
        )
        if now_et.time() >= position.eod_exit_time:
            return "eod_exit"

        return None

    def close(
        self,
        option_symbol: str,
        exit_price: float,
        pnl: float,
    ) -> Optional[OpenPosition]:
        """Remove a position and update cooldown state after a loss."""
        position = self._positions.pop(option_symbol, None)
        if position is None:
            return None
        if pnl < 0:
            self._last_loss_time = datetime.now(tz=ET)
            logger.info(
                "Loss recorded on %s (pnl=%.2f) — cooldown active for %d min",
                option_symbol,
                pnl,
                self._s.cooldown_after_loss_minutes,
            )
        return position

    # ── EXIT_PENDING state machine ────────────────────────────────────────────

    def mark_exit_pending(
        self,
        option_symbol: str,
        order_id: str,
        limit_price: float,
        reason: str,
        is_mandatory: bool,
        exit_quote_bid: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Record an outstanding exit order and block duplicate exit triggers."""
        position = self._positions.get(option_symbol)
        if position is None:
            return
        position.exit_pending = True
        position.exit_order_id = order_id
        position.exit_order_limit_price = limit_price
        position.exit_order_placed_at = now or datetime.now(tz=ET)
        position.exit_triggered_reason = reason
        position.exit_is_mandatory = is_mandatory
        if exit_quote_bid is not None:
            position.exit_quote_bid = exit_quote_bid
        logger.info(
            "Exit pending | %s | order=%s | limit=%.4f | "
            "reason=%s | mandatory=%s",
            option_symbol,
            order_id[:8] if order_id else "none",
            limit_price,
            reason,
            is_mandatory,
        )

    def clear_exit_pending(self, option_symbol: str) -> None:
        """Clear EXIT_PENDING state so the position can be re-evaluated."""
        position = self._positions.get(option_symbol)
        if position is None:
            return
        position.exit_pending = False
        position.exit_order_id = None
        position.exit_order_limit_price = 0.0
        position.exit_order_placed_at = None
        position.exit_triggered_reason = None
        position.exit_is_mandatory = False
        logger.info("Exit pending cleared | %s", option_symbol)

    def record_partial_fill(
        self,
        option_symbol: str,
        filled_qty: int,
        filled_price: float,
    ) -> None:
        """Accumulate broker-confirmed exit fills."""
        position = self._positions.get(option_symbol)
        if position is None:
            return
        position.confirmed_fill_qty += filled_qty
        position.confirmed_fill_value += filled_qty * filled_price
        logger.info(
            "Partial fill | %s | +%d @ %.4f | total_confirmed=%d",
            option_symbol,
            filled_qty,
            filled_price,
            position.confirmed_fill_qty,
        )

    def close_confirmed(
        self,
        option_symbol: str,
        exit_price: float,
        pnl: float,
    ) -> Optional[OpenPosition]:
        """Close using a broker-confirmed fill price."""
        position = self._positions.pop(option_symbol, None)
        if position is None:
            return None
        if pnl < 0:
            self._last_loss_time = datetime.now(tz=ET)
            logger.info(
                "Loss recorded on %s (pnl=%.2f) — cooldown active for %d min",
                option_symbol,
                pnl,
                self._s.cooldown_after_loss_minutes,
            )
        logger.info(
            "Position closed (confirmed) | %s | exit=%.4f | pnl=%.2f",
            option_symbol,
            exit_price,
            pnl,
        )
        return position

    def to_dict_list(self) -> list:
        """Serialize all open positions for the dashboard API."""
        now = datetime.now(tz=ET)
        result = []
        for position in self._positions.values():
            current = (
                position.current_price
                if position.current_price > 0
                else position.entry_price
            )
            unrealized_pnl = round(
                (current - position.entry_price)
                * 100
                * position.quantity,
                2,
            )
            entry_et = (
                position.entry_time.replace(tzinfo=ET)
                if position.entry_time.tzinfo is None
                else position.entry_time.astimezone(ET)
            )
            hold_minutes = round(
                (now - entry_et).total_seconds() / 60,
                1,
            )
            trailing_level = position.trailing_stop_level
            result.append(
                {
                    "option_symbol": position.option_symbol,
                    "symbol": position.symbol,
                    "strategy_id": position.strategy_id,
                    "direction": position.direction,
                    "entry_time": position.entry_time.isoformat(),
                    "entry_price": position.entry_price,
                    "current_price": current,
                    "peak_price": position.peak_price,
                    "trough_price": position.trough_price,
                    "peak_pnl": round(
                        (position.peak_price - position.entry_price)
                        * 100
                        * position.quantity,
                        2,
                    ),
                    "trough_pnl": round(
                        (position.trough_price - position.entry_price)
                        * 100
                        * position.quantity,
                        2,
                    ),
                    "quantity": position.quantity,
                    "unrealized_pnl": unrealized_pnl,
                    "stop_loss_level": round(
                        position.entry_price
                        * (1.0 - position.stop_loss_pct),
                        4,
                    ),
                    "take_profit_level": round(
                        position.entry_price
                        * (1.0 + position.take_profit_pct),
                        4,
                    ),
                    "trailing_stop_level": (
                        round(trailing_level, 4)
                        if trailing_level is not None
                        else None
                    ),
                    "trailing_activation_level": round(
                        position.trailing_activation_price,
                        4,
                    ),
                    "trailing_stop_armed": position.trailing_stop_armed,
                    "stop_loss_pct": position.stop_loss_pct,
                    "take_profit_pct": position.take_profit_pct,
                    "trailing_stop_pct": position.trailing_stop_pct,
                    "trailing_activation_pct": (
                        position.trailing_activation_pct
                    ),
                    "max_hold_minutes": position.max_hold_minutes,
                    "eod_exit_time": position.eod_exit_time.strftime("%H:%M"),
                    "hold_minutes": hold_minutes,
                    "exit_pending": position.exit_pending,
                    "exit_order_id": position.exit_order_id,
                    "exit_triggered_reason": (
                        position.exit_triggered_reason
                    ),
                    "exit_is_mandatory": position.exit_is_mandatory,
                    "confirmed_fill_qty": position.confirmed_fill_qty,
                }
            )
        return result
