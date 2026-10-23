"""
Shadow book tests: capacity-blocked signals are logged and simulated with the
same exit rules the live PositionManager uses; executed signals are logged but
never simulated; results are persisted separately from broker records.
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from app.brokers.broker_interface import OptionChain, OptionContract
from app.config import Settings
from app.evaluation.shadow_book import (
    CAPACITY_REASONS,
    ShadowBook,
    select_shadow_contract,
)
from app.strategies.liquidity_filter import LiquidityFilter
from app.strategies.strategy_base import Signal, SignalDirection
from app.trading.entry_filters import liquidity_filter_params

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 20, 10, 30, tzinfo=ET)


def _settings():
    s = MagicMock()
    s.position.stop_loss_pct = 0.50
    s.position.take_profit_pct = 1.00
    s.position.trailing_stop_pct = 0.25
    s.position.max_hold_minutes = 120
    s.position.eod_exit_time = "15:45"
    s.paper_scaled_exit_variant_shadow_enabled = False
    s.paper_scaled_exit_variant_trigger_pct = 0.25
    s.paper_scaled_exit_variant_partial_fraction = 0.50
    return s


def _book(tmp_path) -> ShadowBook:
    return ShadowBook(
        _settings(),
        events_path=tmp_path / "shadow_book.jsonl",
        state_path=tmp_path / "shadow_state.json",
        fill_window_minutes=10,
    )


def _quote_broker(bid: float, ask: float, at=NOW):
    quote = MagicMock()
    quote.bid = bid
    quote.ask = ask
    quote.timestamp = at
    quote.feed = "opra"
    broker = MagicMock()
    broker.get_option_quote = AsyncMock(return_value=quote)
    return broker


def _events(tmp_path):
    p = Path(tmp_path / "shadow_book.jsonl")
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


NOW = datetime(2026, 7, 20, 10, 30, tzinfo=ET)


def _record(sb, **kwargs):
    # Trusted quote fixtures; missing/stale evidence is covered separately.
    now = kwargs["now"]
    ask = kwargs.setdefault("entry_ask", kwargs.get("limit_price"))
    kwargs.setdefault("contract_metadata", {
        "bid": (ask * 0.95) if ask else 0, "quote_timestamp": now.isoformat(),
        "quote_feed": "opra", "liquidity_passed": True,
    })
    return sb.record_signal(**kwargs)


class TestRecording:

    @pytest.mark.asyncio
    async def test_eligible_contract_opens_priceable_shadow_position(self, tmp_path):
        settings = Settings(
            live_trading_enabled=False,
            broker="paper",
            paper_evaluation_mode=True,
            paper_scaled_sizing_enabled=True,
            paper_scaled_guardrails_enabled=True,
            paper_scaled_min_dte=2,
            paper_scaled_max_dte=8,
            kill_switch_file=str(tmp_path / "NO_KILL_SWITCH"),
        )
        expiration = NOW.date() + timedelta(days=4)
        contract = OptionContract(
            symbol="SPY",
            option_symbol=f"SPY{expiration.strftime('%y%m%d')}C00600000",
            expiration=expiration,
            strike=Decimal("600"),
            option_type="call",
            bid=Decimal("0.40"),
            ask=Decimal("0.42"),
            last=Decimal("0.41"),
            volume=500,
            open_interest=1000,
            implied_volatility=0.20,
            delta=0.40,
        )
        chain = OptionChain(
            symbol="SPY",
            expiration=expiration,
            underlying_price=Decimal("600"),
            calls=[contract],
            puts=[],
            fetched_at=NOW,
        )
        broker = MagicMock()
        broker.get_available_expirations = AsyncMock(return_value=[expiration])
        broker.get_option_chain = AsyncMock(return_value=chain)
        liq_filter = LiquidityFilter(liquidity_filter_params(settings))
        signal = Signal(
            strategy_id="orb",
            symbol="SPY",
            direction=SignalDirection.LONG,
            timestamp=NOW,
            price=600.0,
        )

        option_symbol, limit_price, entry_ask = await select_shadow_contract(
            broker,
            liq_filter,
            settings,
            "SPY",
            signal,
            NOW,
        )

        assert option_symbol == contract.option_symbol
        assert limit_price is not None and limit_price > 0
        book = ShadowBook(
            settings,
            events_path=tmp_path / "strict_shadow.jsonl",
            state_path=tmp_path / "strict_shadow_state.json",
        )
        book.record_signal(
            now=NOW,
            strategy_id="orb",
            symbol="SPY",
            direction="long",
            executed=False,
            block_reason="strategy_shadow_only",
            option_symbol=option_symbol,
            limit_price=limit_price,
            entry_ask=entry_ask,
            quality_score=4,
        )
        assert {p.variant for p in book._open.values()} == {"baseline", "breakeven_25"}
        assert all(p.channel == "diagnostic" for p in book._open.values())

    def test_inverted_signal_is_separate_and_simulated(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_inverted_signal(
            now=NOW,
            strategy_id="vwap_reclaim",
            symbol="SPY",
            direction="short",
            option_symbol="SPY260720P00600000",
            limit_price=0.40,
            entry_ask=0.40,
            quality_score=4,
        )
        assert sb.open_count() == 1
        event = _events(tmp_path)[0]
        assert event["variant"] == "inverted"
        assert event["block_reason"] == "inverted_direction_counterfactual"

    def test_blocked_capacity_signal_opens_shadow_position(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.35,
        )
        assert sb.open_count() == 1
        evs = _events(tmp_path)
        assert len(evs) == 1
        assert evs[0]["event"] == "signal"
        assert evs[0]["executed"] is False
        assert evs[0]["block_reason"] == "orb_slot_reserved"

    def test_executed_signal_logged_but_not_simulated(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW, strategy_id="orb", symbol="QQQ", direction="SHORT",
            executed=True, option_symbol="QQQ260720P00690000",
            limit_price=0.30, journal_id=72,
        )
        assert sb.open_count() == 0
        evs = _events(tmp_path)
        assert evs[0]["executed"] is True
        assert evs[0]["block_reason"] is None
        assert evs[0]["journal_id"] == 72

    def test_non_capacity_block_logged_but_not_simulated(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW, strategy_id="orb", symbol="TSLA", direction="LONG",
            executed=False, block_reason="risk:eod_entry_cutoff",
            option_symbol="TSLA260720C00370000", limit_price=0.50,
        )
        assert sb.open_count() == 0
        assert len(_events(tmp_path)) == 1

    def test_unpriceable_capacity_block_logged_without_simulation(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="cooldown_after_loss",
            option_symbol=None, limit_price=None,
        )
        assert sb.open_count() == 0
        assert len(_events(tmp_path)) == 1


class TestSimulation:

    @pytest.mark.asyncio
    async def test_exit_variants_compare_breakeven_and_partial_profit(self, tmp_path):
        settings = _settings()
        settings.paper_scaled_exit_variant_shadow_enabled = True
        settings.paper_scaled_sizing_enabled = True
        settings.paper_scaled_guardrails_enabled = False
        settings.paper_scaled_premium_budget_dollars = 250
        settings.universe.max_contracts_per_position = 10
        sb = ShadowBook(
            settings,
            events_path=tmp_path / "shadow_book.jsonl",
            state_path=tmp_path / "shadow_state.json",
        )
        kwargs = dict(
            now=NOW,
            strategy_id="vwap_reclaim",
            symbol="SPY",
            direction="LONG",
            option_symbol="SPY260720C00600000",
            limit_price=0.40,
            entry_ask=0.40,
            quality_score=4,
        )
        _record(sb,
            executed=False,
            block_reason="strategy_shadow_only",
            **kwargs,
        )
        sb.record_exit_variants(**kwargs, contract_metadata={"bid": 0.38, "quote_timestamp": NOW.isoformat(), "quote_feed": "opra", "liquidity_passed": True})
        assert sb.open_count() == 3

        await sb.update(_quote_broker(bid=0.50, ask=0.54, at=NOW + timedelta(minutes=5)), NOW + timedelta(minutes=5))
        await sb.update(_quote_broker(bid=0.40, ask=0.44, at=NOW + timedelta(minutes=10)), NOW + timedelta(minutes=10))

        closes = {
            event["variant"]: event
            for event in _events(tmp_path)
            if event["event"] == "shadow_close"
        }
        assert closes["breakeven_25"]["shadow_pnl"] == 0.0
        assert closes["breakeven_25"]["exit_reason"] == "breakeven_stop"
        assert closes["partial_25_breakeven"]["shadow_pnl"] == 5.0
        assert closes["partial_25_breakeven"]["partial_taken"] is True
        assert "baseline" not in closes

    @pytest.mark.asyncio
    async def test_trailing_stop_closes_shadow_position(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
        )
        # Rises to 0.60 (peak), then falls to 0.44 < 0.60*0.75 → trailing stop
        await sb.update(_quote_broker(bid=0.60, ask=0.64, at=NOW + timedelta(minutes=5)), NOW + timedelta(minutes=5))
        assert sb.open_count() == 1
        closed = await sb.update(_quote_broker(bid=0.44, ask=0.48, at=NOW + timedelta(minutes=10)), NOW + timedelta(minutes=10))
        assert closed == 1
        assert sb.open_count() == 0

        close = [e for e in _events(tmp_path) if e["event"] == "shadow_close"][0]
        assert close["exit_reason"] == "trailing_stop"
        assert close["shadow_pnl"] == pytest.approx((0.44 - 0.40) * 100)
        assert close["block_reason"] == "orb_slot_reserved"
        assert close["mfe"] == pytest.approx(20.0)
        assert close["mae"] == pytest.approx(0.0)
        assert close["mfe_pct"] == pytest.approx(0.50)
        assert close["mae_pct"] == pytest.approx(0.0)
        assert close["profit_retention_ratio"] == pytest.approx(0.20)
        assert close["mfe_giveback"] == pytest.approx(16.0)
        assert close["entry_time"] == NOW.isoformat()

    @pytest.mark.asyncio
    async def test_take_profit_and_stop_loss(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW, strategy_id="orb", symbol="AAA", direction="LONG",
            executed=False, block_reason="max_trades_per_day",
            option_symbol="AAA260720C00010000", limit_price=0.40,
        )
        _record(sb,
            now=NOW, strategy_id="vwap_reclaim", symbol="BBB", direction="LONG",
            executed=False, block_reason="max_trades_per_day",
            option_symbol="BBB260720C00010000", limit_price=0.40,
        )

        async def _status(option_symbol):
            q = MagicMock()
            q.timestamp = NOW + timedelta(minutes=5)
            q.feed = "opra"
            if option_symbol.startswith("AAA"):
                q.bid, q.ask = 0.81, 0.85     # +102% → take_profit
            else:
                q.bid, q.ask = 0.19, 0.23     # -52% → stop_loss
            return q

        broker = MagicMock()
        broker.get_option_quote = AsyncMock(side_effect=_status)
        closed = await sb.update(broker, NOW + timedelta(minutes=5))
        assert closed == 2

        reasons = {e["symbol"]: e["exit_reason"]
                   for e in _events(tmp_path) if e["event"] == "shadow_close"}
        assert reasons == {"AAA": "take_profit", "BBB": "stop_loss"}

    @pytest.mark.asyncio
    async def test_session_end_force_close(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
        )
        await sb.update(_quote_broker(bid=0.42, ask=0.46, at=NOW + timedelta(minutes=5)), NOW + timedelta(minutes=5))
        n = sb.close_all(NOW + timedelta(minutes=5, seconds=30))
        assert n == 1
        close = [e for e in _events(tmp_path) if e["event"] == "shadow_close"][0]
        assert close["exit_reason"] == "session_end"
        assert close["exit_price"] == pytest.approx(0.42)


class TestEpisodes:

    def test_repeated_signal_is_one_opportunity(self, tmp_path):
        """A signal persisting across polling cycles must not open multiple
        shadow positions or inflate the opportunity count."""
        sb = _book(tmp_path)
        for i in range(4):
            _record(sb,
                now=NOW + timedelta(minutes=5 * i),
                strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
                executed=False, block_reason="orb_slot_reserved",
                option_symbol="XLF260720C00042000", limit_price=0.35,
            )
        assert sb.open_count() == 1
        evs = _events(tmp_path)
        sig_evs = [e for e in evs if e["event"] == "signal"]
        assert len(sig_evs) == 4                                  # raw observations
        assert len({e["opportunity_id"] for e in sig_evs}) == 1   # one opportunity
        assert [e["new_opportunity"] for e in sig_evs] == [True, False, False, False]

    def test_episode_expires_after_window(self, tmp_path):
        """The same key re-firing after the episode window is a new opportunity."""
        sb = ShadowBook(
            _settings(),
            events_path=tmp_path / "shadow_book.jsonl",
            state_path=tmp_path / "shadow_state.json",
            episode_window_minutes=30,
        )
        _record(sb,
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="cooldown_after_loss",
            option_symbol=None, limit_price=None,
        )
        _record(sb,
            now=NOW + timedelta(minutes=45),
            strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="cooldown_after_loss",
            option_symbol=None, limit_price=None,
        )
        sig_evs = [e for e in _events(tmp_path) if e["event"] == "signal"]
        assert len({e["opportunity_id"] for e in sig_evs}) == 2

    def test_different_direction_is_separate_opportunity(self, tmp_path):
        sb = _book(tmp_path)
        for direction in ("LONG", "SHORT"):
            _record(sb,
                now=NOW, strategy_id="orb", symbol="TSLA", direction=direction,
                executed=False, block_reason="max_trades_per_day",
                option_symbol=f"TSLA260720{'C' if direction=='LONG' else 'P'}00370000",
                limit_price=0.50,
            )
        sig_evs = [e for e in _events(tmp_path) if e["event"] == "signal"]
        assert len({e["opportunity_id"] for e in sig_evs}) == 2
        assert sb.open_count() == 2


class TestFillValidation:

    @pytest.mark.asyncio
    async def test_ask_touching_limit_validates_fill(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
            entry_ask=0.44,   # not marketable at entry
        )
        # Ask comes down to the limit within the fill window → validated
        await sb.update(_quote_broker(bid=0.36, ask=0.40, at=NOW + timedelta(minutes=5)), NOW + timedelta(minutes=5))
        # Then trail out
        await sb.update(_quote_broker(bid=0.60, ask=0.64, at=NOW + timedelta(minutes=10)), NOW + timedelta(minutes=10))
        await sb.update(_quote_broker(bid=0.44, ask=0.48, at=NOW + timedelta(minutes=15)), NOW + timedelta(minutes=15))

        close = [e for e in _events(tmp_path) if e["event"] == "shadow_close"][0]
        assert close["fill_validated"] is True
        assert close["category"] == "fill_validated"

    @pytest.mark.asyncio
    async def test_marketable_at_entry_validates_immediately(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW, strategy_id="orb", symbol="QQQ", direction="SHORT",
            executed=False, block_reason="max_trades_per_day",
            option_symbol="QQQ260720P00690000", limit_price=0.40,
            entry_ask=0.39,   # ask already at/below limit
        )
        sp = list(sb._open.values())[0]
        assert sp.fill_validated is True

    @pytest.mark.asyncio
    async def test_ask_never_reaching_limit_expires_without_pnl(self, tmp_path):
        sb = ShadowBook(
            _settings(),
            events_path=tmp_path / "shadow_book.jsonl",
            state_path=tmp_path / "shadow_state.json",
            fill_window_minutes=10,
        )
        _record(sb,
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
            entry_ask=0.46,
        )
        # Ask stays above limit through the window; later touches after the
        # deadline — must NOT validate
        await sb.update(_quote_broker(bid=0.41, ask=0.45, at=NOW + timedelta(minutes=5)), NOW + timedelta(minutes=5))
        await sb.update(_quote_broker(bid=0.38, ask=0.40, at=NOW + timedelta(minutes=20)), NOW + timedelta(minutes=20))
        # Trail out
        await sb.update(_quote_broker(bid=0.29, ask=0.33, at=NOW + timedelta(minutes=25)), NOW + timedelta(minutes=25))

        expiry = [e for e in _events(tmp_path) if e["event"] == "shadow_unfilled"][0]
        assert expiry["fill_validated"] is False
        assert expiry["shadow_pnl"] is None
        assert sb.open_count() == 0


class TestPersistence:

    def test_state_survives_restart_same_day(self, tmp_path):
        sb = _book(tmp_path)
        now = datetime.now(tz=ET)
        _record(sb,
            now=now, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
        )
        sb2 = _book(tmp_path)
        assert sb2.open_count() == 1

    def test_stale_prior_day_positions_not_restored(self, tmp_path):
        sb = _book(tmp_path)
        _record(sb,
            now=NOW - timedelta(days=3), strategy_id="orb", symbol="OLD",
            direction="LONG", executed=False, block_reason="max_trades_per_day",
            option_symbol="OLD260717C00010000", limit_price=0.40,
        )
        sb2 = _book(tmp_path)
        assert sb2.open_count() == 0
