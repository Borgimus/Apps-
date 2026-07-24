"""
Risk Manager.

Enforces all pre-trade guardrails before any order reaches the broker.
This is the last gate before order submission — it must be called
unconditionally for every order attempt.

Hard-coded rules (not configurable at runtime):
  • No market orders for options — always limit only.
  • Kill switch file check.
  • No trades in first 15 min / last 15 min of session.

Configurable rules (from settings):
  • max_risk_per_trade    (fraction of equity)
  • max_trades_per_day
  • max_daily_loss        (fraction of starting equity)
  • min_open_interest
  • min_volume
  • max_spread_pct
  • earnings blackout
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ..brokers.broker_interface import OptionContract, OrderRequest, OrderType
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class RiskCheck(str, Enum):
    KILL_SWITCH = "kill_switch"
    MARKET_ORDER = "market_order"
    SESSION_BUFFER = "session_buffer"
    MAX_RISK_PER_TRADE = "max_risk_per_trade"
    MAX_TRADES_PER_DAY = "max_trades_per_day"
    MAX_DAILY_LOSS = "max_daily_loss"
    MIN_OPEN_INTEREST = "min_open_interest"
    MIN_VOLUME = "min_volume"
    MAX_SPREAD_PCT = "max_spread_pct"
    EARNINGS_BLACKOUT = "earnings_blackout"
    LIVE_TRADING_GUARD = "live_trading_guard"
    RECON_BLOCKED = "recon_blocked"
    EOD_ENTRY_CUTOFF = "eod_entry_cutoff"


@dataclass
class RiskCheckResult:
    passed: bool
    failed_checks: List[RiskCheck] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    approved_quantity: int = 0
    approved_risk_dollars: Decimal = Decimal("0")

    def add_failure(self, check: RiskCheck, msg: str):
        self.passed = False
        self.failed_checks.append(check)
        self.messages.append(msg)

    def summary(self) -> str:
        if self.passed:
            return f"APPROVED | qty={self.approved_quantity} | risk=${self.approved_risk_dollars:.2f}"
        return "REJECTED | " + " | ".join(self.messages)


class RiskManager:
    """
    Stateful risk manager.  Must be instantiated once per trading session
    and re-used so that daily counters (entries, P&L) are maintained correctly.

    Entry counting semantics
    ────────────────────────
    max_trades_per_day is treated as max NEW ENTRIES per day.
    Exits (trailing_stop, stop_loss, EOD) are never counted against this limit
    and are never blocked by it.

    The check uses:
        entries_today + pending_entries >= max_trades_per_day

    pending_entries prevents order spam: a slot is reserved the moment an entry
    order is accepted by the broker and released only when the fill is confirmed
    or the order is cancelled/rejected.

    Lifecycle:
        record_entry_pending()  — order accepted by broker
        record_entry_filled()   — fill confirmed by FillTracker
        record_entry_cancelled()— order cancelled or rejected (frees slot)
        record_exit(pnl)        — position closed; for PnL tracking only
    """

    def __init__(self, settings: Settings | None = None):
        self._s = settings or get_settings()
        self._entries_today: int = 0     # confirmed filled entries
        self._pending_entries: int = 0   # placed but not yet filled/cancelled
        self._exits_today: int = 0       # closed positions (reporting only)
        self._daily_pnl: Decimal = Decimal("0")
        self._session_date: Optional[date] = None
        self._starting_equity: Optional[Decimal] = None
        # RECON_BLOCKED: set when broker order state is unknown at recovery time.
        # Cleared by Reconciler after a successful broker.get_orders() round-trip.
        # Blocks new entries only; exits are never routed through check_order().
        self._recon_blocked: bool = False
        self._recon_blocked_reason: str = ""

    # ── Session management ────────────────────────────────────────────────────

    def start_session(self, equity: Decimal):
        """Call at the start of each trading day."""
        today = date.today()
        if self._session_date != today:
            logger.info(
                "RiskManager: new session %s | starting_equity=%.2f", today, equity
            )
            self._session_date = today
            self._entries_today = 0
            self._pending_entries = 0
            self._exits_today = 0
            self._daily_pnl = Decimal("0")
            self._starting_equity = equity

    # ── Entry / exit recording ────────────────────────────────────────────────

    def record_entry_pending(self):
        """Call when an entry order is accepted by the broker (before fill confirmation)."""
        self._pending_entries += 1
        logger.info(
            "RiskManager: entry pending | entries=%d pending=%d exits=%d",
            self._entries_today, self._pending_entries, self._exits_today,
        )

    def record_entry_filled(self):
        """Call when a pending entry order is confirmed filled by the broker."""
        self._pending_entries = max(0, self._pending_entries - 1)
        self._entries_today += 1
        logger.info(
            "RiskManager: entry filled | entries=%d pending=%d exits=%d",
            self._entries_today, self._pending_entries, self._exits_today,
        )

    def record_entry_cancelled(self):
        """Call when a pending entry order is cancelled or rejected without filling."""
        self._pending_entries = max(0, self._pending_entries - 1)
        logger.info(
            "RiskManager: entry cancelled | entries=%d pending=%d exits=%d",
            self._entries_today, self._pending_entries, self._exits_today,
        )

    def record_exit(self, pnl: Decimal = Decimal("0")):
        """
        Call when a position is closed (trailing_stop, stop_loss, take_profit, EOD).
        Never counts against the entry limit. Updates PnL for daily-loss tracking.
        """
        self._exits_today += 1
        self._daily_pnl += pnl
        logger.info(
            "RiskManager: exit recorded | entries=%d exits=%d daily_pnl=%.2f",
            self._entries_today, self._exits_today, float(self._daily_pnl),
        )

    def record_trade(self, pnl: Decimal = Decimal("0")):
        """
        Deprecated. Use record_entry_pending() for new entries, record_exit(pnl) for closes.
        Kept for backward compatibility: calls record_entry_pending() when pnl=0,
        or record_exit(pnl) when pnl is non-zero (legacy exit callers).
        """
        if pnl != Decimal("0"):
            self.record_exit(pnl)
        else:
            self.record_entry_pending()

    def set_recon_blocked(self, reason: str = "") -> None:
        """
        Block new entries until broker order state has been confirmed.

        Called by SessionRecovery when broker.get_orders() is unavailable or
        when local/broker order state is inconsistent.  Exits are never routed
        through check_order() so they are unaffected.
        """
        self._recon_blocked = True
        self._recon_blocked_reason = reason or "broker order state unknown at recovery time"
        logger.warning(
            "RiskManager: RECON_BLOCKED — new entries halted | reason: %s",
            self._recon_blocked_reason,
        )

    def clear_recon_blocked(self) -> None:
        """
        Clear the reconciliation block after a successful broker round-trip.

        Called by Reconciler once both broker.get_orders() and
        broker.get_positions() complete successfully.
        """
        if self._recon_blocked:
            logger.info(
                "RiskManager: RECON_BLOCKED cleared — broker state confirmed, entries unblocked",
            )
            self._recon_blocked = False
            self._recon_blocked_reason = ""

    @property
    def recon_blocked(self) -> bool:
        """True while new entries are blocked pending broker reconciliation."""
        return self._recon_blocked

    def restore_daily_counters(
        self,
        entries: int,
        pnl: Decimal,
        pending: int,
        recon_blocked: bool = False,
    ) -> None:
        """
        Restore daily counters from persistent state after a process restart.
        Must be called after start_session() so _session_date is set.

        entries      : filled-entry count from trade_journal.
        pnl          : sum of realized_pnl for closed trades today.
        pending      : broker-confirmed open order count.
        recon_blocked: True when broker order state was unavailable or
                       inconsistent; sets RECON_BLOCKED to prevent new entries
                       until the Reconciler confirms broker state.
        """
        if self._session_date is None:
            raise RuntimeError("restore_daily_counters called before start_session()")
        prev_entries = self._entries_today
        prev_pending = self._pending_entries
        self._entries_today = max(self._entries_today, entries)
        self._pending_entries = max(self._pending_entries, pending)
        self._daily_pnl = pnl if isinstance(pnl, Decimal) else Decimal(str(pnl))
        logger.info(
            "RiskManager: counters restored from DB "
            "| entries %d→%d | pending %d→%d | pnl %.2f",
            prev_entries, self._entries_today,
            prev_pending, self._pending_entries,
            float(self._daily_pnl),
        )
        if recon_blocked:
            self.set_recon_blocked("broker.get_orders() unavailable or inconsistent at recovery time")

    # ── Main check ────────────────────────────────────────────────────────────

    def check_order(
        self,
        request: OrderRequest,
        equity: Decimal,
        contract: Optional[OptionContract] = None,
        earnings_calendar: Optional[Dict[str, List[date]]] = None,
        now: Optional[datetime] = None,
    ) -> RiskCheckResult:
        """
        Run all pre-trade risk checks.

        Parameters
        ----------
        request           : proposed order.
        equity            : current account equity.
        contract          : option contract details (for liquidity checks).
        earnings_calendar : {symbol: [earnings_date, ...]} for blackout check.
        now               : override current datetime (useful for testing).
        """
        result = RiskCheckResult(passed=True, approved_quantity=request.quantity)
        now = now or datetime.now(tz=ET)

        self._check_recon_blocked(result)
        self._check_kill_switch(result)
        self._check_live_trading_guard(result)
        self._check_market_order(result, request)
        self._check_session_buffer(result, now)
        self._check_eod_entry_cutoff(result, now)
        self._check_max_trades_per_day(result)
        self._check_daily_loss(result, equity)
        self._check_risk_per_trade(result, request, equity, contract)
        if contract:
            self._check_liquidity(result, contract)
        self._check_earnings_blackout(result, request.symbol, earnings_calendar, now.date())

        if not result.passed:
            logger.warning(
                "RiskManager: REJECTED %s %s | %s",
                request.side,
                request.option_symbol,
                result.summary(),
            )
        else:
            logger.info(
                "RiskManager: APPROVED %s %s | %s",
                request.side,
                request.option_symbol,
                result.summary(),
            )

        return result

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_recon_blocked(self, result: RiskCheckResult):
        if self._recon_blocked:
            result.add_failure(
                RiskCheck.RECON_BLOCKED,
                f"Entry blocked: broker order state unconfirmed "
                f"({self._recon_blocked_reason}). "
                f"Waiting for reconciler to confirm broker state.",
            )

    def _check_kill_switch(self, result: RiskCheckResult):
        if self._s.is_kill_switch_active():
            result.add_failure(
                RiskCheck.KILL_SWITCH,
                f"Kill switch active: {self._s.kill_switch_file} exists",
            )

    def _check_live_trading_guard(self, result: RiskCheckResult):
        if self._s.live_trading_enabled:
            # Log prominent warning — do not block, but create an audit trail
            logger.warning(
                "⚠️  LIVE TRADING ENABLED — order will be submitted to live broker"
            )

    def _check_market_order(self, result: RiskCheckResult, request: OrderRequest):
        if request.order_type == OrderType.MARKET:
            result.add_failure(
                RiskCheck.MARKET_ORDER,
                "Market orders for options are prohibited. Use limit orders only.",
            )

    def _check_session_buffer(self, result: RiskCheckResult, now: datetime):
        open_h, open_m = map(int, self._s.market_open.split(":"))
        close_h, close_m = map(int, self._s.market_close.split(":"))

        market_open_dt = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
        market_close_dt = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)

        no_trade_open = market_open_dt + timedelta(minutes=self._s.no_trade_open_buffer_minutes)
        no_trade_close = market_close_dt - timedelta(minutes=self._s.no_trade_close_buffer_minutes)

        if now < no_trade_open:
            result.add_failure(
                RiskCheck.SESSION_BUFFER,
                f"No trades in first {self._s.no_trade_open_buffer_minutes} min after open "
                f"(until {no_trade_open.strftime('%H:%M')} ET)",
            )
        elif now >= no_trade_close:
            result.add_failure(
                RiskCheck.SESSION_BUFFER,
                f"No trades in last {self._s.no_trade_close_buffer_minutes} min before close "
                f"(after {no_trade_close.strftime('%H:%M')} ET)",
            )

    def _check_eod_entry_cutoff(self, result: RiskCheckResult, now: datetime):
        eod_h, eod_m = map(int, self._s.position.eod_exit_time.split(":"))
        eod_dt = now.replace(hour=eod_h, minute=eod_m, second=0, microsecond=0)
        minutes_to_eod = (eod_dt - now).total_seconds() / 60
        min_required = self._s.position.min_entry_minutes_before_eod
        if 0 < minutes_to_eod < min_required:
            result.add_failure(
                RiskCheck.EOD_ENTRY_CUTOFF,
                f"Only {minutes_to_eod:.0f} min before EOD exit "
                f"({self._s.position.eod_exit_time} ET) — "
                f"minimum {min_required} min required for new entries",
            )

    def _check_max_trades_per_day(self, result: RiskCheckResult):
        capacity_used = self._entries_today + self._pending_entries
        if capacity_used >= self._s.risk.max_trades_per_day:
            result.add_failure(
                RiskCheck.MAX_TRADES_PER_DAY,
                f"Max entries per day reached: {capacity_used}/{self._s.risk.max_trades_per_day}",
            )

    def _check_daily_loss(self, result: RiskCheckResult, equity: Decimal):
        if self._starting_equity and self._starting_equity > 0:
            loss_pct = float(-self._daily_pnl / self._starting_equity)
            max_loss = self._s.risk.max_daily_loss
            if loss_pct >= max_loss:
                result.add_failure(
                    RiskCheck.MAX_DAILY_LOSS,
                    f"Daily loss limit reached: {loss_pct:.1%} >= {max_loss:.1%}",
                )

    def _check_risk_per_trade(
        self,
        result: RiskCheckResult,
        request: OrderRequest,
        equity: Decimal,
        contract: Optional[OptionContract],
    ):
        max_risk = equity * Decimal(str(self._s.risk.max_risk_per_trade))
        # Option premium × 100 shares per contract = max loss on long option
        premium = contract.ask if contract else request.limit_price
        trade_cost = premium * 100 * request.quantity
        result.approved_risk_dollars = trade_cost

        if trade_cost > max_risk:
            # Scale down quantity instead of hard reject if possible
            max_contracts = int(max_risk / (premium * 100))
            if max_contracts < 1:
                result.add_failure(
                    RiskCheck.MAX_RISK_PER_TRADE,
                    f"Trade cost ${trade_cost:.2f} exceeds max risk ${max_risk:.2f} "
                    f"and cannot be reduced to 1 contract",
                )
            else:
                logger.info(
                    "RiskManager: quantity reduced %d→%d to stay within risk limit",
                    request.quantity,
                    max_contracts,
                )
                result.approved_quantity = max_contracts
                result.approved_risk_dollars = premium * 100 * max_contracts

    def _check_liquidity(self, result: RiskCheckResult, contract: OptionContract):
        r = self._s.risk

        if contract.open_interest < r.min_open_interest:
            result.add_failure(
                RiskCheck.MIN_OPEN_INTEREST,
                f"Open interest {contract.open_interest} < {r.min_open_interest}",
            )

        if contract.volume < r.min_volume:
            result.add_failure(
                RiskCheck.MIN_VOLUME,
                f"Volume {contract.volume} < {r.min_volume}",
            )

        if contract.spread_pct > r.max_spread_pct:
            result.add_failure(
                RiskCheck.MAX_SPREAD_PCT,
                f"Spread {contract.spread_pct:.3f} > {r.max_spread_pct:.3f}",
            )

    def _check_earnings_blackout(
        self,
        result: RiskCheckResult,
        symbol: str,
        earnings_calendar: Optional[Dict[str, List[date]]],
        today: date,
    ):
        if self._s.risk.allow_earnings_trades or not earnings_calendar:
            return

        blackout = self._s.risk.earnings_blackout_days
        for ed in earnings_calendar.get(symbol, []):
            if abs((ed - today).days) <= blackout:
                result.add_failure(
                    RiskCheck.EARNINGS_BLACKOUT,
                    f"{symbol} has earnings on {ed} (within {blackout}-day blackout)",
                )
                break

    # ── Utility ───────────────────────────────────────────────────────────────

    @property
    def entries_today(self) -> int:
        """Number of entry orders confirmed filled today."""
        return self._entries_today

    @property
    def pending_entries(self) -> int:
        """Entry orders placed with the broker but not yet filled or cancelled."""
        return self._pending_entries

    @property
    def exits_today(self) -> int:
        """Number of positions closed today (for reporting; never blocks entries)."""
        return self._exits_today

    @property
    def trades_today(self) -> int:
        """Backward-compat alias for entries_today (filled entries only)."""
        return self._entries_today

    @property
    def daily_pnl(self) -> Decimal:
        return self._daily_pnl

    def position_size_contracts(
        self, equity: Decimal, option_ask: Decimal, max_risk_override: float | None = None
    ) -> int:
        """
        Calculate maximum number of contracts based on risk budget.
        Returns at least 1 if the single-contract cost is within budget,
        otherwise 0 (meaning the trade should not be placed).
        """
        risk_pct = max_risk_override or self._s.risk.max_risk_per_trade
        max_risk = equity * Decimal(str(risk_pct))
        cost_per_contract = option_ask * 100
        if cost_per_contract <= 0:
            return 0
        contracts = int(max_risk / cost_per_contract)
        return max(contracts, 0)
