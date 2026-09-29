"""
Safety guards that previously failed open.

1. Exit monitoring substituted the entry price when a quote failed, which made
   an open position look flat and silently disabled stop and trailing exits.
2. The earnings check returned "no earnings" whenever Yahoo was unreachable.
3. The stale-pending-order pre-flight check passed when it could not run.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import scripts.session_runner as runner
from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
from app.config import Settings
from app.evaluation.pre_session import _check_no_stale_pending_orders
from app.risk.risk_manager import RiskManager
from app.scanning.yfinance_scanner import YFinanceScanner
from app.trading.position_manager import PositionManager
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 29, 10, 0, tzinfo=ET)
SYM = "IWM261002C00250000"


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(runner.asyncio, "sleep", AsyncMock())
    runner._reset_data_feed_errors()
    yield
    runner._reset_data_feed_errors()


def _settings():
    s = Settings(_env_file=None, live_trading_enabled=False)
    s.position.stop_loss_pct = 0.5
    s.position.take_profit_pct = 1.0
    s.position.trailing_stop_pct = 0.25
    s.position.trailing_activation_pct = 0.25
    s.position.max_hold_minutes = 120
    s.position.eod_exit_time = "15:45"
    return s


def _pm(entry_time=T0, entry_price=2.00):
    pm = PositionManager(_settings())
    pm.open(SYM, "IWM", "orb", "long", entry_time, entry_price, 1)
    return pm


def _quote(bid, ask=None):
    ask = bid + 0.05 if ask is None else ask
    return SimpleNamespace(bid=bid, ask=ask, mid=(bid + ask) / 2, timestamp=None)


def _broker(*quotes):
    """Each element is a quote or an exception, returned in call order."""
    b = MagicMock()
    b.get_option_quote = AsyncMock(side_effect=list(quotes))
    b.place_option_order = AsyncMock(return_value=SimpleNamespace(order_id="exit-order-1"))
    return b


async def _cycle(pm, broker, now):
    return await runner.monitor_positions(
        broker=broker, pm=pm, journal=None, risk=MagicMock(), now=now, dry_run=False,
    )


def _down():
    return RuntimeError("401 Unauthorized")


class TestUnpricedExposure:

    @pytest.mark.asyncio
    async def test_quote_failure_keeps_last_valid_mark_instead_of_entry(self):
        pm = _pm()
        await _cycle(pm, _broker(_quote(1.10)), T0 + timedelta(minutes=1))
        pos = pm.get_position(SYM)
        assert pos.current_price == pytest.approx(1.10)

        # Three attempts inside _retry, all failing.
        await _cycle(pm, _broker(_down(), _down(), _down()), T0 + timedelta(minutes=2))

        assert pos.current_price == pytest.approx(1.10), "must not reset to entry price"
        assert pos.trough_price == pytest.approx(1.10)
        assert pos.unpriced_since == T0 + timedelta(minutes=2)
        assert runner._data_feed_errors == 1
        assert runner._data_feed_error_labels == [f"exit_quote({SYM})"]
        assert not pos.exit_pending

    @pytest.mark.asyncio
    async def test_stop_level_mark_still_exits_when_the_next_quote_fails(self):
        pm = _pm()
        pos = pm.get_position(SYM)
        # A breached stop recorded as the last valid mark (e.g. after a restart
        # mid-cycle) is enforced from that mark, never reset to entry.
        pos.current_price = 0.90
        await _cycle(pm, _broker(_down(), _down(), _down()), T0 + timedelta(minutes=2))
        assert pos.exit_pending and pos.exit_triggered_reason == "stop_loss"

    @pytest.mark.asyncio
    async def test_prolonged_unpriced_exposure_forces_mandatory_exit(self):
        pm = _pm()
        pos = pm.get_position(SYM)
        await _cycle(pm, _broker(_down(), _down(), _down()), T0 + timedelta(minutes=1))
        assert not pos.exit_pending

        later = T0 + timedelta(minutes=1, seconds=runner._MAX_UNPRICED_EXPOSURE_SECONDS)
        # Three failures for monitoring, three for the exit-order quote refresh.
        broker = _broker(*[_down()] * 6)
        await _cycle(pm, broker, later)

        assert pos.exit_pending
        assert pos.exit_triggered_reason == "quote_unavailable"
        assert pos.exit_is_mandatory
        req = broker.place_option_order.await_args.args[0]
        assert req.notes == "exit:quote_unavailable"
        # With no quote, the limit is priced from the last valid mark, not zero.
        assert float(req.limit_price) == pytest.approx(round(2.00 * 0.98, 2))

    @pytest.mark.asyncio
    async def test_time_based_exit_still_fires_while_unpriced(self):
        pm = _pm(entry_time=T0 - timedelta(minutes=121))
        pos = pm.get_position(SYM)
        await _cycle(pm, _broker(*[_down()] * 6), T0)
        assert pos.exit_pending and pos.exit_triggered_reason == "max_hold"

    @pytest.mark.asyncio
    async def test_valid_quote_clears_unpriced_state(self):
        pm = _pm()
        pos = pm.get_position(SYM)
        await _cycle(pm, _broker(_down(), _down(), _down()), T0 + timedelta(minutes=1))
        assert pos.unpriced_since is not None
        await _cycle(pm, _broker(_quote(1.95)), T0 + timedelta(minutes=2))
        assert pos.unpriced_since is None
        assert pos.current_price == pytest.approx(1.95)

    @pytest.mark.asyncio
    async def test_zero_bid_and_zero_mid_is_unpriced_not_flat(self):
        pm = _pm()
        pos = pm.get_position(SYM)
        await _cycle(pm, _broker(_quote(0.0, 0.0)), T0 + timedelta(minutes=1))
        assert pos.unpriced_since is not None
        assert runner._data_feed_errors == 1


class TestEarningsStatus:

    def test_unreadable_calendar_is_unknown_not_clear(self):
        class Ticker:
            ticker = "NVDA"

            @property
            def calendar(self):
                raise ConnectionError("yahoo unreachable")

        assert YFinanceScanner._check_earnings(Ticker(), date(2026, 9, 29)) is None

    def test_readable_calendar_still_reports_true_and_false(self):
        today = date(2026, 9, 29)
        assert YFinanceScanner._check_earnings(
            SimpleNamespace(calendar={"Earnings Date": [today]}), today) is True
        assert YFinanceScanner._check_earnings(
            SimpleNamespace(calendar={}), today) is False

    def _risk(self, allow=False):
        s = Settings(_env_file=None, live_trading_enabled=False)
        s.risk.allow_earnings_trades = allow
        return RiskManager(settings=s)

    def _messages(self, risk, status):
        req = OrderRequest(symbol="NVDA", option_symbol="NVDA261002C00200000",
                           side=OrderSide.BUY_TO_OPEN, quantity=1,
                           order_type=OrderType.LIMIT, limit_price=Decimal("1.00"))
        result = risk.check_order(request=req, equity=Decimal("100000"),
                                  now=T0, earnings_status=status)
        return [m for m in result.messages if "earnings" in m]

    @pytest.mark.parametrize("status", ["unknown", "earnings"])
    def test_unverified_or_confirmed_earnings_blocks_broker_entry(self, status):
        assert self._messages(self._risk(), status)

    def test_clear_status_does_not_block(self):
        assert self._messages(self._risk(), "clear") == []

    def test_explicit_allow_setting_permits_unknown(self):
        assert self._messages(self._risk(allow=True), "unknown") == []


class TestStalePendingOrderCheck:

    @pytest.mark.asyncio
    async def test_check_that_cannot_run_fails_and_is_required(self):
        session = MagicMock()
        session.execute = AsyncMock(side_effect=RuntimeError("database locked"))
        result = await _check_no_stale_pending_orders(session)
        assert result.passed is False
        assert result.required is True
        assert "could not run" in result.message
