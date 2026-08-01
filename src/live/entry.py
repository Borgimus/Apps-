"""Live entry evaluation for a qualified candidate setup.

Given a stored setup (breakout level + initial-stop reference) and current market data, this
decides whether to enter NOW, applying the same deterministic rules as the rest of the system:
regular-session only, fresh data, valid breakout (no gap/chase), portfolio caps, and 1%-risk
sizing. It returns a fully-formed entry order + attached protective stop, or a typed rejection.
It never submits anything — the orchestrator owns submission and only acts in PAPER_AUTO.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.broker.interface import OrderRequest
from src.data.calendar import is_regular_session
from src.data.market_data import MarketSnapshot, require_fresh
from src.execution.orders import build_entry_order, build_protective_stop
from src.risk.portfolio import OpenRisk, can_open_new_position
from src.risk.sizing import compute_size
from src.scanner.breakout import BreakoutDecision, entry_limit_price, evaluate_breakout


@dataclass
class CandidateSetup:
    symbol: str
    breakout_level: float
    initial_stop: float          # lookahead-free reference (e.g. session low up to trigger)
    reference_price: float       # prior close/open, for gap detection
    setup_version: str


@dataclass
class EntryDecision:
    accepted: bool
    reject_reason: str | None = None
    trade_id: str | None = None
    entry_order: OrderRequest | None = None
    stop_order: OrderRequest | None = None
    shares: int = 0
    expected_entry: float = 0.0
    initial_stop: float = 0.0


def evaluate_entry(
    setup: CandidateSetup,
    *,
    snapshot: MarketSnapshot,
    last_price: float,
    account,                      # broker.interface.Account
    open_positions: list[OpenRisk],
    open_symbols: set[str],
    committed_risk: float,
    config: dict,
    now,
) -> EntryDecision:
    cfg_bo = config["breakout"]
    cfg_risk = config["risk"]

    # Duplicate guard: never enter a symbol we already hold or have pending.
    if setup.symbol in open_symbols:
        return EntryDecision(False, reject_reason="duplicate_position_or_pending")

    # Regular session only (no extended-hours entry in v1).
    if not is_regular_session(now):
        return EntryDecision(False, reject_reason="not_regular_session")

    # Fresh data required.
    try:
        require_fresh(snapshot, float(cfg_bo.get("freshness", {}).get("max_bar_staleness_seconds", 120)))
    except Exception as exc:  # DataError -> fail closed
        return EntryDecision(False, reject_reason=f"stale_data:{exc}")

    # Valid breakout (crossed, not gapped beyond max, within chase distance).
    bo = evaluate_breakout(setup.breakout_level, last_price, setup.reference_price, cfg_bo)
    if bo.decision is not BreakoutDecision.TRIGGER:
        return EntryDecision(False, reject_reason=f"breakout_{bo.decision.value.lower()}:{bo.reason}")

    # Conservative expected entry (includes allowed slippage).
    limit = entry_limit_price(setup.breakout_level, cfg_bo)
    expected_entry = setup.breakout_level * (1.0 + float(cfg_bo.get("order", {}).get(
        "slippage_ceiling_pct", 0.5)) / 100.0)

    # Portfolio-level gate before sizing binds the final count.
    est_notional = expected_entry * 1  # refined after sizing; used only for the exposure gate
    ok, why = can_open_new_position(open_positions, est_notional, cfg_risk)
    if not ok:
        return EntryDecision(False, reject_reason=f"portfolio:{why}")

    sizing = compute_size(
        expected_entry_price=expected_entry, initial_stop_price=setup.initial_stop,
        risk_equity=float(account.equity), risk_fraction=float(cfg_risk["risk_fraction"]),
        buying_power=float(account.buying_power),
        max_position_notional=float(cfg_risk["max_position_notional"]),
        liquidity_cap_shares=None, cfg_risk=cfg_risk, already_committed_risk=committed_risk,
    )
    if not sizing.accepted:
        return EntryDecision(False, reject_reason=f"sizing:{sizing.reject_reason.value}")

    trade_id = f"{setup.symbol}-{setup.setup_version}"
    entry_order = build_entry_order(trade_id=trade_id, symbol=setup.symbol, qty=sizing.shares,
                                    breakout_level=setup.breakout_level, limit_price=limit)
    stop_order = build_protective_stop(trade_id=trade_id, symbol=setup.symbol,
                                       qty=sizing.shares, stop_price=setup.initial_stop)
    return EntryDecision(
        accepted=True, trade_id=trade_id, entry_order=entry_order, stop_order=stop_order,
        shares=sizing.shares, expected_entry=expected_entry, initial_stop=setup.initial_stop,
    )
