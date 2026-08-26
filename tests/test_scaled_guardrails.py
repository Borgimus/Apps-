"""Regression coverage for the amended scaled-sizing cohort."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
from app.config import Settings
from app.risk import RiskCheck, RiskManager
from app.trading.health_report import HealthReporter
from app.trading.entry_filters import (
    completed_intraday_bars,
    scaled_entry_block_reason,
    select_allowed_expiration,
)
from scripts.session_runner import _market_regime_from_bars, _rank_active_symbols

ET = ZoneInfo("America/New_York")
SAFE_NOW = datetime(2026, 8, 10, 10, 30, tzinfo=ET)
EQUITY = Decimal("100000")


def _settings(**overrides) -> Settings:
    values = {
        "live_trading_enabled": False,
        "broker": "paper",
        "paper_evaluation_mode": True,
        "paper_scaled_sizing_enabled": True,
        "paper_scaled_guardrails_enabled": True,
        "kill_switch_file": "./DOES_NOT_EXIST_SCALED_GUARD_KILL_SWITCH",
    }
    values.update(overrides)
    return Settings(**values)


def _order(symbol: str = "SPY") -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        option_symbol=f"{symbol}260810C00100000",
        side=OrderSide.BUY_TO_OPEN,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1.00"),
    )


def test_same_direction_correlated_stop_blocks_next_index(mock_option_contract):
    risk = RiskManager(_settings())
    risk.start_session(EQUITY)
    risk.record_exit(
        Decimal("-66"),
        symbol="IWM",
        direction="long",
        reason="stop_loss",
    )

    result = risk.check_order(
        _order("SPY"),
        EQUITY,
        mock_option_contract,
        now=SAFE_NOW,
        signal_direction="long",
    )

    assert RiskCheck.CORRELATED_STOP_LOCK in result.failed_checks


def test_correlated_stop_does_not_block_opposite_direction(mock_option_contract):
    risk = RiskManager(_settings())
    risk.start_session(EQUITY)
    risk.record_exit(
        Decimal("-66"),
        symbol="IWM",
        direction="long",
        reason="stop_loss",
    )

    result = risk.check_order(
        _order("QQQ"),
        EQUITY,
        mock_option_contract,
        now=SAFE_NOW,
        signal_direction="short",
    )

    assert RiskCheck.CORRELATED_STOP_LOCK not in result.failed_checks


def test_two_losing_exits_block_another_entry(mock_option_contract):
    risk = RiskManager(_settings())
    risk.start_session(EQUITY)
    risk.record_exit(Decimal("-40"), reason="trailing_stop")
    risk.record_exit(Decimal("-30"), reason="eod_exit")

    result = risk.check_order(
        _order(),
        EQUITY,
        mock_option_contract,
        now=SAFE_NOW,
        signal_direction="long",
    )

    assert RiskCheck.EXPERIMENT_LOSS_COUNT in result.failed_checks


def test_experiment_dollar_limit_uses_budget_scale(mock_option_contract):
    risk = RiskManager(_settings())
    risk.start_session(EQUITY)
    risk.record_exit(Decimal("-250"), reason="eod_exit")

    result = risk.check_order(
        _order(),
        EQUITY,
        mock_option_contract,
        now=SAFE_NOW,
        signal_direction="long",
    )

    assert RiskCheck.EXPERIMENT_DAILY_LOSS in result.failed_checks


def test_guardrails_do_not_change_non_scaled_mode(mock_option_contract):
    risk = RiskManager(_settings(paper_scaled_sizing_enabled=False))
    risk.start_session(EQUITY)
    risk.record_exit(Decimal("-250"), reason="stop_loss")

    result = risk.check_order(
        _order(),
        EQUITY,
        mock_option_contract,
        now=SAFE_NOW,
        signal_direction="long",
    )

    assert RiskCheck.EXPERIMENT_DAILY_LOSS not in result.failed_checks
    assert RiskCheck.CORRELATED_STOP_LOCK not in result.failed_checks


def test_global_ranking_uses_score_then_rvol():
    store = {
        "candidates": [
            {"symbol": "SPY", "score": 70, "rvol": 1.2},
            {"symbol": "QQQ", "score": 80, "rvol": 1.0},
            {"symbol": "IWM", "score": 80, "rvol": 2.0},
        ]
    }

    assert _rank_active_symbols(["SPY", "QQQ", "IWM"], store) == [
        "IWM",
        "QQQ",
        "SPY",
    ]


def _bars(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2026-08-10 13:30", periods=len(closes), freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.1 for value in closes],
            "low": [value - 0.1 for value in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
        },
        index=index,
    )


def test_market_regime_requires_vwap_and_ema_alignment():
    assert _market_regime_from_bars(_bars([100, 101, 102, 103, 104])) == "long"
    assert _market_regime_from_bars(_bars([104, 103, 102, 101, 100])) == "short"


def test_health_drawdown_is_dollars_even_when_session_never_has_a_profit():
    assert HealthReporter._max_drawdown([-66.0, -98.0, -80.0]) == 244.0


def test_scaled_cohort_blocks_qqq_and_keeps_current_strategies_shadow_only():
    settings = _settings()
    assert settings.paper_scaled_guardrail_cohort == "guardrails_v3_shadow_validation"
    assert settings.paper_scaled_require_delta is True
    assert (settings.paper_scaled_min_dte, settings.paper_scaled_max_dte) == (2, 5)
    assert scaled_entry_block_reason(settings, "QQQ", "orb") == "symbol_disabled"
    assert (
        scaled_entry_block_reason(settings, "SPY", "vwap_reclaim")
        == "strategy_shadow_only"
    )
    assert scaled_entry_block_reason(settings, "IWM", "orb") == "strategy_shadow_only"


def test_non_scaled_mode_does_not_apply_shadow_only_veto():
    settings = _settings(paper_scaled_sizing_enabled=False)
    assert scaled_entry_block_reason(settings, "QQQ", "orb") is None


def test_allowed_expiration_is_fail_closed_to_two_through_five_dte():
    today = date(2026, 8, 26)
    expirations = [
        date(2026, 8, 26),
        date(2026, 8, 27),
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 4),
    ]
    selected = select_allowed_expiration(
        expirations, today, [0, 1, 2], min_dte=2, max_dte=5
    )
    assert selected == date(2026, 8, 28)
    assert select_allowed_expiration(
        expirations[:2], today, [0, 1, 2], min_dte=2, max_dte=5
    ) is None


def test_incomplete_five_minute_bar_is_excluded():
    idx = pd.date_range("2026-08-26 09:30", periods=3, freq="5min", tz=ET)
    bars = pd.DataFrame({"close": [1, 2, 3]}, index=idx)
    completed = completed_intraday_bars(
        bars, datetime(2026, 8, 26, 9, 42, tzinfo=ET), interval_minutes=5
    )
    assert list(completed["close"]) == [1, 2]
