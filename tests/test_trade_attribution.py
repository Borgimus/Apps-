from datetime import datetime
from types import SimpleNamespace

import pytest

from app.evaluation.trade_attribution import analyze_trade, summarize_diagnostics


def _trade(**overrides):
    values = {
        "id": 42,
        "strategy_id": "orb",
        "underlying_symbol": "QQQ",
        "option_symbol": "QQQ260819C00700000",
        "signal_direction": "long",
        "entry_time": datetime(2026, 8, 19, 9, 45),
        "expiration": "2026-08-19",
        "fill_price": 1.00,
        "exit_price": 0.80,
        "quantity": 1,
        "filled_quantity": 1,
        "realized_pnl": -20.0,
        "mfe": 50.0,
        "mae": -25.0,
        "peak_price": 1.50,
        "trough_price": 0.75,
        "spread_pct": 0.05,
        "delta": 0.40,
        "time_to_fill_secs": 30.0,
        "exit_reason": "trailing_stop",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_loss_after_meaningful_mfe_is_exit_asymmetry():
    row = analyze_trade(_trade())
    assert row.primary_attribution == "exit_asymmetry"
    assert row.mfe_pct == pytest.approx(0.50)
    assert row.mae_pct == pytest.approx(-0.25)
    assert row.mfe_giveback_dollars == pytest.approx(70.0)
    assert row.profit_retention_ratio == pytest.approx(-0.40)


def test_late_loser_that_never_worked_is_entry_timing():
    row = analyze_trade(_trade(mfe=2.0, mae=-40.0, time_to_fill_secs=91.0))
    assert row.primary_attribution == "entry_timing"
    assert "late_fill" in row.evidence_flags


def test_never_worked_with_contract_risk_is_contract_selection():
    row = analyze_trade(
        _trade(mfe=1.0, mae=-50.0, spread_pct=0.18, delta=0.20)
    )
    assert row.primary_attribution == "contract_selection"
    assert "wide_entry_spread" in row.evidence_flags
    assert "delta_outside_target" in row.evidence_flags


def test_never_worked_with_missing_delta_is_contract_selection():
    row = analyze_trade(_trade(mfe=0.0, mae=-50.0, delta=None))
    assert row.primary_attribution == "contract_selection"
    assert "delta_missing" in row.evidence_flags


def test_shadow_missing_metadata_is_not_mislabeled_contract_selection():
    row = analyze_trade(_trade(
        mfe=0.0,
        mae=-50.0,
        delta=None,
        contract_metadata_expected=False,
    ))
    assert row.primary_attribution == "entry_signal_failure"
    assert "contract_metadata_unavailable" in row.evidence_flags


def test_missing_explicit_excursions_backfills_from_extrema():
    row = analyze_trade(_trade(mfe=None, mae=None, peak_price=1.20, trough_price=0.70))
    assert row.mfe_dollars == pytest.approx(20.0)
    assert row.mae_dollars == pytest.approx(-30.0)


def test_incomplete_excursion_data_is_not_guessed():
    row = analyze_trade(_trade(mfe=None, mae=None, peak_price=None, trough_price=None))
    assert row.primary_attribution == "insufficient_data"


def test_dte_and_zero_dte_flag_are_reported():
    row = analyze_trade(_trade())
    assert row.dte == 0
    assert "zero_dte" in row.evidence_flags


def test_summary_identifies_dominant_failure_and_coverage():
    rows = [
        analyze_trade(_trade(id=1)),
        analyze_trade(_trade(id=2, mfe=1.0, mae=-30.0, time_to_fill_secs=90.0)),
        analyze_trade(_trade(id=3, mfe=40.0, mae=-10.0, realized_pnl=10.0)),
    ]
    summary = summarize_diagnostics(rows)
    assert summary["trades_analyzed"] == 3
    assert summary["coverage_pct"] == pytest.approx(1.0)
    assert summary["dominant_failure_mode"] in {"entry_timing", "exit_asymmetry"}
    assert summary["total_mfe_giveback_dollars"] > 0
