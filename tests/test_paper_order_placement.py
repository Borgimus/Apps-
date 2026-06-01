"""
End-to-end paper order placement tests.

These tests wire together the RiskManager + PaperBroker + strategies to
verify the full signal→order pipeline works correctly.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.brokers.broker_interface import (
    AccountInfo,
    OptionChain,
    OptionContract,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.brokers.paper_broker import PaperBroker
from app.risk import RiskManager
from app.strategies import (
    IVCrushFilter,
    LiquidityFilter,
    OpeningRangeBreakoutStrategy,
)
from app.strategies.strategy_base import Signal, SignalDirection

ET = ZoneInfo("America/New_York")
EQUITY = Decimal("50000")
MID_SESSION = datetime(2024, 1, 2, 11, 0, tzinfo=ET)


def _make_liquid_contract(oi: int = 500, vol: int = 200, spread: float = 0.05):
    bid = Decimal("2.00")
    ask = bid * Decimal(str(1 + spread))
    return OptionContract(
        symbol="SPY",
        option_symbol="SPY240102C00450000",
        expiration=date(2024, 1, 2),
        strike=Decimal("450"),
        option_type="call",
        bid=bid,
        ask=ask,
        last=bid,
        volume=vol,
        open_interest=oi,
        implied_volatility=0.20,
        delta=0.42,
    )


class TestFullOrderPipeline:

    @pytest.mark.asyncio
    async def test_valid_signal_places_order(self):
        contract = _make_liquid_contract()
        broker = PaperBroker(starting_equity=float(EQUITY))
        rm = RiskManager()
        rm.start_session(EQUITY)

        signal = Signal(
            strategy_id="orb",
            symbol="SPY",
            direction=SignalDirection.LONG,
            timestamp=MID_SESSION,
            price=449.0,
        )

        req = OrderRequest(
            symbol="SPY",
            option_symbol="SPY240102C00450000",
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=contract.ask,
        )

        risk_result = rm.check_order(req, EQUITY, contract, now=MID_SESSION)
        assert risk_result.passed, f"Unexpected rejection: {risk_result.messages}"

        order_result = await broker.place_option_order(req)
        assert order_result.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_kill_switch_blocks_pipeline(self, tmp_path):
        from app.config import Settings

        ks = tmp_path / "KS"
        ks.touch()
        settings = Settings(
            kill_switch_file=str(ks),
            live_trading_enabled=False,
            broker="paper",
            database_url="sqlite+aiosqlite:///:memory:",
        )
        rm = RiskManager(settings)
        rm.start_session(EQUITY)
        contract = _make_liquid_contract()

        req = OrderRequest(
            symbol="SPY",
            option_symbol="SPY240102C00450000",
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("2.10"),
        )
        risk_result = rm.check_order(req, EQUITY, contract, now=MID_SESSION)
        assert not risk_result.passed
        from app.risk.risk_manager import RiskCheck
        assert RiskCheck.KILL_SWITCH in risk_result.failed_checks

    def test_iv_crush_filter_removes_earnings_signals(self):
        iv_filter = IVCrushFilter({"earnings_blackout_days": 1})
        signals = [
            Signal("orb", "AAPL", SignalDirection.LONG, MID_SESSION, 185.0),
            Signal("orb", "SPY", SignalDirection.LONG, MID_SESSION, 450.0),
        ]
        earnings_cal = {"AAPL": [date(2024, 1, 2)]}
        filtered = iv_filter.apply(
            signals, earnings_cal, reference_date=date(2024, 1, 2)
        )
        symbols = [s.symbol for s in filtered]
        assert "AAPL" not in symbols
        assert "SPY" in symbols

    def test_liquidity_filter_selects_best_contract(self, mock_option_chain):
        lf = LiquidityFilter(
            {
                "min_open_interest": 100,
                "min_volume": 50,
                "max_spread_pct": 0.15,
                "delta_target_min": 0.35,
                "delta_target_max": 0.45,
            }
        )
        signal = Signal("orb", "SPY", SignalDirection.LONG, MID_SESSION, 450.0)
        contract = lf.select_contract(mock_option_chain, signal)
        assert contract is not None
        assert contract.option_type == "call"

    def test_liquidity_filter_rejects_bad_contracts(self):
        from app.brokers.broker_interface import OptionChain

        bad_chain = OptionChain(
            symbol="XYZ",
            expiration=date(2024, 1, 2),
            underlying_price=Decimal("100"),
            calls=[
                OptionContract(
                    symbol="XYZ",
                    option_symbol="XYZ240102C00100000",
                    expiration=date(2024, 1, 2),
                    strike=Decimal("100"),
                    option_type="call",
                    bid=Decimal("0.05"),
                    ask=Decimal("0.50"),  # 163% spread
                    last=Decimal("0.10"),
                    volume=2,    # below min_volume
                    open_interest=5,  # below min_oi
                    implied_volatility=0.50,
                )
            ],
        )
        lf = LiquidityFilter()
        signal = Signal("orb", "XYZ", SignalDirection.LONG, MID_SESSION, 100.0)
        contract = lf.select_contract(bad_chain, signal)
        assert contract is None

    @pytest.mark.asyncio
    async def test_daily_trade_counter_increments(self):
        rm = RiskManager()
        rm.start_session(EQUITY)
        assert rm.entries_today == 0
        assert rm.pending_entries == 0

        # Entry lifecycle: pending → filled
        rm.record_entry_pending()
        assert rm.pending_entries == 1
        assert rm.entries_today == 0
        rm.record_entry_filled()
        assert rm.pending_entries == 0
        assert rm.entries_today == 1
        assert rm.trades_today == 1  # backward-compat alias

        # Exit updates PnL but does not increment entries
        rm.record_exit(pnl=Decimal("150"))
        assert rm.entries_today == 1
        assert rm.exits_today == 1
        assert rm.daily_pnl == Decimal("150")
