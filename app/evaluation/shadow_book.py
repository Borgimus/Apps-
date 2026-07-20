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

  evaluation/shadow_book.jsonl   append-only event log
  logs/shadow_book_state.json    open positions + episodes (restart recovery)

Raw signals vs unique opportunities
───────────────────────────────────
A strategy condition can stay true across several polling cycles; those
repeated observations are ONE trade opportunity, not many. Signals are
grouped into episodes keyed by (strategy, underlying, direction): a new
observation joins the current episode unless the key has been quiet for
longer than `episode_window_minutes` (default 60). Every raw observation is
logged (`new_opportunity` false), but only the first observation of an
episode can open a shadow position. Analysis should use unique opportunities
as the denominator; raw counts are reported for completeness.

Fill validation
───────────────
Opening every shadow position at the proposed limit overstates results, so
each shadow entry carries two categories:

  theoretical     the order would have been submitted (all shadow entries)
  fill-validated  the market demonstrably reached the order: the quote ask
                  touched the buy limit within `fill_window_minutes` of entry
                  (mirroring the live entry-order lifetime before stale-cancel
                  resolution)

Fill-validated results are the primary counterfactual; theoretical results
are a sensitivity analysis. Closes are tagged with `fill_validated`.

Model bias (NET BIAS IS MIXED — do not read shadow P&L as a bound):
  * Entry at the submitted limit with no price improvement: conservative on
    entry price (live fills frequently improve).
  * Assuming the limit fills: optimistic on fill probability — mitigated but
    not eliminated by fill validation (a touched ask does not guarantee a
    fill at queue position).
  * Exit at the trigger price with no slippage: optimistic.
  * Marking to the bid for valuation: conservative.

Interpretation rule: shadow trades inform strategy design and capacity
decisions only. They never count toward the live-readiness paper-trading
sample; broker-executed trades remain the primary evidence because they
include real order behavior, fill uncertainty, timing, and state management.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
    opportunity_id: str
    strategy_id: str
    symbol: str
    direction: str
    option_symbol: str
    entry_time: str          # ISO
    entry_price: float       # assumed fill at the would-be limit
    block_reason: str
    fill_deadline: str       # ISO — ask must touch limit before this
    fill_validated: bool = False
    fill_validated_at: Optional[str] = None
    peak_price: float = 0.0
    trough_price: float = 0.0
    last_price: float = 0.0


class ShadowBook:
    """Passive shadow ledger for qualified-but-not-executed signals."""

    def __init__(self, settings, events_path="evaluation/shadow_book.jsonl",
                 state_path="logs/shadow_book_state.json",
                 episode_window_minutes: int = 60,
                 fill_window_minutes: int = 10):
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

        self._episode_window = timedelta(minutes=episode_window_minutes)
        self._fill_window = timedelta(minutes=fill_window_minutes)

        self._open: Dict[str, ShadowPosition] = {}
        # (strategy, symbol, direction) -> {"opportunity_id","last_seen","observations"}
        self._episodes: Dict[str, Dict[str, Any]] = {}
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
        entry_ask: Optional[float] = None,
        journal_id: Optional[int] = None,
        quality_score: Optional[float] = None,
    ) -> None:
        """Log one fully qualified signal observation.

        Repeated observations of the same (strategy, symbol, direction) within
        the episode window join the existing opportunity and never open a
        second shadow position. Blocked-for-capacity first observations open a
        shadow position when priceable."""
        self._seq += 1
        signal_id = f"{now.strftime('%Y%m%d')}-{self._seq:03d}-{symbol}-{strategy_id}"

        ep_key = f"{strategy_id}|{symbol}|{direction}"
        ep = self._episodes.get(ep_key)
        new_opportunity = True
        if ep is not None:
            last_seen = datetime.fromisoformat(ep["last_seen"])
            if now - last_seen <= self._episode_window:
                new_opportunity = False
        if new_opportunity:
            opportunity_id = f"{now.strftime('%Y%m%d')}-{symbol}-{strategy_id}-{self._seq:03d}"
            self._episodes[ep_key] = {
                "opportunity_id": opportunity_id,
                "last_seen": now.isoformat(),
                "observations": 1,
            }
        else:
            ep["last_seen"] = now.isoformat()
            ep["observations"] = int(ep.get("observations", 0)) + 1
            opportunity_id = ep["opportunity_id"]

        self._emit({
            "event": "signal",
            "signal_id": signal_id,
            "opportunity_id": opportunity_id,
            "new_opportunity": new_opportunity,
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
        self._save_state()

        if executed or not new_opportunity:
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

        sp = ShadowPosition(
            signal_id=signal_id,
            opportunity_id=opportunity_id,
            strategy_id=strategy_id,
            symbol=symbol,
            direction=direction,
            option_symbol=option_symbol,
            entry_time=now.isoformat(),
            entry_price=float(limit_price),
            block_reason=block_reason,
            fill_deadline=(now + self._fill_window).isoformat(),
            peak_price=float(limit_price),
            trough_price=float(limit_price),
            last_price=float(limit_price),
        )
        # Marketable at signal time counts as validated (ask already at limit)
        if entry_ask is not None and entry_ask > 0 and entry_ask <= float(limit_price):
            sp.fill_validated = True
            sp.fill_validated_at = now.isoformat()
        self._open[sp.signal_id] = sp
        self._save_state()
        logger.info(
            "ShadowBook: simulating %s %s @ %.4f (blocked: %s%s)",
            strategy_id, option_symbol, limit_price, block_reason,
            ", fill-validated at entry" if sp.fill_validated else "",
        )

    # ── Simulation ────────────────────────────────────────────────────────────

    async def update(self, broker, now: datetime) -> int:
        """Mark open shadow positions to quote, run fill validation, and close
        any that hit an exit rule. Returns closes this cycle. Never raises."""
        closed = 0
        dirty = False
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

            # Fill validation: buy-limit is demonstrably reachable when the
            # ask touches the limit inside the order's live window.
            if (not sp.fill_validated
                    and now <= datetime.fromisoformat(sp.fill_deadline)
                    and ask > 0 and ask <= sp.entry_price):
                sp.fill_validated = True
                sp.fill_validated_at = now.isoformat()
                dirty = True
                logger.info(
                    "ShadowBook: fill validated for %s (ask %.4f <= limit %.4f)",
                    sp.option_symbol, ask, sp.entry_price,
                )

            sp.last_price = price
            sp.peak_price = max(sp.peak_price, price)
            sp.trough_price = min(sp.trough_price, price)

            reason = self._exit_reason(sp, price, now)
            if reason:
                self._close(sp, price, reason, now)
                closed += 1
                dirty = True
        if dirty:
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
            "opportunity_id": sp.opportunity_id,
            "ts": now.isoformat(),
            "strategy_id": sp.strategy_id,
            "symbol": sp.symbol,
            "direction": sp.direction,
            "option_symbol": sp.option_symbol,
            "block_reason": sp.block_reason,
            "category": "fill_validated" if sp.fill_validated else "theoretical",
            "fill_validated": sp.fill_validated,
            "fill_validated_at": sp.fill_validated_at,
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
            "ShadowBook: closed %s %s @ %.4f → %+.2f (%s, %s)",
            sp.strategy_id, sp.option_symbol, exit_price, pnl, reason,
            "fill-validated" if sp.fill_validated else "theoretical",
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
            tmp.write_text(json.dumps({
                "seq": self._seq,
                "open": [asdict(sp) for sp in self._open.values()],
                "episodes": self._episodes,
            }, indent=2))
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
            for key, ep in (data.get("episodes") or {}).items():
                if str(ep.get("last_seen", "")).startswith(today):
                    self._episodes[key] = ep
            if self._open:
                logger.info("ShadowBook: restored %d open shadow position(s)", len(self._open))
        except Exception as exc:
            logger.warning("ShadowBook: failed to load state: %s", exc)


async def select_shadow_contract(broker, liq_filter, settings, symbol, sig, now
                                 ) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Mirror the live contract-selection path (expirations → preferred-DTE
    chain → liquidity filter → limit price) for a signal that was blocked
    before contract selection. Returns (option_symbol, limit_price, ask) or
    (None, None, None). Read-only broker calls; never raises."""
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
            return None, None, None

        chain = await broker.get_option_chain(symbol, target_exp)
        contract = liq_filter.select_contract(chain, sig)
        if contract is None:
            return None, None, None

        limit_price = compute_limit_price(
            mode=getattr(settings.options, "entry_limit_price_mode", "mid"),
            bid=float(contract.bid),
            ask=float(contract.ask),
            offset_pct=getattr(settings.options, "entry_marketable_offset_pct", 0.01),
        )
        return contract.option_symbol, float(limit_price), float(contract.ask)
    except Exception as exc:
        logger.debug("ShadowBook: shadow contract selection failed for %s: %s", symbol, exc)
        return None, None, None
