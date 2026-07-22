"""Read and summarize observation-only Alpaca/Tradier comparison records."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]


def read_market_data_observations(
    day: str,
    root: Optional[Path] = None,
) -> list[dict[str, Any]]:
    base = root or ROOT
    path = base / "evaluation" / "market_data_comparisons.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(row, dict)
            and row.get("event") == "selected_contract_comparison"
            and row.get("session_date") == day
        ):
            rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("ts", "")))


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values: list[Optional[float]]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 6) if present else None


def summarize_market_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [row for row in rows if row.get("status") == "ok"]
    errors = [row for row in rows if row.get("status") == "error"]
    disagreements = [
        row
        for row in successes
        if (row.get("comparison") or {}).get("liquidity_disagreement") is True
    ]

    # The observer may sample the same contract more than once. The display uses
    # the latest observation per contract/strategy while the summary retains the
    # full sample count.
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in successes:
        key = (str(row.get("option_symbol", "")), str(row.get("strategy_id", "")))
        latest[key] = row

    display_rows: list[dict[str, Any]] = []
    for row in sorted(latest.values(), key=lambda item: str(item.get("ts", "")), reverse=True):
        alpaca = row.get("alpaca") or {}
        tradier = row.get("tradier") or {}
        comparison = row.get("comparison") or {}
        display_rows.append({
            "ts": row.get("ts"),
            "underlying_symbol": row.get("underlying_symbol"),
            "option_symbol": row.get("option_symbol"),
            "strategy_id": row.get("strategy_id"),
            "direction": row.get("direction"),
            "alpaca_bid": alpaca.get("bid"),
            "alpaca_ask": alpaca.get("ask"),
            "tradier_bid": tradier.get("bid"),
            "tradier_ask": tradier.get("ask"),
            "mid_diff": comparison.get("mid_diff"),
            "mid_diff_pct": comparison.get("mid_diff_pct"),
            "alpaca_spread_pct": comparison.get("alpaca_spread_pct"),
            "tradier_spread_pct": comparison.get("tradier_spread_pct"),
            "spread_diff_pct": comparison.get("spread_diff_pct"),
            "tradier_quote_age_secs": comparison.get("tradier_quote_age_secs"),
            "tradier_liquidity_passed": comparison.get("tradier_liquidity_passed"),
            "tradier_rejection_reasons": comparison.get("tradier_rejection_reasons") or [],
        })

    return {
        "mode": "observe",
        "affects_trading": False,
        "observations": len(rows),
        "successful": len(successes),
        "errors": len(errors),
        "liquidity_disagreements": len(disagreements),
        "unique_contracts": len({row.get("option_symbol") for row in successes if row.get("option_symbol")}),
        "avg_abs_mid_diff": _average([
            abs(value) if value is not None else None
            for value in (_number((row.get("comparison") or {}).get("mid_diff")) for row in successes)
        ]),
        "avg_abs_mid_diff_pct": _average([
            abs(value) if value is not None else None
            for value in (_number((row.get("comparison") or {}).get("mid_diff_pct")) for row in successes)
        ]),
        "avg_tradier_quote_age_secs": _average([
            _number((row.get("comparison") or {}).get("tradier_quote_age_secs"))
            for row in successes
        ]),
        "rows": display_rows,
        "error_rows": errors[-10:],
    }
