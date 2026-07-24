from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.brokers.broker_interface import OptionChain, OptionContract
from app.strategies.liquidity_filter import LiquidityFilter
from app.strategies.strategy_base import Signal, SignalDirection
from app.trading.position_manager import PositionManager
from app.trading.tradier_contract_gate import (
    TradierContractGate,
    TradierGateDecision,
)
from app.utils.logging_setup import _stream_targets_file
from scripts.capture_session_fingerprint import _is_runtime_path

ET = ZoneInfo("America/New_York")


def _position_settings() -> SimpleNamespace:
    return SimpleNamespace(
        position=SimpleNamespace(
            stop_loss_pct=0.50,
            take_profit_pct=1.00,
            trailing_stop_pct=0.25,
            trailing_activation_pct=0.25,
            max_hold_minutes=120,
            eod_exit_time="12:30",
            cooldown_after_loss_minutes=15,
        )
    )


def test_trailing_stop_is_not_active_at_entry(monkeypatch):
    monkeypatch.delenv("POSITION_TRAILING_ACTIVATION_PCT", raising=False)
    manager = PositionManager(settings=_position_settings())
    now = datetime(2026, 7, 23, 10, 0, tzinfo=ET)
    position = manager.open(
        option_symbol="SPY260723P00732000",
        symbol="SPY",
        strategy_id="vwap_reclaim",
        direction="short",
        entry_time=now,
        entry_price=0.17,
        quantity=1,
    )

    manager.update_price(position.option_symbol, 0.12)

    assert position.trailing_stop_armed is False
    assert manager.should_exit(position.option_symbol, 0.12, now) is None


def test_trailing_stop_arms_after_favorable_move(monkeypatch):
    monkeypatch.delenv("POSITION_TRAILING_ACTIVATION_PCT", raising=False)
    manager = PositionManager(settings=_position_settings())
    now = datetime(2026, 7, 23, 10, 0, tzinfo=ET)
    position = manager.open(
        option_symbol="SPY260723P00732000",
        symbol="SPY",
        strategy_id="vwap_reclaim",
        direction="short",
        entry_time=now,
        entry_price=0.20,
        quantity=1,
    )

    manager.update_price(position.option_symbol, 0.26)
    assert position.trailing_stop_armed is True
    assert position.trailing_stop_level == pytest.approx(0.195)
    assert manager.should_exit(
        position.option_symbol,
        0.19,
        now,
    ) == "trailing_stop"


def _contract() -> OptionContract:
    return OptionContract(
        symbol="SPY",
        option_symbol="SPY260723P00732000",
        expiration=date(2026, 7, 23),
        strike=Decimal("732"),
        option_type="put",
        bid=Decimal("0.18"),
        ask=Decimal("0.19"),
        last=Decimal("0.18"),
        volume=9475,
        open_interest=2803,
        implied_volatility=0.0,
        delta=None,
    )


def _thresholds() -> dict:
    return {
        "min_open_interest": 100,
        "min_volume": 50,
        "max_spread_pct": 0.10,
        "delta_target_min": 0.35,
        "delta_target_max": 0.45,
        "max_contract_cost": 1000,
    }


def _chain_and_signal() -> tuple[OptionChain, Signal]:
    contract = _contract()
    chain = OptionChain(
        symbol="SPY",
        expiration=contract.expiration,
        underlying_price=Decimal("732"),
        puts=[contract],
        fetched_at=datetime.now(tz=timezone.utc),
    )
    signal = Signal(
        strategy_id="vwap_reclaim",
        symbol="SPY",
        direction=SignalDirection.SHORT,
        timestamp=datetime.now(tz=ET),
        price=732.0,
    )
    return chain, signal


def test_tradier_gate_vetoes_low_delta_contract(tmp_path):
    gate = TradierContractGate(
        token="test-token",
        mode="veto",
        output_file=str(tmp_path / "comparisons.jsonl"),
    )
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    gate._fetch_quote = lambda option_symbol: (  # type: ignore[method-assign]
        {
            "symbol": option_symbol,
            "bid": 0.16,
            "ask": 0.17,
            "volume": 14688,
            "open_interest": 4385,
            "bid_date": now_ms,
            "ask_date": now_ms,
            "greeks": {"delta": -0.0274, "smv_vol": 0.214},
        },
        {"available": "119"},
    )

    decision = gate.validate_selected_contract(
        contract=_contract(),
        underlying_symbol="SPY",
        strategy_id="vwap_reclaim",
        direction="short",
        thresholds=_thresholds(),
    )

    assert decision.enabled is True
    assert decision.passed is False
    assert "delta abs=0.027" in decision.reason


def test_liquidity_filter_returns_none_when_tradier_vetoes(monkeypatch):
    class RejectingGate:
        def validate_selected_contract(self, **kwargs):
            return TradierGateDecision(
                enabled=True,
                passed=False,
                reason="delta outside target",
            )

    monkeypatch.setattr(
        "app.trading.tradier_contract_gate.get_tradier_contract_gate",
        lambda: RejectingGate(),
    )

    liquidity_filter = LiquidityFilter(_thresholds())
    chain, signal = _chain_and_signal()

    assert liquidity_filter.select_contract(chain, signal) is None
    assert (
        liquidity_filter.classify_no_contract_reason(chain, signal)
        == "tradier_contract_veto"
    )


def test_disabled_gate_invocation_error_preserves_legacy_selection(monkeypatch):
    monkeypatch.setenv("TRADIER_CONTRACT_GATE_MODE", "off")

    def broken_gate():
        raise RuntimeError("gate unavailable")

    monkeypatch.setattr(
        "app.trading.tradier_contract_gate.get_tradier_contract_gate",
        broken_gate,
    )

    liquidity_filter = LiquidityFilter(_thresholds())
    chain, signal = _chain_and_signal()

    selected = liquidity_filter.select_contract(chain, signal)
    assert selected is not None
    assert selected.option_symbol == "SPY260723P00732000"


def test_enabled_gate_invocation_error_is_fail_closed(monkeypatch):
    monkeypatch.setenv("TRADIER_CONTRACT_GATE_MODE", "veto")

    def broken_gate():
        raise RuntimeError("gate unavailable")

    monkeypatch.setattr(
        "app.trading.tradier_contract_gate.get_tradier_contract_gate",
        broken_gate,
    )

    liquidity_filter = LiquidityFilter(_thresholds())
    chain, signal = _chain_and_signal()

    assert liquidity_filter.select_contract(chain, signal) is None
    assert (
        liquidity_filter.classify_no_contract_reason(chain, signal)
        == "tradier_contract_gate_error"
    )


def test_fingerprint_ignores_only_runtime_logs():
    assert _is_runtime_path("logs/session_2026-07-23.log") is True
    assert _is_runtime_path("logs/trading.jsonl") is True
    assert _is_runtime_path("app/trading/position_manager.py") is False
    assert _is_runtime_path("config.yaml") is False


def test_logging_detects_existing_stream_redirection(tmp_path):
    session_log = tmp_path / "session_2026-07-23.log"
    with session_log.open("w", encoding="utf-8") as handle:
        assert _stream_targets_file(handle, session_log) is True
