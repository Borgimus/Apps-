"""Entries must fit inside the remaining daily-loss limit, stop loss included."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
from app.config import Settings
from app.risk.loss_capacity import exceeds_loss_capacity, worst_case_trade_loss
from app.risk.risk_manager import RiskCheck, RiskManager

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 10, 10, 30, tzinfo=ET)
EQUITY = Decimal("100000")


def test_worst_case_is_the_stop_loss_on_the_position():
    assert worst_case_trade_loss(2.00, 3, 0.5) == pytest.approx(300.0)
    assert worst_case_trade_loss(0.0, 3, 0.5) == 0.0


@pytest.mark.parametrize("realized, trade, limit, breach", [
    (-200, 100, 250, True),    # the defect: -200 realized, room for only 50
    (-100, 100, 250, False),
    (0, 260, 250, True),
    (50, 240, 250, False),     # gains do not create extra capacity
    (-200, 100, None, False),
])
def test_capacity(realized, trade, limit, breach):
    assert exceeds_loss_capacity(realized, trade, limit) is breach


def _risk(tmp_path, realized):
    s = Settings(_env_file=None, live_trading_enabled=False, broker="paper",
                 paper_evaluation_mode=True, paper_scaled_sizing_enabled=True,
                 paper_scaled_guardrails_enabled=True,
                 kill_switch_file=str(tmp_path / "KILL_SWITCH"))
    s.position.stop_loss_pct = 0.5
    risk = RiskManager(s)
    risk.start_session(EQUITY)
    if realized:
        risk.record_exit(Decimal(str(realized)), reason="eod_exit")
    return risk


def _order(limit="2.00", qty=1):
    return OrderRequest(symbol="SPY", option_symbol="SPY260810C00500000",
                        side=OrderSide.BUY_TO_OPEN, quantity=qty,
                        order_type=OrderType.LIMIT, limit_price=Decimal(limit))


def test_entry_that_could_breach_the_experiment_limit_is_refused(tmp_path):
    risk = _risk(tmp_path, -200)  # $50 of $250 remains; this trade risks $100
    result = risk.check_order(_order(), EQUITY, now=NOW, signal_direction="long")
    assert RiskCheck.EXPERIMENT_DAILY_LOSS in result.failed_checks
    assert any("remaining experiment loss capacity" in m for m in result.messages)


def test_entry_within_remaining_capacity_is_not_blocked_by_it(tmp_path):
    risk = _risk(tmp_path, -100)  # $150 remains; this trade risks $100
    result = risk.check_order(_order(), EQUITY, now=NOW, signal_direction="long")
    assert RiskCheck.EXPERIMENT_DAILY_LOSS not in result.failed_checks
