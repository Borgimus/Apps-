"""
Shadow book: passive per-strategy signal ledger.

Records EVERY fully qualified ORB / VWAP signal — executed or blocked by
capacity constraints (three-trade limit, ORB slot reservation, open-position
dedup, cooldown, RECON_BLOCKED) — and simulates outcomes for the blocked ones
under the same exit rules the live PositionManager applies. This shows whether
trade-slot competition is starving a strategy of samples, independent of the
broker-executed book.

Strictly observational: never places orders, never mutates trading state.
Shadow results are persisted separately from broker-executed results:

  evaluation/shadow_book.jsonl   append-only event log (signal / shadow_close)
  logs/shadow_book_state.json    open shadow positions (restart recovery)

Modeling assumptions (documented for analysis):
  * Shadow entries fill at the same limit price the live pricing mode would
    have placed (live fills frequently improve on this — shadow is slightly
    conservative on entry).
  * Exits use the option BID (same convention as monitor_positions) and fill
    at the trigger price with no slippage.
  * Outcomes are marked-to-quote every poll cycle (~5 min), matching the live
    monitoring cadence.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Block reasons considered "capacity competition" — these get shadow-simulated.
CAPACITY_REASONS = frozenset({
    "orb_slot_reserved",
    "position_already_open",
    "pending_order_exists",
    "cooldown_after_loss",
    "max_trades_per_day",
    "max_active_positions",
    "max_symbols_traded_day",
    "recon_blocked",
})


@dataclass
class ShadowPosition:
    signal_id: str
    strategy_id: str
    symbol: str
    direction: str
    option_symbol: str
    entry_time: str          # ISO
    entry_price: float       # assumed fill at the would-be limit
    block_reason: str
    peak_price: float = 0.0
    trough_price: float = 0.0
    last_price: float = 0.0


class ShadowBook:
    """Passive shadow ledger for qualified-but-not-executed signals."""

    def __init__(self, settings, events_path="evaluation/shadow_book.jsonl",
                 state_path="logs/shadow_book_state.json"):
        self._events_path = Path(events_path)
        self._state_path = Path(state_path)
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

        p = settings.position
        self._stop_loss_pct = float(p.stop_loss_pct)
        self._take_profit_pct = float(p.take_profit_pct)
        self._trailing_stop_pct = float(p.trailing_stop_pct)
        self._max_hold_minutes = int(p.max_hold_minutes)
        h, m = map(int, p.eod_exit_time.split(":"))
        self._eod_exit = time(h, m)

        self._open: Dict[str, ShadowPosition] = {}
        self._seq = 0
        self._load_state()

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_signal(
        self,
        now: datetime,
        strategy_id: str,
        symbol: str,
        direction: str,
        executed: bool,
        block_reason: Optional[str] = None,
        option_symbol: Optional[str] = None,
        limit_price: Optional[float] = None,
        journal_id: Optional[int] = None,
        quality_score: Optional[float] = None,
    ) -> None:
        """Log one fully qualified signal. If blocked for a capacity reason and
        priceable, open a shadow position to simulate its outcome."""
        self._seq += 1
        signal_id = f"{now.strftime('%Y%m%d')}-{self._seq:03d}-{symbol}-{strategy_id}"
        self._emit({
            "event": "signal",
            "signal_id": signal_id,
            "ts": now.isoformat(),
            "strategy_id": strategy_id,
            "symbol": symbol,
            "direction": direction,
            "executed": executed,
            "block_reason": None if executed else (block_reason or "unknown"),
            "option_symbol": option_symbol,
            "limit_price": limit_price,
            "journal_id": journal_id,
            "quality_score": quality_score,
        })

        if executed:
            return
        if block_reason not in CAPACITY_REASONS:
            return
        if not option_symbol or not limit_price or limit_price <= 0:
            logger.info(
                "ShadowBook: %s blocked (%s) but not priceable — signal logged, no simulation",
                signal_id, block_reason,
            )
            return
        if any(sp.option_symbol == option_symbol for sp in self._open.values()):
            return  # already simulating this contract

        self._open[signal_id] = ShadowPosition(
            signal_id=signal_id,
            strategy_id=strategy_id,
            symbol=symbol,
            direction=direction,
            option_symbol=option_symbol,
            entry_time=now.isoformat(),
            entry_price=float(limit_price),
            block_reason=block_reason,
            peak_price=float(limit_price),
            trough_price=float(limit_price),
            last_price=float(limit_price),
        )
        self._save_state()
        logger.info(
            "ShadowBook: simulating %s %s @ %.4f (blocked: %s)",
            strategy_id, option_symbol, limit_price, block_reason,
        )

    # ── Simulation ────────────────────────────────────────────────────────────

    async def update(self, broker, now: datetime) -> int:
        """Mark open shadow positions to quote and close any that hit an exit
        rule. Returns number of shadow closes this cycle. Never raises."""
        closed = 0
        for sid, sp in list(self._open.items()):
            try:
                quote = await broker.get_option_quote(sp.option_symbol)
                bid = float(quote.bid)
                ask = float(quote.ask)
                mid = (bid + ask) / 2 if (bid + ask) > 0 else 0.0
                price = bid if bid > 0 else (mid or sp.last_price)
            except Exception as exc:
                logger.debug("ShadowBook: quote failed for %s: %s", sp.option_symbol, exc)
                continue

            sp.last_price = price
            sp.peak_price = max(sp.peak_price, price)
            sp.trough_price = min(sp.trough_price, price)

            reason = self._exit_reason(sp, price, now)
            if reason:
                self._close(sp, price, reason, now)
                closed += 1
        if closed:
            self._save_state()
        return closed

    def close_all(self, now: datetime, reason: str = "session_end") -> int:
        """Force-close all open shadow positions at last known price."""
        n = 0
        for sp in list(self._open.values()):
            self._close(sp, sp.last_price, reason, now)
            n += 1
        self._save_state()
        return n

    def open_count(self) -> int:
        return len(self._open)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _exit_reason(self, sp: ShadowPosition, price: float, now: datetime) -> Optional[str]:
        entry = sp.entry_price
        if price <= entry * (1.0 - self._stop_loss_pct):
            return "stop_loss"
        if price >= entry * (1.0 + self._take_profit_pct):
            return "take_profit"
        if price <= sp.peak_price * (1.0 - self._trailing_stop_pct):
            return "trailing_stop"
        entry_dt = datetime.fromisoformat(sp.entry_time)
        if (now - entry_dt).total_seconds() / 60 >= self._max_hold_minutes:
            return "max_hold"
        if now.time() >= self._eod_exit:
            return "eod_exit"
        return None

    def _close(self, sp: ShadowPosition, exit_price: float, reason: str, now: datetime) -> None:
        pnl = round((exit_price - sp.entry_price) * 100, 2)
        entry_dt = datetime.fromisoformat(sp.entry_time)
        self._emit({
            "event": "shadow_close",
            "signal_id": sp.signal_id,
            "ts": now.isoformat(),
            "strategy_id": sp.strategy_id,
            "symbol": sp.symbol,
            "direction": sp.direction,
            "option_symbol": sp.option_symbol,
            "block_reason": sp.block_reason,
            "entry_price": sp.entry_price,
            "exit_price": exit_price,
            "shadow_pnl": pnl,
            "exit_reason": reason,
            "hold_seconds": int((now - entry_dt).total_seconds()),
            "peak_price": sp.peak_price,
            "trough_price": sp.trough_price,
        })
        self._open.pop(sp.signal_id, None)
        logger.info(
            "ShadowBook: closed %s %s @ %.4f → %+.2f (%s)",
            sp.strategy_id, sp.option_symbol, exit_price, pnl, reason,
        )

    def _emit(self, record: Dict[str, Any]) -> None:
        try:
            with self._events_path.open("a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            logger.warning("ShadowBook: failed to write event: %s", exc)

    def _save_state(self) -> None:
        try:
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(
                {"seq": self._seq, "open": [asdict(sp) for sp in self._open.values()]},
                indent=2,
            ))
            tmp.replace(self._state_path)
        except Exception as exc:
            logger.warning("ShadowBook: failed to save state: %s", exc)

    def _load_state(self) -> None:
        try:
            if not self._state_path.exists():
                return
            data = json.loads(self._state_path.read_text())
            self._seq = int(data.get("seq", 0))
            today = datetime.now().strftime("%Y-%m-%d")
            for row in data.get("open", []):
                # Only restore same-day shadow positions
                if str(row.get("entry_time", "")).startswith(today):
                    sp = ShadowPosition(**row)
                    self._open[sp.signal_id] = sp
            if self._open:
                logger.info("ShadowBook: restored %d open shadow position(s)", len(self._open))
        except Exception as exc:
            logger.warning("ShadowBook: failed to load state: %s", exc)


async def select_shadow_contract(broker, liq_filter, settings, symbol, sig, now):
    """Mirror the live contract-selection path (expirations → preferred-DTE
    chain → liquidity filter → limit price) for a signal that was blocked
    before contract selection. Returns (option_symbol, limit_price) or
    (None, None). Read-only broker calls; never raises."""
    try:
        from app.trading.pricing import compute_limit_price

        expirations = await broker.get_available_expirations(symbol)
        today = now.date()
        target_exp = None
        for dte in settings.options.preferred_dte:
            candidate = today + timedelta(days=dte)
            if candidate in expirations:
                target_exp = candidate
                break
        if target_exp is None and expirations:
            target_exp = min(expirations, key=lambda d: abs((d - today).days))
        if target_exp is None:
            return None, None

        chain = await broker.get_option_chain(symbol, target_exp)
        contract = liq_filter.select_contract(chain, sig)
        if contract is None:
            return None, None

        limit_price = compute_limit_price(
            mode=getattr(settings.options, "entry_limit_price_mode", "mid"),
            bid=float(contract.bid),
            ask=float(contract.ask),
            offset_pct=getattr(settings.options, "entry_marketable_offset_pct", 0.01),
        )
        return contract.option_symbol, float(limit_price)
    except Exception as exc:
        logger.debug("ShadowBook: shadow contract selection failed for %s: %s", symbol, exc)
        return None, None
