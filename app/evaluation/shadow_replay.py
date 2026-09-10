"""Read-only replay of recorded first-eligible candidates and quote observations.

No broker object, order methods, current environment or subsequent trade outcome
is consulted. Decisions use only information encountered in timestamp order.
Quote touches model fills at the submitted limit; queue priority, fees and exit
slippage remain unmodelled. The research scenario assumes an empty portfolio and
strategy permission, and never grants that permission to the actual runner.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.trading.entry_filters import scaled_guardrails_active
from app.trading.exit_rules import exit_reason, trailing_activation_setting
from app.trading.quote_evidence import fill_evidence_valid, parse_quote_timestamp

ET = ZoneInfo("America/New_York")
VARIANTS = ("baseline", "breakeven_25", "partial_25_breakeven")


def capture_replay_policy(settings) -> dict:
    """Freeze the policy alongside session provenance, before any observation."""
    s, p, r, u = settings, settings.position, settings.risk, settings.universe
    return {
        "schema_version": 1,
        "scaled_sizing": s.paper_scaled_sizing_enabled is True,
        "guardrails": scaled_guardrails_active(s),
        "entry_filters": {
            "min_quality": float(s.paper_scaled_min_signal_quality),
            "confirm_market_regime": s.paper_scaled_market_regime_confirmation_enabled,
            "blocked_symbols": s.paper_scaled_blocked_symbols,
            "require_delta": s.paper_scaled_require_delta,
            "min_dte": s.paper_scaled_min_dte, "max_dte": s.paper_scaled_max_dte,
            "max_signal_age_minutes": (s.paper_scaled_max_signal_age_minutes
                                       if scaled_guardrails_active(s) else p.max_signal_age_minutes),
            "min_open_interest": r.min_open_interest, "min_volume": r.min_volume,
            "max_spread_pct": r.max_spread_pct,
            "delta_min": s.options.delta_target_min, "delta_max": s.options.delta_target_max,
            "preferred_dte": list(s.options.preferred_dte),
            "entry_price_mode": s.options.entry_limit_price_mode,
            "entry_marketable_offset_pct": s.options.entry_marketable_offset_pct,
            "global_ranking": s.paper_scaled_global_ranking_enabled,
        },
        "premium_budget": float(s.paper_scaled_premium_budget_dollars),
        "contract_cap": int(u.max_contracts_per_position),
        "max_active_positions": int(u.max_active_positions),
        "max_entries": int(r.max_trades_per_day),
        "max_symbols": int(u.max_symbols_traded_per_day),
        "one_entry_per_symbol_per_day": True,
        "max_risk_per_trade": float(r.max_risk_per_trade),
        "max_daily_loss_fraction": float(r.max_daily_loss),
        "daily_loss_dollars": float(s.paper_scaled_daily_loss_limit_dollars),
        "max_losing_trades": int(s.paper_scaled_max_losing_trades_per_day),
        "cooldown_minutes": float(p.cooldown_after_loss_minutes),
        "correlated_groups": s.paper_scaled_correlated_groups,
        "market_open": s.market_open, "market_close": s.market_close,
        "open_buffer_minutes": s.no_trade_open_buffer_minutes,
        "close_buffer_minutes": s.no_trade_close_buffer_minutes,
        "eod_exit_time": p.eod_exit_time,
        "min_entry_minutes_before_eod": p.min_entry_minutes_before_eod,
        "orb_reservation": s.paper_eval_permissive_entry_mode is True,
        "orb_reserve_until": s.orb_slot_reserve_until,
        "stop_loss_pct": float(p.stop_loss_pct),
        "take_profit_pct": float(p.take_profit_pct),
        "trailing_stop_pct": float(p.trailing_stop_pct),
        "trailing_activation_pct": trailing_activation_setting(p),
        "max_hold_minutes": p.max_hold_minutes,
        "entry_timeout_seconds": float(s.entry_order_timeout_secs),
        "variant_trigger_pct": float(s.paper_scaled_exit_variant_trigger_pct),
        "exit_variants_enabled": s.paper_scaled_exit_variant_shadow_enabled,
        "partial_fraction": float(s.paper_scaled_exit_variant_partial_fraction),
    }


def policy_hash(policy: dict) -> str:
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:16]


def eligible_observation_reasons(settings, now, signal_timestamp, metadata, ask) -> list[str]:
    """Missing or stale evidence cannot create a first-eligible entry anchor."""
    reasons = []
    signal_ts = parse_quote_timestamp(signal_timestamp)
    limit = (settings.paper_scaled_max_signal_age_minutes if scaled_guardrails_active(settings)
             else getattr(settings.position, "max_signal_age_minutes", 60))
    if not isinstance(limit, (int, float)):
        limit = 60
    if signal_ts is None or not 0 <= (now - signal_ts).total_seconds() <= float(limit) * 60:
        reasons.append("signal_age_unverified_or_stale")
    try:
        valid = fill_evidence_valid(
            bid=float(metadata.get("bid") or 0), ask=float(ask or 0),
            timestamp=metadata.get("quote_timestamp"),
            feed=metadata.get("quote_feed"), now=now,
        )
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        reasons.append("quote_evidence_invalid")
    return reasons


def replay_session(events: list[dict], variant="baseline", *, respect_permissions=True) -> dict:
    try:
        return _replay_session(events, variant, respect_permissions=respect_permissions)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return {"variant": variant, "status": "unavailable", "reason": "malformed_replay_evidence",
                "scenario": "current_strategy_permissions" if respect_permissions else "hypothetical_strategy_reactivation",
                "decisions": [], "trades": [], "counts_toward_readiness": False}


def _replay_session(events: list[dict], variant="baseline", *, respect_permissions=True) -> dict:
    """Replay a single uninterrupted model-4 session in recorded arrival order."""
    if variant not in VARIANTS:
        raise ValueError("Unknown exit variant")
    result = {
        "variant": variant, "status": "unavailable", "decisions": [], "trades": [],
        "scenario": "current_strategy_permissions" if respect_permissions else "hypothetical_strategy_reactivation",
        "initial_portfolio": "empty", "counts_toward_readiness": False,
        "pnl_basis": "sized_dollars_before_fees_and_slippage",
    }
    starts = [e for e in events if e.get("event") == "shadow_session_start"]
    if len(starts) != 1 or str(starts[0].get("model_version")) != "4":
        return {**result, "reason": "one_uninterrupted_model4_session_required"}
    context = starts[0].get("session_context", {})
    p = context.get("replay_policy", {})
    equity = context.get("starting_equity")
    if (p.get("schema_version") != 1 or not isinstance(equity, (float, int))
            or not math.isfinite(equity) or equity <= 0
            or context.get("replay_policy_hash") != policy_hash(p)):
        return {**result, "reason": "captured_policy_or_equity_missing"}
    if respect_permissions and not isinstance(context.get("broker_entry_strategies"), list):
        return {**result, "reason": "strategy_permissions_unrecorded"}

    positions, filled_strategies, submitted_symbols, stop_locks = {}, [], set(), set()
    realized = 0.0
    losses = 0
    cooldown_until = None
    prior_ts = None
    ended = False
    decision_ids = set()
    peak_pnl = drawdown = 0.0
    result["policy_hash"] = context["replay_policy_hash"]
    result["policy"] = p

    def at(now, hhmm):
        hour, minute = map(int, hhmm.split(":")[:2])
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def group(symbol):
        for spec in p["correlated_groups"].split(";"):
            if ":" in spec:
                name, members = spec.split(":", 1)
                if symbol.upper() in {m.strip().upper() for m in members.split(",")}:
                    return name.strip() or "unnamed"
        return None

    def finish(sid, pos, bid, now, reason):
        nonlocal realized, losses, cooldown_until, peak_pnl, drawdown
        leg = (bid - pos["entry_price"]) * pos["remaining"] * 100
        realized += leg
        total = pos["partial_pnl"] + leg
        if total < 0:
            losses += 1
            cooldown_until = now + timedelta(minutes=p["cooldown_minutes"])
            if p["guardrails"] and reason == "stop_loss" and group(pos["symbol"]):
                stop_locks.add((group(pos["symbol"]), pos["direction"].lower()))
        peak_pnl = max(peak_pnl, realized)
        drawdown = max(drawdown, peak_pnl - realized)
        result["trades"].append({
            "pair_id": sid, "symbol": pos["symbol"], "strategy_id": pos["strategy_id"],
            "option_symbol": pos["option_symbol"], "entry_time": pos["entry_time"],
            "fill_time": pos["fill_time"].isoformat(), "exit_time": now.isoformat(),
            "entry_price": pos["entry_price"], "exit_price": bid, "quantity": pos["quantity"],
            "pnl": round(total, 2), "exit_reason": reason,
        })
        positions.pop(sid)

    relevant = {"eligible_entry", "shadow_quote", "shadow_session_end"}
    for event in events:
        if event.get("event") not in relevant:
            continue
        now = parse_quote_timestamp(event.get("ts"))
        if (now is None or (prior_ts is not None and now < prior_ts) or ended
                or str(event.get("model_version")) != "4"
                or event.get("session_id") != starts[0].get("session_id")
                or event.get("replay_policy_hash") != context["replay_policy_hash"]):
            return {**result, "status": "unavailable", "reason": "event_provenance_or_order_invalid",
                    "decisions": [], "trades": []}
        now = now.astimezone(ET)
        prior_ts = now
        for sid, pos in list(positions.items()):
            if pos["fill_time"] is None and now > pos["deadline"]:
                positions.pop(sid)
                result["decisions"].append({"pair_id": sid, "ts": now.isoformat(), "action": "expired"})

        if event["event"] == "eligible_entry":
            sid = event["pair_id"]
            if sid in decision_ids:
                return {**result, "reason": "duplicate_candidate", "decisions": [], "trades": []}
            decision_ids.add(sid)
            meta = event["contract_metadata"]
            reasons = []
            gates = event.get("runtime_gates", {})
            if gates.get("reconciliation_clear") is not True:
                reasons.append("reconciliation_not_clear")
            if gates.get("kill_switch_clear") is not True:
                reasons.append("kill_switch_not_clear")
            if respect_permissions and event["strategy_id"] not in context["broker_entry_strategies"]:
                reasons.append("strategy_suspended")
            if event.get("entry_filter_eligible") is not True:
                reasons.append("entry_filters_failed")
            if len(positions) >= p["max_active_positions"]:
                reasons.append("max_active_positions")
            if any(pos["symbol"] == event["symbol"] for pos in positions.values()):
                reasons.append("underlying_already_reserved")
            pending = [pos for pos in positions.values() if pos["fill_time"] is None]
            if len(filled_strategies) + len(pending) >= p["max_entries"]:
                reasons.append("max_entries")
            if p["one_entry_per_symbol_per_day"] and event["symbol"] in submitted_symbols:
                reasons.append("symbol_already_submitted_today")
            if event["symbol"] not in submitted_symbols and len(submitted_symbols) >= p["max_symbols"]:
                reasons.append("max_symbols")
            if cooldown_until is not None and now < cooldown_until:
                reasons.append("cooldown_after_loss")
            if -realized >= equity * p["max_daily_loss_fraction"]:
                reasons.append("account_daily_loss")
            if p["guardrails"]:
                if -realized >= p["daily_loss_dollars"]:
                    reasons.append("experiment_daily_loss")
                if losses >= p["max_losing_trades"]:
                    reasons.append("experiment_loss_count")
                if (group(event["symbol"]), event["direction"].lower()) in stop_locks:
                    reasons.append("correlated_stop_lock")
            if (now < at(now, p["market_open"]) + timedelta(minutes=p["open_buffer_minutes"])
                    or now >= at(now, p["market_close"]) - timedelta(minutes=p["close_buffer_minutes"])):
                reasons.append("session_buffer")
            if now > at(now, p["eod_exit_time"]) - timedelta(minutes=p["min_entry_minutes_before_eod"]):
                reasons.append("eod_entry_cutoff")
            non_orb = sum(s != "orb" for s in filled_strategies) + sum(pos["strategy_id"] != "orb" for pos in pending)
            if (p["orb_reservation"] and event["strategy_id"] != "orb"
                    and now < at(now, p["orb_reserve_until"]) and non_orb >= p["max_entries"] - 1):
                reasons.append("orb_slot_reserved")
            bid, ask = float(meta["bid"]), float(meta["ask"])
            if not fill_evidence_valid(bid=bid, ask=ask, timestamp=meta["quote_timestamp"],
                                       feed=meta["quote_feed"], now=now):
                reasons.append("quote_evidence_invalid")
            # RiskManager scales quantity to current equity. Eligible comparisons
            # retain their matched size; portfolio results disclose the actual size.
            marked_pnl = sum((pos["last_bid"] - pos["entry_price"]) * pos["remaining"] * 100
                             for pos in positions.values() if pos["fill_time"] is not None)
            risk_dollars = max(0, equity + realized + marked_pnl) * p["max_risk_per_trade"]
            quantity = min(int(event["quantity"]), p["contract_cap"],
                           int(Decimal(str(risk_dollars)) / (Decimal(str(ask)) * 100))) if ask > 0 else 0
            if quantity < 1:
                reasons.append("max_risk_per_trade")
            if p["scaled_sizing"] and max(ask, event["entry_price"]) * quantity * 100 > p["premium_budget"] + 1e-8:
                reasons.append("premium_budget")
            if variant == "partial_25_breakeven" and quantity < 2:
                reasons.append("partial_exit_requires_two_contracts")
            result["decisions"].append({"pair_id": sid, "ts": now.isoformat(),
                                        "action": "rejected" if reasons else "admitted",
                                        "reasons": reasons, "quantity": quantity})
            if reasons:
                continue
            filled = ask <= event["entry_price"]
            pos = dict(event, quantity=quantity, remaining=quantity,
                       deadline=parse_quote_timestamp(event["fill_deadline"]),
                       fill_time=now if filled else None, partial_pnl=0.0,
                       last_bid=bid, last_quote=meta, peak=event["entry_price"],
                       breakeven=False, partial=False)
            positions[sid] = pos
            submitted_symbols.add(pos["symbol"])
            if filled:
                filled_strategies.append(pos["strategy_id"])

        elif event["event"] == "shadow_quote":
            bid, ask = event["bid"], event["ask"]
            if not fill_evidence_valid(bid=bid, ask=ask, timestamp=event["quote_timestamp"],
                                       feed=event["quote_feed"], now=now):
                continue
            for sid, pos in list(positions.items()):
                if pos["option_symbol"] != event["option_symbol"]:
                    continue
                if pos["fill_time"] is None:
                    if ask > pos["entry_price"]:
                        continue
                    pos["fill_time"] = now
                    filled_strategies.append(pos["strategy_id"])
                pos["last_bid"], pos["last_quote"] = bid, event
                pos["peak"] = max(pos["peak"], bid)
                if variant != "baseline" and bid >= pos["entry_price"] * (1 + p["variant_trigger_pct"]):
                    pos["breakeven"] = True
                    if variant == "partial_25_breakeven" and not pos["partial"]:
                        sold = min(pos["quantity"] - 1, max(1, int(pos["quantity"] * p["partial_fraction"])))
                        partial_pnl = (bid - pos["entry_price"]) * sold * 100
                        pos["partial_pnl"] += partial_pnl
                        realized += partial_pnl
                        peak_pnl = max(peak_pnl, realized)
                        pos["remaining"] -= sold
                        pos["partial"] = True
                reason = ("breakeven_stop" if pos["breakeven"] and bid <= pos["entry_price"] else exit_reason(
                    entry_price=pos["entry_price"], current_price=bid, peak_price=pos["peak"],
                    entry_time=pos["fill_time"], now=now, stop_loss_pct=p["stop_loss_pct"],
                    take_profit_pct=p["take_profit_pct"], trailing_stop_pct=p["trailing_stop_pct"],
                    trailing_stop_armed=pos["peak"] >= pos["entry_price"] * (1 + p["trailing_activation_pct"]),
                    max_hold_minutes=p["max_hold_minutes"], eod_exit_time=at(now, p["eod_exit_time"]).time(),
                ))
                if reason:
                    finish(sid, pos, bid, now, reason)
        else:
            ended = True
            for sid, pos in list(positions.items()):
                q = pos["last_quote"]
                if pos["fill_time"] is None:
                    positions.pop(sid)
                    result["decisions"].append({"pair_id": sid, "ts": now.isoformat(), "action": "cancelled_at_end"})
                elif fill_evidence_valid(bid=q["bid"], ask=q["ask"], timestamp=q["quote_timestamp"],
                                          feed=q["quote_feed"], now=now):
                    finish(sid, pos, pos["last_bid"], now, "session_end")

    result.update(status="complete" if ended and not positions else "incomplete",
                  admitted=sum(d["action"] == "admitted" for d in result["decisions"]),
                  filled=len(filled_strategies), closed=len(result["trades"]),
                  unresolved=len(positions), realized_pnl=round(realized, 2),
                  max_realized_drawdown=round(drawdown, 2), losing_trades=losses)
    result["portfolio_pnl"] = result["realized_pnl"] if result["status"] == "complete" else None
    return result
