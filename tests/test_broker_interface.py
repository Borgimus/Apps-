"""Tests for broker interface and paper broker."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.brokers.broker_interface import (
    OptionContract,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.brokers.paper_broker import PaperBroker


# ── PaperBroker ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPaperBroker:

    async def test_initial_account(self):
        broker = PaperBroker(starting_equity=50_000.0)
        acct = await broker.get_account()
        assert acct.equity == Decimal("50000")
        assert acct.cash == Decimal("50000")
        assert acct.is_paper is True

    async def test_buy_order_fills_immediately(self, mock_option_contract):
        broker = PaperBroker(starting_equity=10_000.0)
        req = OrderRequest(
            symbol="SPY",
            option_symbol="SPY240102C00450000",
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("2.10"),
        )
        result = await broker.place_option_order(req)
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == 1
        assert result.filled_price is not None

    async def test_buy_order_deducts_cash(self):
        broker = PaperBroker(starting_equity=10_000.0)
        req = OrderRequest(
            symbol="SPY",
            option_symbol="SPY240102C00450000",
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("2.00"),
        )
        await broker.place_option_order(req)
        acct = await broker.get_account()
        # Cost = $2.00 × 100 = $200 + slippage
        assert acct.cash < Decimal("10000")
        assert acct.cash > Decimal("9700")  # rough check

    async def test_market_order_raises(self):
        broker = PaperBroker()
        req = OrderRequest(
            symbol="SPY",
            option_symbol="SPY240102C00450000",
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.MARKET,
            limit_price=Decimal("2.10"),
        )
        with pytest.raises(ValueError, match="Market orders"):
            await broker.place_option_order(req)

    async def test_insufficient_cash_rejected(self):
        broker = PaperBroker(starting_equity=100.0)  # only $100
        req = OrderRequest(
            symbol="SPY",
            option_symbol="SPY240102C00450000",
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("2.10"),  # costs $210
        )
        result = await broker.place_option_order(req)
        assert result.status == OrderStatus.REJECTED
        assert result.cancel_reason is not None

    async def test_positions_updated_after_buy(self):
        broker = PaperBroker(starting_equity=10_000.0)
        req = OrderRequest(
            symbol="SPY",
            option_symbol="SPY240102C00450000",
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("2.00"),
        )
        await broker.place_option_order(req)
        positions = await broker.get_positions()
        assert len(positions) == 1
        assert positions[0].option_symbol == "SPY240102C00450000"

    async def test_order_status_retrievable(self):
        broker = PaperBroker(starting_equity=10_000.0)
        req = OrderRequest(
            symbol="SPY",
            option_symbol="SPY240102C00450000",
            side=OrderSide.BUY_TO_OPEN,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("2.00"),
        )
        order = await broker.place_option_order(req)
        status = await broker.get_order_status(order.order_id)
        assert status.order_id == order.order_id
        assert status.status == OrderStatus.FILLED


# ── OptionContract helpers ────────────────────────────────────────────────────

class TestOptionContractHelpers:

    def test_mid_price(self, mock_option_contract):
        assert mock_option_contract.mid == Decimal("2.05")

    def test_spread_pct(self, mock_option_contract):
        # spread = (2.10 - 2.00) / 2.05 ≈ 0.0488
        pct = mock_option_contract.spread_pct
        assert 0.04 < pct < 0.06

    def test_wide_spread_contract(self):
        c = OptionContract(
            symbol="SPY",
            option_symbol="TEST",
            expiration=date(2024, 1, 2),
            strike=Decimal("450"),
            option_type="call",
            bid=Decimal("1.00"),
            ask=Decimal("3.00"),
            last=Decimal("2.00"),
            volume=100,
            open_interest=100,
            implied_volatility=0.20,
        )
        assert c.spread_pct > 0.9  # (3-1)/2 = 1.0

    def test_zero_ask_spread_is_inf(self):
        c = OptionContract(
            symbol="SPY",
            option_symbol="TEST",
            expiration=date(2024, 1, 2),
            strike=Decimal("450"),
            option_type="call",
            bid=Decimal("0"),
            ask=Decimal("0"),
            last=Decimal("0"),
            volume=0,
            open_interest=0,
            implied_volatility=0.0,
        )
        assert c.spread_pct == float("inf")
