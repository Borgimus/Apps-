"""
Shadow book tests: capacity-blocked signals are logged and simulated with the
same exit rules the live PositionManager uses; executed signals are logged but
never simulated; results are persisted separately from broker records.
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from app.evaluation.shadow_book import ShadowBook, CAPACITY_REASONS

ET = ZoneInfo("America/New_York")


def _settings():
    s = MagicMock()
    s.position.stop_loss_pct = 0.50
    s.position.take_profit_pct = 1.00
    s.position.trailing_stop_pct = 0.25
    s.position.max_hold_minutes = 120
    s.position.eod_exit_time = "15:45"
    return s


def _book(tmp_path) -> ShadowBook:
    return ShadowBook(
        _settings(),
        events_path=tmp_path / "shadow_book.jsonl",
        state_path=tmp_path / "shadow_state.json",
    )


def _quote_broker(bid: float, ask: float):
    quote = MagicMock()
    quote.bid = bid
    quote.ask = ask
    broker = MagicMock()
    broker.get_option_quote = AsyncMock(return_value=quote)
    return broker


def _events(tmp_path):
    p = Path(tmp_path / "shadow_book.jsonl")
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


NOW = datetime(2026, 7, 20, 10, 30, tzinfo=ET)


class TestRecording:

    def test_blocked_capacity_signal_opens_shadow_position(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_signal(
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
        sb.record_signal(
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
        sb.record_signal(
            now=NOW, strategy_id="orb", symbol="TSLA", direction="LONG",
            executed=False, block_reason="risk:eod_entry_cutoff",
            option_symbol="TSLA260720C00370000", limit_price=0.50,
        )
        assert sb.open_count() == 0
        assert len(_events(tmp_path)) == 1

    def test_unpriceable_capacity_block_logged_without_simulation(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_signal(
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="cooldown_after_loss",
            option_symbol=None, limit_price=None,
        )
        assert sb.open_count() == 0
        assert len(_events(tmp_path)) == 1


class TestSimulation:

    @pytest.mark.asyncio
    async def test_trailing_stop_closes_shadow_position(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_signal(
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
        )
        # Rises to 0.60 (peak), then falls to 0.44 < 0.60*0.75 → trailing stop
        await sb.update(_quote_broker(bid=0.60, ask=0.64), NOW + timedelta(minutes=5))
        assert sb.open_count() == 1
        closed = await sb.update(_quote_broker(bid=0.44, ask=0.48), NOW + timedelta(minutes=10))
        assert closed == 1
        assert sb.open_count() == 0

        close = [e for e in _events(tmp_path) if e["event"] == "shadow_close"][0]
        assert close["exit_reason"] == "trailing_stop"
        assert close["shadow_pnl"] == pytest.approx((0.44 - 0.40) * 100)
        assert close["block_reason"] == "orb_slot_reserved"

    @pytest.mark.asyncio
    async def test_take_profit_and_stop_loss(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_signal(
            now=NOW, strategy_id="orb", symbol="AAA", direction="LONG",
            executed=False, block_reason="max_trades_per_day",
            option_symbol="AAA260720C00010000", limit_price=0.40,
        )
        sb.record_signal(
            now=NOW, strategy_id="vwap_reclaim", symbol="BBB", direction="LONG",
            executed=False, block_reason="max_trades_per_day",
            option_symbol="BBB260720C00010000", limit_price=0.40,
        )

        async def _status(option_symbol):
            q = MagicMock()
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
        sb.record_signal(
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
        )
        await sb.update(_quote_broker(bid=0.42, ask=0.46), NOW + timedelta(minutes=5))
        n = sb.close_all(NOW + timedelta(minutes=30))
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
            sb.record_signal(
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
        sb.record_signal(
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="cooldown_after_loss",
            option_symbol=None, limit_price=None,
        )
        sb.record_signal(
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
            sb.record_signal(
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
        sb.record_signal(
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
            entry_ask=0.44,   # not marketable at entry
        )
        # Ask comes down to the limit within the fill window → validated
        await sb.update(_quote_broker(bid=0.36, ask=0.40), NOW + timedelta(minutes=5))
        # Then trail out
        await sb.update(_quote_broker(bid=0.60, ask=0.64), NOW + timedelta(minutes=10))
        await sb.update(_quote_broker(bid=0.44, ask=0.48), NOW + timedelta(minutes=15))

        close = [e for e in _events(tmp_path) if e["event"] == "shadow_close"][0]
        assert close["fill_validated"] is True
        assert close["category"] == "fill_validated"

    @pytest.mark.asyncio
    async def test_marketable_at_entry_validates_immediately(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_signal(
            now=NOW, strategy_id="orb", symbol="QQQ", direction="SHORT",
            executed=False, block_reason="max_trades_per_day",
            option_symbol="QQQ260720P00690000", limit_price=0.40,
            entry_ask=0.39,   # ask already at/below limit
        )
        sp = list(sb._open.values())[0]
        assert sp.fill_validated is True

    @pytest.mark.asyncio
    async def test_ask_never_reaching_limit_stays_theoretical(self, tmp_path):
        sb = ShadowBook(
            _settings(),
            events_path=tmp_path / "shadow_book.jsonl",
            state_path=tmp_path / "shadow_state.json",
            fill_window_minutes=10,
        )
        sb.record_signal(
            now=NOW, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
            entry_ask=0.46,
        )
        # Ask stays above limit through the window; later touches after the
        # deadline — must NOT validate
        await sb.update(_quote_broker(bid=0.41, ask=0.45), NOW + timedelta(minutes=5))
        await sb.update(_quote_broker(bid=0.38, ask=0.40), NOW + timedelta(minutes=20))
        # Trail out
        await sb.update(_quote_broker(bid=0.29, ask=0.33), NOW + timedelta(minutes=25))

        close = [e for e in _events(tmp_path) if e["event"] == "shadow_close"][0]
        assert close["fill_validated"] is False
        assert close["category"] == "theoretical"


class TestPersistence:

    def test_state_survives_restart_same_day(self, tmp_path):
        sb = _book(tmp_path)
        now = datetime.now(tz=ET)
        sb.record_signal(
            now=now, strategy_id="vwap_reclaim", symbol="XLF", direction="LONG",
            executed=False, block_reason="orb_slot_reserved",
            option_symbol="XLF260720C00042000", limit_price=0.40,
        )
        sb2 = _book(tmp_path)
        assert sb2.open_count() == 1

    def test_stale_prior_day_positions_not_restored(self, tmp_path):
        sb = _book(tmp_path)
        sb.record_signal(
            now=NOW - timedelta(days=3), strategy_id="orb", symbol="OLD",
            direction="LONG", executed=False, block_reason="max_trades_per_day",
            option_symbol="OLD260717C00010000", limit_price=0.40,
        )
        sb2 = _book(tmp_path)
        assert sb2.open_count() == 0
