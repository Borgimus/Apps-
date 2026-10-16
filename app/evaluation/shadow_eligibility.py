"""Entry-filter assessment, separate from diagnostic shadow P&L.

Passing these filters does not validate a portfolio: downstream capacity,
cooldown, loss limits and broker reconciliation still require chronological replay.
"""

from math import isfinite

from app.risk.paper_sizing import calculate_paper_scaled_quantity


def assess_entry_filters(settings, *, symbol, direction, quality_score,
                         market_regime, limit_price, entry_ask, contract_metadata):
    reasons = []
    meta = contract_metadata or {}
    scaled = getattr(settings, "paper_scaled_sizing_enabled", False) is True
    guards = scaled and getattr(settings, "paper_scaled_guardrails_enabled", False) is True
    quantity = 1
    if scaled:
        cap = settings.universe.max_contracts_per_position
        try:
            price = max(float(limit_price), float(entry_ask))
        except (ValueError, TypeError):
            price = 0.0
        quantity = calculate_paper_scaled_quantity(
            price, settings.paper_scaled_premium_budget_dollars, cap,
        )
        if quantity == 0:
            reasons.append("premium_budget_or_price_invalid")
    if guards:
        if symbol.upper() in {s.strip().upper() for s in settings.paper_scaled_blocked_symbols.split(",")}:
            reasons.append("symbol_disabled")
        if quality_score is None or not isfinite(quality_score) or quality_score < settings.paper_scaled_min_signal_quality:
            reasons.append("signal_quality_below_min")
        if settings.paper_scaled_market_regime_confirmation_enabled and str(market_regime).lower() != direction.lower():
            reasons.append("market_regime_mismatch")
    if meta.get("liquidity_passed") is not True:
        reasons.append("contract_filters_unverified")
    return {
        "entry_filter_eligible": not reasons,
        "entry_filter_reasons": reasons,
        "affordable_quantity": quantity,
        "portfolio_validated": False,
        "simulation_scope": "diagnostic",
    }
