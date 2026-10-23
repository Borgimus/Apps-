"""Regression tests for completed-trade accounting in the evaluation ledger."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.evaluation.daily_report import DailyReport, StrategyStats
from app.evaluation.ledger import EvaluationLedger


def _record(status, realized_pnl, *, rejection_reason=None):
    return SimpleNamespace(
        status=status,
        realized_pnl=realized_pnl,
        entry_time=(
            datetime(2026, 7, 29, 13, 46, tzinfo=timezone.utc)
            if status == "closed"
            else None
        ),
        delta=None,
        spread_pct=0.02,
        rejection_reason=rejection_reason,
    )


def test_rejected_and_cancelled_attempts_are_not_breakeven_trades():
    report = DailyReport(
        date="2026-07-29",
        session_start=None,
        session_end=None,
        trades_submitted=2,
        trades_filled=1,
        trades_cancelled=1,
        trades_rejected=29,
        realized_pnl=-9.0,
        by_strategy=[
            StrategyStats(
                strategy_id="vwap_reclaim",
                fills=1,
                realized_pnl=-9.0,
                losses=1,
            )
        ],
    )
    records = [_record("closed", -9.0), _record("cancelled", 0.0)]
    records.extend(
        _record(
            "rejected",
            0.0,
            rejection_reason="cooldown_after_loss: active",
        )
        for _ in range(29)
    )

    ledger = EvaluationLedger()
    entry = ledger.add_session(report, trade_records=records)
    cumulative = ledger.compute_cumulative()

    assert entry.total_trades == 1
    assert entry.wins == 0
    assert entry.losses == 1
    assert entry.breakevens == 0
    assert entry.gross_wins == 0.0
    assert entry.gross_losses == 9.0
    assert entry.reject_reasons == {"cooldown_after_loss": 29}
    assert entry.by_entry_hour["09:00"]["trades"] == 1

    assert cumulative["total_trades"] == 1
    assert cumulative["total_pnl"] == -9.0
    assert cumulative["expectancy"] == -9.0
    assert cumulative["win_rate"] == 0.0


def test_closed_zero_pnl_trade_remains_a_breakeven():
    report = DailyReport(
        date="2026-07-30",
        session_start=None,
        session_end=None,
        trades_submitted=1,
        trades_filled=1,
        realized_pnl=0.0,
        by_strategy=[
            StrategyStats(
                strategy_id="orb",
                fills=1,
                realized_pnl=0.0,
            )
        ],
    )

    ledger = EvaluationLedger()
    entry = ledger.add_session(
        report,
        trade_records=[_record("closed", 0.0)],
    )

    assert entry.total_trades == 1
    assert entry.wins == 0
    assert entry.losses == 0
    assert entry.breakevens == 1
    assert ledger.compute_cumulative()["total_trades"] == 1
