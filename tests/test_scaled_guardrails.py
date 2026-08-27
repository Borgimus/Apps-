"""Regression coverage for the amended scaled-sizing cohort."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.brokers.broker_interface import (
    OptionChain,
    OptionContract,
    OrderRequest,
    OrderSide,
    OrderType,
)
from app.brokers.alpaca_broker import AlpacaBroker
from app.config import Settings
from app.risk import RiskCheck, RiskManager
from app.scanning.alpaca_confirmer import AlpacaConfirmer
from app.scanning.universe_loader import UniverseLoader
from app.trading.health_report import HealthReporter
from app.trading.entry_filters import (
    completed_intraday_bars,
    select_expiration_for_settings,
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
    assert (settings.paper_scaled_min_dte, settings.paper_scaled_max_dte) == (2, 8)
    assert scaled_entry_block_reason(settings, "QQQ", "orb") == "symbol_disabled"
    assert (
        scaled_entry_block_reason(settings, "SPY", "vwap_reclaim")
        == "strategy_shadow_only"
    )
    assert scaled_entry_block_reason(settings, "IWM", "orb") == "strategy_shadow_only"


def test_non_scaled_mode_does_not_apply_shadow_only_veto():
    settings = _settings(paper_scaled_sizing_enabled=False)
    assert scaled_entry_block_reason(settings, "QQQ", "orb") is None


def test_allowed_expiration_is_fail_closed_to_configured_dte_window():
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


def test_weekly_expiration_remains_eligible_late_in_week():
    today = date(2026, 8, 27)  # Thursday
    settings = _settings()
    expirations = [date(2026, 8, 28), date(2026, 9, 4)]

    assert (
        select_expiration_for_settings(expirations, today, settings)
        == date(2026, 9, 4)
    )


def test_default_runtime_scans_all_38_research_symbols():
    settings = _settings()
    loader = UniverseLoader(path=settings.universe.file)
    loader.load()
    groups = [
        group.strip()
        for group in settings.universe.groups_enabled.split(",")
        if group.strip()
    ]
    symbols = loader.get_symbols_with_groups(
        enabled_groups=groups,
        max_per_group=settings.universe.max_per_group,
        max_total=settings.universe.max_total_symbols,
        max_symbols=settings.universe.max_symbols_per_scan,
    )

    assert settings.universe.mode == "grouped"
    assert settings.universe.max_symbols_per_scan == 40
    assert settings.universe.max_active_symbols == 6
    assert len(symbols) == 38
    assert set(symbols.values()) == {
        "core_etfs",
        "mega_cap",
        "liquid_growth",
        "high_beta_liquid",
    }


def test_shadow_cohort_rejects_narrow_runtime_universe():
    with pytest.raises(ValueError, match="at least 38 scanned symbols"):
        _settings(
            universe={
                "mode": "grouped",
                "file": "./config/ticker_universe.yaml",
                "max_symbols_per_scan": 10,
                "max_active_symbols": 6,
                "groups_enabled": (
                    "core_etfs,mega_cap,liquid_growth,high_beta_liquid"
                ),
            }
        )


def _confirmed_chain(expiration: date, delta: float | None) -> OptionChain:
    contract = OptionContract(
        symbol="SPY",
        option_symbol=f"SPY{expiration.strftime('%y%m%d')}C00700000",
        expiration=expiration,
        strike=Decimal("700"),
        option_type="call",
        bid=Decimal("1.00"),
        ask=Decimal("1.05"),
        last=Decimal("1.02"),
        volume=500,
        open_interest=1000,
        implied_volatility=0.20,
        delta=delta,
    )
    return OptionChain(
        symbol="SPY",
        expiration=expiration,
        underlying_price=Decimal("700"),
        calls=[contract],
        puts=[],
        fetched_at=datetime.now(tz=ET),
    )


def _scan_candidate():
    candidate = MagicMock()
    candidate.symbol = "SPY"
    candidate.score = 80.0
    candidate.signal_type = "LONG"
    candidate.is_rejected = False
    candidate.metrics.price = 700.0
    return candidate


@pytest.mark.asyncio
async def test_confirmer_uses_same_dte_and_delta_policy_as_entry_path():
    today = datetime.now(tz=ET).date()
    expiration = today + timedelta(days=4)
    settings = _settings()
    broker = MagicMock()
    broker.get_available_expirations = AsyncMock(return_value=[expiration])
    broker.get_option_chain = AsyncMock(return_value=_confirmed_chain(expiration, 0.40))

    confirmed = await AlpacaConfirmer(broker, settings).confirm(_scan_candidate())

    assert confirmed is not None
    assert confirmed.expiration == expiration


@pytest.mark.asyncio
async def test_confirmer_rejects_missing_delta_and_one_dte():
    today = datetime.now(tz=ET).date()
    eligible = today + timedelta(days=4)
    settings = _settings()

    missing_delta_broker = MagicMock()
    missing_delta_broker.get_available_expirations = AsyncMock(return_value=[eligible])
    missing_delta_broker.get_option_chain = AsyncMock(
        return_value=_confirmed_chain(eligible, None)
    )
    assert (
        await AlpacaConfirmer(missing_delta_broker, settings).confirm(_scan_candidate())
        is None
    )

    one_dte_broker = MagicMock()
    one_dte_broker.get_available_expirations = AsyncMock(
        return_value=[today + timedelta(days=1)]
    )
    one_dte_broker.get_option_chain = AsyncMock()
    assert await AlpacaConfirmer(one_dte_broker, settings).confirm(_scan_candidate()) is None
    one_dte_broker.get_option_chain.assert_not_awaited()


@pytest.mark.asyncio
async def test_alpaca_contract_discovery_follows_pagination():
    first = MagicMock()
    first.raise_for_status = MagicMock()
    first.json.return_value = {
        "option_contracts": [{"symbol": "SPY-FIRST"}],
        "next_page_token": "page-2",
    }
    second = MagicMock()
    second.raise_for_status = MagicMock()
    second.json.return_value = {
        "option_contracts": [{"symbol": "SPY-SECOND"}],
        "next_page_token": None,
    }
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = MagicMock()
    broker._client.get = AsyncMock(side_effect=[first, second])

    contracts = await broker._get_option_contracts({"underlying_symbols": "SPY"})

    assert [row["symbol"] for row in contracts] == ["SPY-FIRST", "SPY-SECOND"]
    assert broker._client.get.await_count == 2
    second_params = broker._client.get.await_args_list[1].kwargs["params"]
    assert second_params["page_token"] == "page-2"
    assert second_params["limit"] == 10_000


@pytest.mark.asyncio
async def test_alpaca_expiration_discovery_requests_future_window():
    today = datetime.now(tz=ET).date()
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._get_option_contracts = AsyncMock(
        return_value=[
            {"expiration_date": (today + timedelta(days=4)).isoformat()},
            {"expiration_date": (today + timedelta(days=8)).isoformat()},
        ]
    )

    expirations = await broker.get_available_expirations("SPY")
    cached_expirations = await broker.get_available_expirations("SPY")

    assert expirations == [today + timedelta(days=4), today + timedelta(days=8)]
    assert cached_expirations == expirations
    assert broker._get_option_contracts.await_count == 1
    params = broker._get_option_contracts.await_args.args[0]
    assert params["expiration_date_gte"] == today.isoformat()
    assert params["expiration_date_lte"] == (
        today + timedelta(days=broker._OPTION_EXPIRATION_LOOKAHEAD_DAYS)
    ).isoformat()


def test_incomplete_five_minute_bar_is_excluded():
    idx = pd.date_range("2026-08-26 09:30", periods=3, freq="5min", tz=ET)
    bars = pd.DataFrame({"close": [1, 2, 3]}, index=idx)
    completed = completed_intraday_bars(
        bars, datetime(2026, 8, 26, 9, 42, tzinfo=ET), interval_minutes=5
    )
    assert list(completed["close"]) == [1, 2]
