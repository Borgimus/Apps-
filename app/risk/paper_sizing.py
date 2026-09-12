"""Pure helpers for the controlled paper-only scaled-sizing experiment."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Union


Number = Union[Decimal, float, int, str]


def calculate_paper_scaled_quantity(
    option_ask: Number,
    premium_budget_dollars: Number,
    max_contracts: int,
) -> int:
    """Return floor(budget / (ask * 100)), capped by max_contracts.

    A result of zero means one contract would exceed the approved premium
    budget and the entry must be skipped. Invalid, non-positive inputs also
    fail closed with a zero result.
    """
    try:
        ask = Decimal(str(option_ask))
        budget = Decimal(str(premium_budget_dollars))
        cap = int(max_contracts)
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return 0

    if not ask.is_finite() or not budget.is_finite() or ask <= 0 or budget <= 0 or cap <= 0:
        return 0

    contract_cost = ask * Decimal("100")
    budget_quantity = int(budget // contract_cost)
    return min(cap, budget_quantity)


def normalized_one_contract_pnl(realized_pnl: Number, quantity: int) -> float:
    """Normalize a multi-contract trade's P&L to one contract."""
    qty = max(1, int(quantity or 1))
    return float(Decimal(str(realized_pnl)) / Decimal(qty))
