"""Passive diagnostic shadow book, separate from broker execution.

Model 3 uses the shared PositionManager exit policy, including trailing-stop
activation. Entry simulation requires a positive, uncrossed OPRA quote no more
than 60 seconds old. Orders expire at the configured broker entry timeout.
Holding duration and price excursions begin at validated fill time. Stale exit
marks have no realized P&L. A touched ask still cannot guarantee a queued fill,
and the model excludes commissions, exit latency and slippage.

Every observation is logged, including failed quality, regime, symbol and
budget checks. Entry-filter eligibility is recorded separately. The simulator
has no chronological portfolio risk replay: capacity, daily entries, cooldown,
loss limits and reconciliation are NOT established by these results. No shadow
record counts toward readiness or authorizes strategy reactivation.

Repeated observations form one opportunity. An initially unpriceable episode
can start simulation when it becomes priceable, but opens at most once. Partial
exit variants require at least two affordable contracts and sell whole contracts.
P&L is one-contract normalized; sized diagnostic P&L is explicitly labelled.

Events append to evaluation/shadow_book.jsonl with a model version. State lives
in logs/shadow_book_state.json. Legacy state is archived without resuming it
under the new rules. Historical events are never rewritten.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from zoneinfo import ZoneInfo
from app.trading.exit_rules import exit_reason, trailing_activation_setting
from app.trading.quote_evidence import fill_evidence_valid, parse_quote_timestamp, quote_is_fresh
from app.evaluation.shadow_eligibility import assess_entry_filters

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")
SHADOW_MODEL_VERSION = "3"

# Block reasons considered "capacity competition" — these get shadow-simulated.
CAPACITY_REASONS = frozenset({
    "orb_slot_reserved",
    "position_already_open",
    "pending_order_exists",
    "cooldown_after_loss",
    "max_trades_per_day",
    "max_active_positions",
    "max_symbols_traded_day",
})

# These observations are useful diagnostics but are not eligible entries.
SIMULATION_REASONS = CAPACITY_REASONS | frozenset({
    "recon_blocked",
    "experiment_daily_loss",
    "experiment_loss_count",
    "correlated_stop_lock",
    "signal_quality_below_min",
    "market_regime_mismatch",
    "inverted_direction_counterfactual",
    "symbol_disabled",
    "strategy_shadow_only",
    "exit_policy_counterfactual",
    "paper_scaled_budget_below_one_contract",
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
    variant: str = "baseline"
    remaining_fraction: float = 1.0
    realized_pnl: float = 0.0
    breakeven_armed: bool = False
    partial_taken: bool = False
    trailing_stop_armed: bool = False
    quantity: int = 1
    last_quote_timestamp: Optional[str] = None
    last_quote_feed: Optional[str] = None
    eligibility: Dict[str, Any] = field(default_factory=dict)


class ShadowBook:
    """Passive shadow ledger for qualified-but-not-executed signals."""

    def __init__(self, settings, events_path="evaluation/shadow_book.jsonl",
                 state_path="logs/shadow_book_state.json",
                 episode_window_minutes: int = 60,
                 fill_window_minutes: Optional[float] = None,
                 clock: Optional[Callable[[], datetime]] = None,
                 session_context: Optional[Dict[str, Any]] = None):
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(_ET))
        self._session_context = session_context or {}
        self._events_path = Path(events_path)
        self._state_path = Path(state_path)
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

        p = settings.position
        self._stop_loss_pct = float(p.stop_loss_pct)
        self._take_profit_pct = float(p.take_profit_pct)
        self._trailing_stop_pct = float(p.trailing_stop_pct)
        self._trailing_activation_pct = trailing_activation_setting(p)
        self._max_hold_minutes = int(p.max_hold_minutes)
        h, m = map(int, p.eod_exit_time.split(":"))
        self._eod_exit = time(h, m)

        self._episode_window = timedelta(minutes=episode_window_minutes)
        timeout = getattr(settings, "entry_order_timeout_secs", 120)
        if not isinstance(timeout, (int, float)):
            timeout = 120
        self._fill_window = timedelta(
            minutes=fill_window_minutes if fill_window_minutes is not None else timeout / 60,
        )
        self._exit_variants_enabled = (
            getattr(settings, "paper_scaled_exit_variant_shadow_enabled", False)
            is True
        )
        self._variant_trigger_pct = float(getattr(
            settings, "paper_scaled_exit_variant_trigger_pct", 0.25
        ))
        self._variant_partial_fraction = float(getattr(
            settings, "paper_scaled_exit_variant_partial_fraction", 0.50
        ))

        self._open: Dict[str, ShadowPosition] = {}
        # (strategy, symbol, direction) -> {"opportunity_id","last_seen","observations"}
        self._episodes: Dict[str, Dict[str, Any]] = {}
        self._seq = 0
        self._load_state()

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_signal(
        self,
        now: Optional[datetime],
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
        variant: str = "baseline",
        market_regime: Optional[str] = None,
        contract_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Log a diagnostic signal. Return true for a new opportunity or a
        simulation that has just become priceable, so variant recording can retry.
        Event new_opportunity remains the unique-opportunity counting flag."""
        # Runtime callers pass None after selecting the contract. Explicit times
        # are reserved for deterministic replay/tests; quote timestamps stay intact.
        now = self._clock() if now is None else now
        self._seq += 1
        suffix = "" if variant == "baseline" else f"-{variant.replace('_', '-')}"
        signal_id = f"v{SHADOW_MODEL_VERSION}-{now.strftime('%Y%m%d')}-{self._seq:03d}-{symbol}-{strategy_id}{suffix}"

        ep_key = f"{variant}|{strategy_id}|{symbol}|{direction}"
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

        ep = self._episodes[ep_key]
        metadata = contract_metadata or {}
        eligibility = assess_entry_filters(
            self._settings, symbol=symbol, direction=direction,
            quality_score=quality_score, market_regime=market_regime,
            limit_price=limit_price, entry_ask=entry_ask, contract_metadata=metadata,
        )

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
            "variant": variant,
            "contract_metadata": metadata,
            **eligibility,
        })
        self._save_state()

        if executed:
            ep["simulated"] = True
            self._save_state()
            return new_opportunity
        if ep.get("simulated"):
            return new_opportunity
        if block_reason not in SIMULATION_REASONS:
            return new_opportunity
        if not option_symbol or not limit_price or not math.isfinite(limit_price) or limit_price <= 0:
            logger.info(
                "ShadowBook: %s blocked (%s) but not priceable — signal logged, no simulation",
                signal_id, block_reason,
            )
            return new_opportunity
        quantity = eligibility["affordable_quantity"]
        if variant == "partial_25_breakeven" and quantity < 2:
            self._emit({"event": "variant_not_executable", "signal_id": signal_id,
                        "opportunity_id": opportunity_id, "variant": variant,
                        "reason": "partial_exit_requires_two_contracts", "quantity": quantity})
            return new_opportunity
        if any(
            sp.option_symbol == option_symbol and sp.variant == variant
            for sp in self._open.values()
        ):
            return new_opportunity  # already simulating this contract

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
            variant=variant,
            quantity=quantity,
            eligibility=eligibility,
            trailing_stop_armed=self._trailing_activation_pct == 0,
        )
        # Marketable at signal time counts as validated (ask already at limit)
        if entry_ask is not None and entry_ask <= float(limit_price) and fill_evidence_valid(
            bid=float(metadata.get("bid") or 0), ask=float(entry_ask),
            timestamp=metadata.get("quote_timestamp"), feed=metadata.get("quote_feed"), now=now,
        ):
            sp.fill_validated = True
            sp.fill_validated_at = now.isoformat()
            sp.last_quote_timestamp = metadata["quote_timestamp"]
            sp.last_quote_feed = metadata["quote_feed"]
        self._open[sp.signal_id] = sp
        ep["simulated"] = True
        self._save_state()
        logger.info(
            "ShadowBook: simulating %s/%s %s @ %.4f (blocked: %s%s)",
            strategy_id, variant, option_symbol, limit_price, block_reason,
            ", fill-validated at entry" if sp.fill_validated else "",
        )
        return True  # A previously unpriceable episode can start simulation later.

    def record_exit_variants(
        self,
        *,
        now: datetime,
        strategy_id: str,
        symbol: str,
        direction: str,
        option_symbol: Optional[str],
        limit_price: Optional[float],
        entry_ask: Optional[float],
        quality_score: Optional[float] = None,
        contract_metadata: Optional[Dict[str, Any]] = None,
        market_regime: Optional[str] = None,
    ) -> None:
        """Open passive breakeven and partial-profit policy variants."""
        if not self._exit_variants_enabled:
            return
        for variant in ("breakeven_25", "partial_25_breakeven"):
            self.record_signal(
                now=now,
                strategy_id=strategy_id,
                symbol=symbol,
                direction=direction,
                executed=False,
                block_reason="exit_policy_counterfactual",
                option_symbol=option_symbol,
                limit_price=limit_price,
                entry_ask=entry_ask,
                quality_score=quality_score,
                variant=variant,
                contract_metadata=contract_metadata,
                market_regime=market_regime,
            )

    def record_inverted_signal(
        self,
        *,
        now: datetime,
        strategy_id: str,
        symbol: str,
        direction: str,
        option_symbol: Optional[str],
        limit_price: Optional[float],
        entry_ask: Optional[float],
        quality_score: Optional[float] = None,
        contract_metadata: Optional[Dict[str, Any]] = None,
        market_regime: Optional[str] = None,
    ) -> None:
        """Record a passive opposite-direction counterfactual."""
        self.record_signal(
            now=now,
            strategy_id=strategy_id,
            symbol=symbol,
            direction=direction,
            executed=False,
            block_reason="inverted_direction_counterfactual",
            option_symbol=option_symbol,
            limit_price=limit_price,
            entry_ask=entry_ask,
            quality_score=quality_score,
            variant="inverted",
            contract_metadata=contract_metadata,
            market_regime=market_regime,
        )

    # ── Simulation ────────────────────────────────────────────────────────────

    async def update(self, broker, now: Optional[datetime] = None) -> int:
        """Mark open shadow positions to quote, run fill validation, and close
        any that hit an exit rule. Runtime samples the clock after each await.
        Explicit now freezes time for deterministic replay/tests."""
        closed = 0
        dirty = False
        for sid, sp in list(self._open.items()):
            observed_at = self._clock() if now is None else now
            if not sp.fill_validated and observed_at > datetime.fromisoformat(sp.fill_deadline):
                self._emit({"event": "shadow_unfilled", "signal_id": sid,
                            "opportunity_id": sp.opportunity_id, "ts": observed_at.isoformat(),
                            "variant": sp.variant, "reason": "entry_timeout",
                            "shadow_pnl": None, "fill_validated": False})
                self._open.pop(sid)
                dirty = True
                continue
            try:
                quote = await broker.get_option_quote(sp.option_symbol)
                observed_at = self._clock() if now is None else now
                # A request started in time may finish after the entry deadline.
                if not sp.fill_validated and observed_at > datetime.fromisoformat(sp.fill_deadline):
                    self._emit({"event": "shadow_unfilled", "signal_id": sid,
                                "opportunity_id": sp.opportunity_id, "ts": observed_at.isoformat(),
                                "variant": sp.variant, "reason": "entry_timeout",
                                "shadow_pnl": None, "fill_validated": False})
                    self._open.pop(sid)
                    dirty = True
                    continue
                bid = float(quote.bid)
                ask = float(quote.ask)
                if not fill_evidence_valid(
                    bid=bid, ask=ask, timestamp=quote.timestamp,
                    feed=getattr(quote, "feed", None), now=observed_at,
                ):
                    continue
                price = bid
            except Exception as exc:
                logger.debug("ShadowBook: quote failed for %s: %s", sp.option_symbol, exc)
                continue

            # Fill validation: buy-limit is demonstrably reachable when the
            # ask touches the limit inside the order's live window.
            if (not sp.fill_validated
                    and observed_at <= datetime.fromisoformat(sp.fill_deadline)
                    and ask > 0 and ask <= sp.entry_price):
                sp.fill_validated = True
                sp.fill_validated_at = observed_at.isoformat()
                # Price excursions and holding duration start at simulated fill.
                sp.peak_price = sp.trough_price = sp.last_price = sp.entry_price
                dirty = True
                logger.info(
                    "ShadowBook: fill validated for %s (ask %.4f <= limit %.4f)",
                    sp.option_symbol, ask, sp.entry_price,
                )

            if not sp.fill_validated:
                continue
            sp.last_quote_timestamp = quote.timestamp.isoformat()
            sp.last_quote_feed = quote.feed
            sp.last_price = price
            sp.peak_price = max(sp.peak_price, price)
            sp.trough_price = min(sp.trough_price, price)
            sp.trailing_stop_armed = (
                sp.trailing_stop_armed
                or sp.peak_price >= sp.entry_price * (1.0 + self._trailing_activation_pct)
            )
            # Persist all marks, including peaks and troughs between exit events.
            dirty = True

            trigger = sp.entry_price * (1.0 + self._variant_trigger_pct)
            if (
                sp.variant == "breakeven_25"
                and not sp.breakeven_armed
                and price >= trigger
            ):
                sp.breakeven_armed = True
                dirty = True
            elif (
                sp.variant == "partial_25_breakeven"
                and not sp.partial_taken
                and price >= trigger
            ):
                partial_contracts = min(sp.quantity - 1, max(1, int(sp.quantity * self._variant_partial_fraction)))
                partial = partial_contracts / sp.quantity
                sp.realized_pnl += (
                    (price - sp.entry_price) * 100 * partial
                )
                sp.remaining_fraction = 1.0 - partial
                sp.partial_taken = True
                sp.breakeven_armed = True
                dirty = True
                logger.info(
                    "ShadowBook: partial-profit variant took %.0f%% of %s @ %.4f",
                    partial * 100,
                    sp.option_symbol,
                    price,
                )

            reason = self._exit_reason(sp, price, observed_at)
            if reason:
                self._close(sp, price, reason, observed_at)
                closed += 1
                dirty = True
        if dirty:
            self._save_state()
        return closed

    def close_all(self, now: datetime, reason: str = "session_end") -> int:
        """End simulations; stale or absent marks produce unpriced outcomes."""
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
        if sp.breakeven_armed and price <= entry:
            return "breakeven_stop"
        return exit_reason(
            entry_price=entry, current_price=price, peak_price=sp.peak_price,
            entry_time=datetime.fromisoformat(sp.fill_validated_at or sp.entry_time),
            now=now, stop_loss_pct=self._stop_loss_pct,
            take_profit_pct=self._take_profit_pct, trailing_stop_pct=self._trailing_stop_pct,
            trailing_stop_armed=sp.trailing_stop_armed,
            max_hold_minutes=self._max_hold_minutes, eod_exit_time=self._eod_exit,
        )

    def _close(self, sp: ShadowPosition, exit_price: float, reason: str, now: datetime) -> None:
        priced = sp.fill_validated and sp.last_quote_feed in ("opra", "tradier_opra") and quote_is_fresh(sp.last_quote_timestamp, now)
        raw_pnl = sp.realized_pnl + (exit_price - sp.entry_price) * 100 * sp.remaining_fraction
        pnl = round(raw_pnl, 2)
        mfe = round((sp.peak_price - sp.entry_price) * 100, 2)
        mae = round((sp.trough_price - sp.entry_price) * 100, 2)
        entry_premium = sp.entry_price * 100
        mfe_pct = round(mfe / entry_premium, 4) if entry_premium > 0 else None
        mae_pct = round(mae / entry_premium, 4) if entry_premium > 0 else None
        retention = round(pnl / mfe, 4) if mfe > 0 else None
        giveback = round(mfe - pnl, 2)
        entry_dt = datetime.fromisoformat(sp.fill_validated_at or sp.entry_time)
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
            "category": "fill_validated" if priced else "unpriced",
            "fill_validated": sp.fill_validated,
            "fill_validated_at": sp.fill_validated_at,
            "entry_time": sp.entry_time,
            "entry_price": sp.entry_price,
            "exit_price": exit_price if priced else None,
            "last_mark_price": exit_price,
            "shadow_pnl": pnl if priced else None,
            "outcome_priced": priced,
            "last_mark_pnl": pnl,
            "quantity": sp.quantity,
            "pnl_basis": "one_contract_normalized",
            "sized_shadow_pnl": round(raw_pnl * sp.quantity, 2) if priced and sp.quantity > 0 else None,
            "last_quote_timestamp": sp.last_quote_timestamp,
            "last_quote_feed": sp.last_quote_feed,
            **sp.eligibility,
            "exit_reason": reason,
            "hold_seconds": int((now - entry_dt).total_seconds()),
            "peak_price": sp.peak_price,
            "trough_price": sp.trough_price,
            "mfe": mfe,
            "mae": mae,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "profit_retention_ratio": retention if priced else None,
            "mfe_giveback": giveback if priced else None,
            "variant": sp.variant,
            "remaining_fraction": sp.remaining_fraction,
            "partial_taken": sp.partial_taken,
            "partial_realized_pnl": round(sp.realized_pnl, 2),
            "breakeven_armed": sp.breakeven_armed,
            "trailing_stop_armed": sp.trailing_stop_armed,
        })
        self._open.pop(sp.signal_id, None)
        logger.info(
            "ShadowBook: closed %s %s @ %.4f → %+.2f (%s, %s)",
            sp.strategy_id, sp.option_symbol, exit_price, pnl, reason,
            "fill-validated" if priced else "unpriced last-mark estimate",
        )

    def _emit(self, record: Dict[str, Any]) -> None:
        record["model_version"] = SHADOW_MODEL_VERSION
        for key in ("options_data_provider", "options_data_adapter_hash", "evaluation_cohort"):
            record[key] = self._session_context.get(key, "unrecorded")
        record["counts_toward_readiness"] = False
        record["exit_policy"] = {
            "stop_loss_pct": self._stop_loss_pct,
            "take_profit_pct": self._take_profit_pct,
            "trailing_stop_pct": self._trailing_stop_pct,
            "trailing_activation_pct": self._trailing_activation_pct,
            "max_hold_minutes": self._max_hold_minutes,
            "eod_exit_time_et": self._eod_exit.isoformat(),
            "entry_timeout_seconds": self._fill_window.total_seconds(),
        }
        try:
            with self._events_path.open("a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            logger.warning("ShadowBook: failed to write event: %s", exc)

    def _save_state(self) -> None:
        try:
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "model_version": SHADOW_MODEL_VERSION,
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
            if data.get("model_version") != SHADOW_MODEL_VERSION:
                # Preserve old simulator state without resuming it under changed rules.
                old_version = str(data.get("model_version", "1"))
                if not old_version.isdigit():
                    old_version = "unknown"
                backup = self._state_path.with_name(self._state_path.name + f".legacy-v{old_version}")
                # Preserve distinct snapshots even if an earlier archive exists.
                if backup.exists() and backup.read_bytes() != self._state_path.read_bytes():
                    import hashlib
                    digest = hashlib.sha256(self._state_path.read_bytes()).hexdigest()[:12]
                    backup = backup.with_name(f"{backup.name}.{digest}")
                if not backup.exists():
                    backup.write_text(self._state_path.read_text())
                self._emit({"event": "state_invalidated", "reason": "shadow_model_changed",
                            "discarded_open_count": len(data.get("open", []))})
                return
            today = self._clock().astimezone(_ET).strftime("%Y-%m-%d")
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


async def select_shadow_contract(broker, liq_filter, settings, symbol, sig, now,
                                 invert: bool = False, metadata: Optional[Dict[str, Any]] = None
                                 ) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Mirror the live contract-selection path (expirations → preferred-DTE
    chain → liquidity filter → limit price) for a signal that was blocked
    before contract selection. Returns (option_symbol, limit_price, ask) or
    (None, None, None). Read-only broker calls; never raises."""
    try:
        from app.trading.entry_filters import select_expiration_for_settings
        from app.trading.pricing import compute_limit_price

        expirations = await broker.get_available_expirations(symbol)
        today = now.date()
        target_exp = select_expiration_for_settings(expirations, today, settings)
        if target_exp is None:
            return None, None, None

        chain = await broker.get_option_chain(symbol, target_exp)
        selected_signal = sig
        if invert:
            from dataclasses import replace
            from app.strategies.strategy_base import SignalDirection
            opposite = (
                SignalDirection.SHORT
                if sig.direction == SignalDirection.LONG
                else SignalDirection.LONG
            )
            selected_signal = replace(sig, direction=opposite)
        contract = liq_filter.select_contract(chain, selected_signal)
        if contract is None:
            return None, None, None

        limit_price = compute_limit_price(
            mode=getattr(settings.options, "entry_limit_price_mode", "mid"),
            bid=float(contract.bid),
            ask=float(contract.ask),
            offset_pct=getattr(settings.options, "entry_marketable_offset_pct", 0.01),
        )
        if metadata is not None:
            metadata.update(contract_evidence(contract, now))
        return contract.option_symbol, float(limit_price), float(contract.ask)
    except Exception as exc:
        logger.debug("ShadowBook: shadow contract selection failed for %s: %s", symbol, exc)
        return None, None, None


def contract_evidence(contract, now: datetime) -> Dict[str, Any]:
    ts = parse_quote_timestamp(getattr(contract, "quote_timestamp", None))
    return {
        "bid": float(contract.bid), "ask": float(contract.ask),
        "delta": contract.delta, "expiration": contract.expiration.isoformat(),
        "dte": (contract.expiration - now.date()).days,
        "spread_pct": contract.spread_pct, "volume": contract.volume,
        "open_interest": contract.open_interest, "liquidity_passed": True,
        "quote_timestamp": ts.isoformat() if ts else None,
        "quote_feed": getattr(contract, "quote_feed", None),
    }
