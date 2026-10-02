"""Trade excursion and failure-mode diagnostics.

This module is observational.  It does not alter entries, exits, sizing, or
risk decisions.  It converts the option-price extrema already persisted on a
closed trade into comparable percentage metrics and a conservative primary
attribution.

The extrema are sampled at the runner's quote polling cadence, so MFE and MAE
are lower-resolution estimates rather than exchange-tick extrema.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class AttributionThresholds:
    """Diagnostic thresholds.  These are not trading gates."""

    negligible_mfe_pct: float = 0.05
    meaningful_mfe_pct: float = 0.10
    low_mfe_retention_ratio: float = 0.25
    late_fill_seconds: float = 60.0
    max_spread_pct: float = 0.10
    delta_target_min: float = 0.35
    delta_target_max: float = 0.45


@dataclass(frozen=True)
class TradeDiagnostic:
    journal_id: Optional[int]
    strategy_id: str
    symbol: str
    option_symbol: Optional[str]
    direction: Optional[str]
    entry_time: Optional[str]
    fill_price: Optional[float]
    exit_price: Optional[float]
    quantity: int
    realized_pnl: Optional[float]
    exit_reason: Optional[str]
    mfe_dollars: Optional[float]
    mae_dollars: Optional[float]
    mfe_pct: Optional[float]
    mae_pct: Optional[float]
    profit_retention_ratio: Optional[float]
    mfe_giveback_dollars: Optional[float]
    dte: Optional[int]
    fill_latency_seconds: Optional[float]
    entry_spread_pct: Optional[float]
    delta: Optional[float]
    evidence_flags: tuple[str, ...]
    primary_attribution: str
    attribution_reason: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_flags"] = list(self.evidence_flags)
        return data


def _float(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _trade_dte(trade: Any) -> Optional[int]:
    expiration = getattr(trade, "expiration", None)
    entry_time = getattr(trade, "entry_time", None)
    if not expiration or not entry_time:
        return None
    try:
        expiry_date = date.fromisoformat(str(expiration)[:10])
        if isinstance(entry_time, str):
            entry_date = datetime.fromisoformat(entry_time).date()
        elif isinstance(entry_time, datetime):
            entry_date = entry_time.date()
        else:
            return None
        return (expiry_date - entry_date).days
    except (TypeError, ValueError):
        return None


def analyze_trade(
    trade: Any,
    thresholds: AttributionThresholds | None = None,
) -> TradeDiagnostic:
    """Build an excursion record and conservative failure attribution."""

    t = thresholds or AttributionThresholds()
    quantity = max(
        1,
        int(
            getattr(trade, "filled_quantity", None)
            or getattr(trade, "quantity", None)
            or 1
        ),
    )
    fill_price = _float(getattr(trade, "fill_price", None))
    exit_price = _float(getattr(trade, "exit_price", None))
    realized = _float(getattr(trade, "realized_pnl", None))
    mfe = _float(getattr(trade, "mfe", None))
    mae = _float(getattr(trade, "mae", None))

    # Backfill dollar excursions from persisted extrema for older rows whose
    # schema had peak/trough before the explicit MFE/MAE values were written.
    peak = _float(getattr(trade, "peak_price", None))
    trough = _float(getattr(trade, "trough_price", None))
    if mfe is None and fill_price is not None and peak is not None:
        mfe = (peak - fill_price) * 100 * quantity
    if mae is None and fill_price is not None and trough is not None:
        mae = (trough - fill_price) * 100 * quantity

    premium = fill_price * 100 * quantity if fill_price and fill_price > 0 else None
    mfe_pct = mfe / premium if mfe is not None and premium else None
    mae_pct = mae / premium if mae is not None and premium else None
    retention = realized / mfe if realized is not None and mfe is not None and mfe > 0 else None
    giveback = mfe - realized if realized is not None and mfe is not None else None

    spread = _float(getattr(trade, "spread_pct", None))
    delta = _float(getattr(trade, "delta", None))
    contract_metadata_expected = bool(
        getattr(trade, "contract_metadata_expected", True)
    )
    fill_latency = _float(getattr(trade, "time_to_fill_secs", None))
    dte = _trade_dte(trade)

    flags: list[str] = []
    if fill_latency is not None and fill_latency > t.late_fill_seconds:
        flags.append("late_fill")
    if spread is not None and spread > t.max_spread_pct:
        flags.append("wide_entry_spread")
    if delta is None and contract_metadata_expected:
        flags.append("delta_missing")
    elif delta is None:
        flags.append("contract_metadata_unavailable")
    elif not t.delta_target_min <= abs(delta) <= t.delta_target_max:
        flags.append("delta_outside_target")
    if dte == 0:
        flags.append("zero_dte")

    if mfe_pct is None or mae_pct is None or realized is None:
        attribution = "insufficient_data"
        reason = "Closed trade lacks complete MFE/MAE or realized-PnL telemetry."
    elif realized >= 0:
        if mfe_pct >= t.meaningful_mfe_pct and retention is not None and retention < t.low_mfe_retention_ratio:
            attribution = "exit_giveback"
            reason = "Trade finished non-negative but retained less than the configured share of meaningful MFE."
        else:
            attribution = "worked"
            reason = "Trade finished non-negative without material low-retention evidence."
    elif mfe_pct >= t.meaningful_mfe_pct and retention is not None and retention < t.low_mfe_retention_ratio:
        attribution = "exit_asymmetry"
        reason = "Trade had meaningful favorable excursion, then closed at a loss after giving it back."
    elif mfe_pct <= t.negligible_mfe_pct:
        if "late_fill" in flags:
            attribution = "entry_timing"
            reason = "The fill was late and the position never developed meaningful favorable excursion."
        elif any(flag in flags for flag in (
            "wide_entry_spread",
            "delta_missing",
            "delta_outside_target",
        )):
            attribution = "contract_selection"
            reason = (
                "The position never worked and the selected contract carried "
                "wide-spread, missing-delta, or outside-target delta risk."
            )
        else:
            attribution = "entry_signal_failure"
            reason = "The position never developed meaningful favorable excursion; timing versus signal quality remains unresolved."
    else:
        attribution = "mixed_entry_exit"
        reason = "The trade moved somewhat favorably, but not enough to isolate entry quality from exit behavior."

    return TradeDiagnostic(
        journal_id=getattr(trade, "id", None),
        strategy_id=str(getattr(trade, "strategy_id", None) or "unknown"),
        symbol=str(getattr(trade, "underlying_symbol", None) or getattr(trade, "symbol", None) or "unknown"),
        option_symbol=getattr(trade, "option_symbol", None),
        direction=getattr(trade, "signal_direction", None) or getattr(trade, "direction", None),
        entry_time=_iso(getattr(trade, "entry_time", None)),
        fill_price=fill_price,
        exit_price=exit_price,
        quantity=quantity,
        realized_pnl=realized,
        exit_reason=getattr(trade, "exit_reason", None),
        mfe_dollars=None if mfe is None else round(mfe, 2),
        mae_dollars=None if mae is None else round(mae, 2),
        mfe_pct=None if mfe_pct is None else round(mfe_pct, 4),
        mae_pct=None if mae_pct is None else round(mae_pct, 4),
        profit_retention_ratio=None if retention is None else round(retention, 4),
        mfe_giveback_dollars=None if giveback is None else round(giveback, 2),
        dte=dte,
        fill_latency_seconds=fill_latency,
        entry_spread_pct=spread,
        delta=delta,
        evidence_flags=tuple(flags),
        primary_attribution=attribution,
        attribution_reason=reason,
    )


def summarize_diagnostics(diagnostics: Iterable[TradeDiagnostic]) -> dict[str, Any]:
    rows = list(diagnostics)
    with_excursions = [r for r in rows if r.mfe_dollars is not None and r.mae_dollars is not None]
    counts = Counter(r.primary_attribution for r in rows)
    pnl_by_mode: dict[str, float] = {}
    for row in rows:
        pnl_by_mode[row.primary_attribution] = round(
            pnl_by_mode.get(row.primary_attribution, 0.0) + (row.realized_pnl or 0.0),
            2,
        )

    failure_counts = {
        key: value
        for key, value in counts.items()
        if key not in {"worked", "insufficient_data"}
    }
    dominant = None
    if failure_counts:
        dominant = sorted(failure_counts, key=lambda key: (-failure_counts[key], key))[0]

    avg_mfe = (
        sum(r.mfe_dollars or 0.0 for r in with_excursions) / len(with_excursions)
        if with_excursions else None
    )
    avg_mae = (
        sum(r.mae_dollars or 0.0 for r in with_excursions) / len(with_excursions)
        if with_excursions else None
    )
    total_giveback = sum(
        max(0.0, r.mfe_giveback_dollars or 0.0)
        for r in with_excursions
    )

    return {
        "trades_analyzed": len(rows),
        "trades_with_excursion_data": len(with_excursions),
        "coverage_pct": round(len(with_excursions) / len(rows), 4) if rows else None,
        "average_mfe_dollars": None if avg_mfe is None else round(avg_mfe, 2),
        "average_mae_dollars": None if avg_mae is None else round(avg_mae, 2),
        "total_mfe_giveback_dollars": round(total_giveback, 2),
        "attribution_counts": dict(sorted(counts.items())),
        "pnl_by_attribution": dict(sorted(pnl_by_mode.items())),
        "dominant_failure_mode": dominant,
        "sampling_note": "MFE/MAE use runner polling observations, not exchange-tick extrema.",
    }
