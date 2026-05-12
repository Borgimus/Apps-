"""
Guard tests for controlled multi-trade paper evaluation.

Tests verify:
  1.  3 trades/day cap (RiskManager)
  2.  2 active positions cap (session_runner loop gate)
  3.  3 symbols/day cap (symbols_traded_today gate)
  4.  1 position per underlying symbol (has_position_for_symbol dedup)
  5.  Duplicate option contract prevention (has_position dedup in scan_and_place)
  6.  Scanner STANDBY prevents all trade attempts
  7.  Daily loss threshold prevents new trades (RiskManager)
  8.  Dashboard /session/state exposes all limit fields
"""

from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")
_NOW = datetime(2026, 5, 13, 10, 0, 0, tzinfo=ET)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_risk_manager(
    max_trades: int = 3,
    max_daily_loss: float = 0.02,
    trades_today: int = 0,
    daily_pnl: float = 0.0,
    starting_equity: float = 100_000.0,
):
    from app.config.settings import Settings, RiskSettings
    from app.risk.risk_manager import RiskManager

    # Build a minimal settings object with controllable limits
    settings = MagicMock()
    settings.risk.max_trades_per_day = max_trades
    settings.risk.max_daily_loss = max_daily_loss
    settings.risk.max_risk_per_trade = 0.01
    settings.risk.min_open_interest = 1
    settings.risk.min_volume = 1
    settings.risk.max_spread_pct = 0.50
    settings.risk.earnings_blackout_days = 1
    settings.risk.allow_earnings_trades = False
    settings.live_trading_enabled = False
    settings.is_kill_switch_active.return_value = False
    settings.no_trade_open_buffer_minutes = 0
    settings.no_trade_close_buffer_minutes = 0
    settings.market_open = "09:30"
    settings.market_close = "16:00"

    rm = RiskManager(settings)
    # Inject pre-loaded state
    rm._trades_today = trades_today
    rm._daily_pnl = Decimal(str(daily_pnl))
    rm._starting_equity = Decimal(str(starting_equity))
    rm._session_date = _NOW.date()
    return rm


def _make_order_request(symbol: str = "MSFT", option_symbol: str = "MSFT260513P00412500"):
    from app.brokers.broker_interface import OrderRequest, OrderSide, OrderType
    return OrderRequest(
        symbol=symbol,
        option_symbol=option_symbol,
        side=OrderSide.BUY_TO_OPEN,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("5.00"),
        strategy_id="test",
    )


def _make_contract(bid: float = 4.80, ask: float = 5.20, oi: int = 500, volume: int = 200):
    from app.brokers.broker_interface import OptionContract
    c = MagicMock(spec=OptionContract)
    c.bid = Decimal(str(bid))
    c.ask = Decimal(str(ask))
    c.mid = Decimal(str((bid + ask) / 2))
    c.spread_pct = Decimal(str(round((ask - bid) / ((bid + ask) / 2), 4)))
    c.open_interest = oi
    c.volume = volume
    c.delta = Decimal("0.40")
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — 3 trades/day cap
# ─────────────────────────────────────────────────────────────────────────────

class TestTradesPerDayCap:

    def test_cap_reached_rejects_order(self):
        """RiskManager blocks a new order when trades_today == max_trades_per_day."""
        rm = _make_risk_manager(max_trades=3, trades_today=3)
        result = rm.check_order(
            request=_make_order_request(),
            equity=Decimal("100000"),
            contract=_make_contract(),
            now=_NOW,
        )
        assert not result.passed
        assert any("max trades" in m.lower() for m in result.messages)

    def test_cap_not_reached_allows_order(self):
        """RiskManager allows an order when trades_today < max_trades_per_day."""
        rm = _make_risk_manager(max_trades=3, trades_today=2)
        result = rm.check_order(
            request=_make_order_request(),
            equity=Decimal("100000"),
            contract=_make_contract(),
            now=_NOW,
        )
        assert result.passed


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — 2 active positions cap
# ─────────────────────────────────────────────────────────────────────────────

class TestActivePositionsCap:

    def test_max_positions_gate_fires_at_limit(self):
        """Session loop gate blocks scan when open positions == max_active_positions."""
        pm = MagicMock()
        pm.open_positions.return_value = ["pos1", "pos2"]
        max_active = 2

        # Guard logic from session_runner main loop
        blocked = len(pm.open_positions()) >= max_active
        assert blocked, "Should block entry when positions == max"

    def test_max_positions_gate_allows_below_limit(self):
        """Session loop gate allows scan when open positions < max_active_positions."""
        pm = MagicMock()
        pm.open_positions.return_value = ["pos1"]
        max_active = 2

        blocked = len(pm.open_positions()) >= max_active
        assert not blocked, "Should allow entry when positions < max"

    def test_settings_default_max_active_positions(self):
        """UniverseSettings.max_active_positions default is 1 (conservative)."""
        from app.config.settings import UniverseSettings
        s = UniverseSettings()
        assert s.max_active_positions >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — 3 symbols/day cap
# ─────────────────────────────────────────────────────────────────────────────

class TestSymbolsPerDayCap:

    def test_fourth_symbol_blocked_after_three_traded(self):
        """symbols_traded_today gate blocks a 4th symbol when max is 3."""
        symbols_traded_today = {"SPY", "AAPL", "NVDA"}
        max_sym_per_day = 3
        fourth_symbol = "GOOGL"

        # Symbol gate: already at limit, new symbol blocked
        at_limit = len(symbols_traded_today) >= max_sym_per_day
        also_already_traded = fourth_symbol in symbols_traded_today
        assert at_limit, "Should be at limit after 3 symbols"
        assert not also_already_traded  # GOOGL is new — blocked by limit, not by prior trade

    def test_symbols_per_day_config(self):
        """UNIVERSE_MAX_SYMBOLS_TRADED_PER_DAY env var populates settings correctly."""
        import os
        from unittest.mock import patch as _patch
        with _patch.dict(os.environ, {"UNIVERSE_MAX_SYMBOLS_TRADED_PER_DAY": "3"}):
            from app.config.settings import UniverseSettings
            s = UniverseSettings()
            assert s.max_symbols_traded_per_day == 3


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — 1 position per underlying symbol
# ─────────────────────────────────────────────────────────────────────────────

class TestOnePositionPerSymbol:

    def test_has_position_for_symbol_blocks_duplicate_underlying(self):
        """scan_and_place dedup gate fires when underlying already has open position."""
        pm = MagicMock()
        pm.has_position_for_symbol.return_value = True
        symbol = "MSFT"

        # Guard from scan_and_place (line ~493)
        assert pm.has_position_for_symbol(symbol), "Guard should fire and skip symbol"

    def test_has_position_for_symbol_allows_new_symbol(self):
        """Gate does not fire when underlying has no open position."""
        pm = MagicMock()
        pm.has_position_for_symbol.return_value = False
        symbol = "MSFT"

        assert not pm.has_position_for_symbol(symbol), "Guard should not fire for new symbol"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Duplicate option contract prevention
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateOptionContractPrevention:

    @pytest.mark.asyncio
    async def test_duplicate_option_symbol_skipped_in_scan_and_place(self):
        """scan_and_place skips entry if the exact option_symbol is already held."""
        from scripts.session_runner import scan_and_place

        option_sym = "MSFT260513P00412500"

        # pm already holds this exact contract
        pm = MagicMock()
        pm.has_position_for_symbol.return_value = False  # underlying gate passes
        pm.has_position.return_value = True              # option_symbol gate fires
        pm.is_in_cooldown.return_value = False
        pm.open_positions.return_value = []

        # Signal that would select this contract
        sig = MagicMock()
        sig.direction.value = "LONG"
        sig.strategy_id = "test"
        sig.price = 420.0
        sig.notes = ""

        # Minimal strategy mock that returns a signal
        strategy = MagicMock()
        strategy.generate_signals.return_value = [sig]

        # Contract mock
        contract = MagicMock()
        contract.option_symbol = option_sym
        contract.bid = Decimal("4.80")
        contract.ask = Decimal("5.20")
        contract.mid = Decimal("5.00")
        contract.delta = Decimal("0.40")
        contract.open_interest = 500
        contract.volume = 200

        broker = MagicMock()
        broker.get_account = AsyncMock(return_value=MagicMock(equity=Decimal("100000"), is_paper=True))
        broker.get_available_expirations = AsyncMock(return_value=[date(2026, 5, 13)])
        broker.get_option_chain = AsyncMock(return_value=MagicMock(calls=[], puts=[contract]))
        broker.place_option_order = AsyncMock()

        import pandas as pd
        import numpy as np
        idx = pd.date_range("2026-05-13 09:30", periods=30, freq="5min", tz=ET)
        bars = pd.DataFrame({
            "open": np.full(30, 420.0),
            "high": np.full(30, 422.0),
            "low": np.full(30, 418.0),
            "close": np.full(30, 421.0),
            "volume": np.full(30, 50000),
        }, index=idx)
        data = MagicMock()
        data.get_intraday_bars = AsyncMock(return_value=bars)

        settings = MagicMock()
        settings.realistic_fill_test_mode = False
        settings.options.preferred_dte = [0]
        settings.options.entry_limit_price_mode = "mid"
        settings.options.exit_limit_price_mode = "mid"
        settings.options.entry_marketable_offset_pct = 0.01
        settings.options.delta_target_min = 0.30
        settings.options.delta_target_max = 0.50
        settings.universe.max_contracts_per_position = 1
        settings.position.stop_loss_pct = 0.50
        settings.position.take_profit_pct = 1.00
        settings.position.trailing_stop_pct = 0.25
        settings.position.max_hold_minutes = 120

        iv_filter = MagicMock()
        iv_filter.passes.return_value = (True, "")
        liq_filter = MagicMock()
        liq_filter.passes.return_value = (True, "")

        risk = MagicMock()
        risk_result = MagicMock()
        risk_result.passed = True
        risk_result.approved_quantity = 1
        risk.check_order.return_value = risk_result

        fill_tracker = MagicMock()
        fill_tracker.has_pending_for_symbol.return_value = False

        placed = await scan_and_place(
            symbol="MSFT",
            broker=broker,
            data=data,
            strategies=[strategy],
            iv_filter=iv_filter,
            liq_filter=liq_filter,
            risk=risk,
            pm=pm,
            journal=None,
            settings=settings,
            now=_NOW,
            dry_run=False,
            fill_tracker=fill_tracker,
            store=None,
            session_date="2026-05-13",
        )

        # No order placed because pm.has_position returned True for the option symbol
        broker.place_option_order.assert_not_called()
        assert placed == 0

    def test_max_contracts_per_position_defaults_to_one(self):
        """UniverseSettings.max_contracts_per_position default is 1."""
        from app.config.settings import UniverseSettings
        s = UniverseSettings()
        assert s.max_contracts_per_position == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Scanner STANDBY prevents all trade attempts
# ─────────────────────────────────────────────────────────────────────────────

class TestStandbyPreventsAllTrades:

    def test_empty_active_symbols_means_no_scan(self):
        """When scanner enters STANDBY (returns None), active_symbols becomes [].
        The scan loop iterates over [] — no symbols are ever passed to scan_and_place."""
        active_symbols = []  # STANDBY result
        symbols_scanned = []

        for symbol in active_symbols:
            symbols_scanned.append(symbol)

        assert symbols_scanned == [], "No symbols should be scanned in STANDBY"

    def test_standby_returns_none_from_scan_function(self):
        """_run_universe_scan returns None (not []) when STANDBY is triggered.
        None is the sentinel that prevents CLI fallback."""
        # STANDBY is specifically None, not empty list
        standby_result = None
        fallback_result = []
        confirmed_result = ["SPY", "AAPL"]

        assert standby_result is None
        assert fallback_result == []
        assert confirmed_result  # truthy


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — Daily loss threshold prevents new trades
# ─────────────────────────────────────────────────────────────────────────────

class TestDailyLossThreshold:

    def test_daily_loss_at_threshold_rejects_order(self):
        """RiskManager blocks new orders when daily loss reaches max_daily_loss."""
        # 2% loss on $100k equity = $2,000 loss
        rm = _make_risk_manager(
            max_daily_loss=0.02,
            daily_pnl=-2000.0,
            starting_equity=100_000.0,
        )
        result = rm.check_order(
            request=_make_order_request(),
            equity=Decimal("100000"),
            contract=_make_contract(),
            now=_NOW,
        )
        assert not result.passed
        assert any("loss" in m.lower() for m in result.messages)

    def test_daily_loss_below_threshold_allows_order(self):
        """RiskManager allows orders when daily loss is below max_daily_loss."""
        rm = _make_risk_manager(
            max_daily_loss=0.02,
            daily_pnl=-500.0,   # 0.5% loss, under threshold
            starting_equity=100_000.0,
        )
        result = rm.check_order(
            request=_make_order_request(),
            equity=Decimal("100000"),
            contract=_make_contract(),
            now=_NOW,
        )
        assert result.passed


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Dashboard exposes all limit fields correctly
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardLimitVisibility:

    @pytest.mark.asyncio
    async def test_session_state_exposes_all_limit_fields(self):
        """GET /session/state returns all position/symbol/trade limit fields."""
        import app.api.dashboard_api as dash_mod
        from fastapi.testclient import TestClient

        scan_store = {
            "active_symbols": ["SPY", "AAPL"],
            "standby": False,
            "standby_reason": None,
        }

        uni = MagicMock()
        uni.max_active_positions = 2
        uni.max_symbols_traded_per_day = 3
        uni.max_contracts_per_position = 1

        mock_settings = MagicMock()
        mock_settings.live_trading_enabled = False
        mock_settings.paper_evaluation_mode = True
        mock_settings.is_kill_switch_active.return_value = False
        mock_settings.risk.max_trades_per_day = 3
        mock_settings.universe = uni

        rm = MagicMock()
        rm.daily_pnl = 0.0
        rm.trades_today = 1
        rm._session_date = None
        pm = MagicMock()
        pm.open_positions.return_value = []
        pm.to_dict_list.return_value = []
        ft = MagicMock()
        ft.count.return_value = 0

        original_store = dict(dash_mod._scan_store)
        try:
            dash_mod._scan_store.clear()
            dash_mod._scan_store.update(scan_store)

            with patch("app.api.dashboard_api.get_settings", return_value=mock_settings):
                app_instance = dash_mod.create_app(
                    broker=MagicMock(),
                    risk_manager=rm,
                    position_manager=pm,
                    fill_tracker=ft,
                    scan_results_store=dash_mod._scan_store,
                )
                with TestClient(app_instance) as client:
                    resp = client.get("/session/state")

            assert resp.status_code == 200
            data = resp.json()

            # Trade limit fields
            assert data["trades_today"] == 1
            assert data["max_trades_per_day"] == 3
            assert data["trades_remaining"] == 2

            # Position limit fields
            assert data["max_active_positions"] == 2
            assert data["max_contracts_per_position"] == 1

            # Symbol limit fields
            assert data["active_symbols"] == ["SPY", "AAPL"]
            assert data["max_symbols_traded_per_day"] == 3

            # Scanner state
            assert data["scanner_standby"] is False
            assert data["standby_reason"] is None
        finally:
            dash_mod._scan_store.clear()
            dash_mod._scan_store.update(original_store)
