from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.evaluation.daily_report import DailyReport, to_markdown
from app.evaluation.post_session import _ledger_file_for_settings
from app.risk.paper_sizing import (
    calculate_paper_scaled_quantity,
    normalized_one_contract_pnl,
)


@pytest.mark.parametrize(
    ("ask", "expected"),
    [
        ("0.10", 10),
        ("0.20", 10),
        ("0.50", 5),
        ("2.50", 1),
        ("2.51", 0),
        ("0", 0),
    ],
)
def test_calculate_paper_scaled_quantity(ask, expected):
    assert calculate_paper_scaled_quantity(ask, 250, 10) == expected


def test_quantity_respects_lower_contract_cap():
    assert calculate_paper_scaled_quantity("0.10", 250, 4) == 4


def test_normalized_one_contract_pnl():
    assert normalized_one_contract_pnl(40, 10) == 4.0
    assert normalized_one_contract_pnl(-15, 5) == -3.0


def test_scaled_sizing_requires_paper_evaluation_mode():
    with pytest.raises(ValidationError, match="requires paper_evaluation_mode"):
        Settings(
            paper_scaled_sizing_enabled=True,
            paper_evaluation_mode=False,
            live_trading_enabled=False,
        )


def test_scaled_sizing_rejects_live_mode():
    with pytest.raises(ValidationError, match="live_trading_enabled"):
        Settings(
            paper_scaled_sizing_enabled=True,
            paper_evaluation_mode=True,
            live_trading_enabled=True,
        )


def test_scaled_sizing_rejects_fill_test_mode():
    with pytest.raises(ValidationError, match="incompatible with realistic_fill_test_mode"):
        Settings(
            paper_scaled_sizing_enabled=True,
            paper_evaluation_mode=True,
            live_trading_enabled=False,
            realistic_fill_test_mode=True,
        )


def test_scaled_sizing_accepts_guarded_paper_mode():
    settings = Settings(
        paper_scaled_sizing_enabled=True,
        paper_evaluation_mode=True,
        live_trading_enabled=False,
        realistic_fill_test_mode=False,
        paper_scaled_premium_budget_dollars=250,
    )
    assert settings.paper_scaled_sizing_enabled is True


def test_scaled_cohort_uses_separate_ledger_file():
    settings = SimpleNamespace(
        evaluation_ledger_file="./evaluation/ledger.json",
        paper_scaled_sizing_enabled=True,
        paper_scaled_premium_budget_dollars=250,
        universe=SimpleNamespace(max_contracts_per_position=10),
    )
    assert _ledger_file_for_settings(settings).endswith(
        "ledger.paper_scaled_250_cap_10.json"
    )


def test_one_contract_cohort_keeps_original_ledger_file():
    settings = SimpleNamespace(
        evaluation_ledger_file="./evaluation/ledger.json",
        paper_scaled_sizing_enabled=False,
    )
    assert _ledger_file_for_settings(settings) == "./evaluation/ledger.json"


def test_markdown_labels_actual_and_normalized_pnl():
    report = DailyReport(
        date="2026-07-29",
        session_start=None,
        session_end=None,
        realized_pnl=40,
        one_contract_normalized_pnl=4,
        contracts_filled=10,
        sizing_cohort="paper_scaled_budget_250_cap_10",
        premium_budget_dollars=250,
        contract_cap=10,
        phase="phase3",
        evidence_type="scaled_paper_evaluation",
    )
    markdown = to_markdown(report)
    assert "Actual realized PnL | $40.00" in markdown
    assert "One-contract-normalized PnL | $4.00" in markdown
    assert "Separate paper-only scaled-sizing cohort" in markdown
