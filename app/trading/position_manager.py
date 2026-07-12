"""
Position manager for open option positions.

Tracks each open position and evaluates exit conditions on every price update:
  - Stop loss   : exit if price drops stop_loss_pct from entry
  - Take profit  : exit if price rises take_profit_pct from entry
  - Trailing stop: exit if price drops trailing_stop_pct from peak
  - Max hold     : exit after max_hold_minutes regardless of price
  - EOD exit     : force-close at eod_exit_time ET before market close

Also manages:
  - Dedup guard  : rejects a new signal if a position for the same
                   underlying is already open
  - Loss cooldown: blocks new entries for cooldown_after_loss_minutes
                   after a losing trade is closed
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


@dataclass
class OpenPosition:
    option_symbol: str
    symbol: str              # underlying
    strategy_id: str
    direction: str           # "LONG" or "SHORT"
    entry_time: datetime
    entry_price: float       # option premium paid (per share, ×100 = contract cost)
    quantity: int
    current_price: float = 0.0  # most recent price from broker quote poll
    peak_price: float = 0.0     # highest price seen since entry (for trailing stop)
    trough_price: float = 0.0   # lowest price seen since entry (for drawdown reporting)
    stop_loss_pct: float = 0.50
    take_profit_pct: float = 1.00
    trailing_stop_pct: float = 0.25
    max_hold_minutes: int = 120
    eod_exit_time: time = time(15, 45)
    journal_id: Optional[int] = None   # DB row id for update-on-exit

    # Exit state machine (EXIT_PENDING)
    exit_pending: bool = False
    exit_order_id: Optional[str] = None
    exit_order_limit_price: float = 0.0
    exit_order_placed_at: Optional[datetime] = None
    exit_triggered_reason: Optional[str] = None
    exit_is_mandatory: bool = False
    exit_quote_bid: Optional[float] = None   # pre-order bid for slippage analysis
    confirmed_fill_qty: int = 0              # broker-confirmed fills accumulated
    confirmed_fill_value: float = 0.0        # sum(qty × price) for weighted avg

    def __post_init__(self):
        if self.current_price == 0.0:
            self.current_price = self.entry_price
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price
        if self.trough_price == 0.0:
            self.trough_price = self.entry_price


class PositionManager:
    """
    Stateful manager for open positions within one trading session.

    Thread-safety: single-threaded; asyncio is fine since all callers are coroutines.
    """

    def __init__(self, settings: Settings | None = None):
        self._s = (settings or get_settings()).position
        self._positions: Dict[str, OpenPosition] = {}   # keyed by option_symbol
        self._last_loss_time: Optional[datetime] = None

    # ── Query ─────────────────────────────────────────────────────────────────

    def has_position(self, option_symbol: str) -> bool:
        return option_symbol in self._positions

    def get_position(self, option_symbol: str) -> Optional["OpenPosition"]:
        return self._positions.get(option_symbol)

    def has_position_for_symbol(self, symbol: str) -> bool:
        return any(p.symbol == symbol for p in self._positions.values())

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
        h, m = map(int, self._s.eod_exit_time.split(":"))
        pos = OpenPosition(
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
            max_hold_minutes=self._s.max_hold_minutes,
            eod_exit_time=time(h, m),
            journal_id=journal_id,
        )
        self._positions[option_symbol] = pos
        logger.info(
            "Position opened | %s | entry=%.4f | qty=%d",
            option_symbol, entry_price, quantity,
        )
        return pos

    def update_price(self, option_symbol: str, current_price: float):
        """Update current, peak, and trough price for trailing stop and unrealized PnL."""
        pos = self._positions.get(option_symbol)
        if pos:
            pos.current_price = current_price
            if current_price > pos.peak_price:
                pos.peak_price = current_price
            if current_price < pos.trough_price:
                pos.trough_price = current_price

    def should_exit(
        self,
        option_symbol: str,
        current_price: float,
        now: datetime,
    ) -> Optional[str]:
        """
        Evaluate all exit conditions.

        Returns the exit reason string if position should close, else None.
        """
        pos = self._positions.get(option_symbol)
        if pos is None:
            return None
        if pos.exit_pending:
            return None  # exit order already outstanding — don't re-trigger

        # Stop loss: price fell stop_loss_pct below entry
        if current_price <= pos.entry_price * (1.0 - pos.stop_loss_pct):
            return "stop_loss"

        # Take profit: price rose take_profit_pct above entry
        if current_price >= pos.entry_price * (1.0 + pos.take_profit_pct):
            return "take_profit"

        # Trailing stop: price fell trailing_stop_pct below peak
        if current_price <= pos.peak_price * (1.0 - pos.trailing_stop_pct):
            return "trailing_stop"

        # Max hold time
        hold_mins = (now - pos.entry_time).total_seconds() / 60.0
        if hold_mins >= pos.max_hold_minutes:
            return "max_hold"

        # EOD forced exit
        now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
        if now_et.time() >= pos.eod_exit_time:
            return "eod_exit"

        return None

    def close(self, option_symbol: str, exit_price: float, pnl: float) -> Optional[OpenPosition]:
        """
        Remove position and update cooldown timer if trade was a loss.
        Returns the closed position or None if not found.
        """
        pos = self._positions.pop(option_symbol, None)
        if pos is None:
            return None
        if pnl < 0:
            self._last_loss_time = datetime.now(tz=ET)
            logger.info(
                "Loss recorded on %s (pnl=%.2f) — cooldown active for %d min",
                option_symbol, pnl, self._s.cooldown_after_loss_minutes,
            )
        return pos

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
        """Record that an exit order has been placed; blocks should_exit() until resolved."""
        pos = self._positions.get(option_symbol)
        if pos is None:
            return
        pos.exit_pending = True
        pos.exit_order_id = order_id
        pos.exit_order_limit_price = limit_price
        pos.exit_order_placed_at = now or datetime.now(tz=ET)
        pos.exit_triggered_reason = reason
        pos.exit_is_mandatory = is_mandatory
        if exit_quote_bid is not None:
            pos.exit_quote_bid = exit_quote_bid
        logger.info(
            "Exit pending | %s | order=%s | limit=%.4f | reason=%s | mandatory=%s",
            option_symbol, (order_id[:8] if order_id else "none"),
            limit_price, reason, is_mandatory,
        )

    def clear_exit_pending(self, option_symbol: str) -> None:
        """Clear EXIT_PENDING state; position re-enters normal exit evaluation."""
        pos = self._positions.get(option_symbol)
        if pos is None:
            return
        pos.exit_pending = False
        pos.exit_order_id = None
        pos.exit_order_limit_price = 0.0
        pos.exit_order_placed_at = None
        pos.exit_triggered_reason = None
        pos.exit_is_mandatory = False
        logger.info("Exit pending cleared | %s", option_symbol)

    def record_partial_fill(
        self, option_symbol: str, filled_qty: int, filled_price: float
    ) -> None:
        """Accumulate broker-confirmed partial fill data."""
        pos = self._positions.get(option_symbol)
        if pos is None:
            return
        pos.confirmed_fill_qty += filled_qty
        pos.confirmed_fill_value += filled_qty * filled_price
        logger.info(
            "Partial fill | %s | +%d @ %.4f | total_confirmed=%d",
            option_symbol, filled_qty, filled_price, pos.confirmed_fill_qty,
        )

    def close_confirmed(
        self, option_symbol: str, exit_price: float, pnl: float
    ) -> Optional[OpenPosition]:
        """Close using broker-confirmed fill price. Updates cooldown on loss."""
        pos = self._positions.pop(option_symbol, None)
        if pos is None:
            return None
        if pnl < 0:
            self._last_loss_time = datetime.now(tz=ET)
            logger.info(
                "Loss recorded on %s (pnl=%.2f) — cooldown active for %d min",
                option_symbol, pnl, self._s.cooldown_after_loss_minutes,
            )
        logger.info(
            "Position closed (confirmed) | %s | exit=%.4f | pnl=%.2f",
            option_symbol, exit_price, pnl,
        )
        return pos

    def to_dict_list(self) -> list:
        """Serialise all open positions for the dashboard supervision API."""
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        _ET = _ZI("America/New_York")
        now = _dt.now(tz=_ET)
        result = []
        for p in self._positions.values():
            cp = p.current_price if p.current_price > 0 else p.entry_price
            unrealized_pnl = round((cp - p.entry_price) * 100 * p.quantity, 2)
            entry_et = (
                p.entry_time.replace(tzinfo=_ET)
                if p.entry_time.tzinfo is None
                else p.entry_time.astimezone(_ET)
            )
            hold_minutes = round((now - entry_et).total_seconds() / 60, 1)
            result.append({
                "option_symbol": p.option_symbol,
                "symbol": p.symbol,
                "strategy_id": p.strategy_id,
                "direction": p.direction,
                "entry_time": p.entry_time.isoformat(),
                "entry_price": p.entry_price,
                "current_price": cp,
                "peak_price": p.peak_price,
                "trough_price": p.trough_price,
                "peak_pnl": round((p.peak_price - p.entry_price) * 100 * p.quantity, 2),
                "trough_pnl": round((p.trough_price - p.entry_price) * 100 * p.quantity, 2),
                "quantity": p.quantity,
                "unrealized_pnl": unrealized_pnl,
                "stop_loss_level": round(p.entry_price * (1.0 - p.stop_loss_pct), 4),
                "take_profit_level": round(p.entry_price * (1.0 + p.take_profit_pct), 4),
                "trailing_stop_level": round(p.peak_price * (1.0 - p.trailing_stop_pct), 4),
                "stop_loss_pct": p.stop_loss_pct,
                "take_profit_pct": p.take_profit_pct,
                "trailing_stop_pct": p.trailing_stop_pct,
                "max_hold_minutes": p.max_hold_minutes,
                "eod_exit_time": p.eod_exit_time.strftime("%H:%M"),
                "hold_minutes": hold_minutes,
                "exit_pending": p.exit_pending,
                "exit_order_id": p.exit_order_id,
                "exit_triggered_reason": p.exit_triggered_reason,
                "exit_is_mandatory": p.exit_is_mandatory,
                "confirmed_fill_qty": p.confirmed_fill_qty,
            })
        return result
