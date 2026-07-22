from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evaluation.market_data_observer import (
    TradierMarketDataObserver,
    build_comparison,
)
from app.evaluation.market_data_review import (
    read_market_data_observations,
    summarize_market_data,
)


def test_build_comparison_detects_tradier_liquidity_disagreement():
    result = build_comparison(
        alpaca={"bid": 0.48, "ask": 0.52},
        tradier={
            "bid": 0.40,
            "ask": 0.60,
            "volume": 10,
            "open_interest": 80,
            "bid_date": 1784742855000,
            "ask_date": 1784742855000,
            "greeks": {"delta": 0.40},
        },
        thresholds={
            "min_open_interest": 100,
            "min_volume": 50,
            "max_spread_pct": 0.10,
            "delta_target_min": 0.35,
            "delta_target_max": 0.45,
            "max_contract_cost": 1000,
        },
        observed_at=datetime.fromtimestamp(1784742860, tz=timezone.utc),
    )

    assert result["alpaca_mid"] == pytest.approx(0.50)
    assert result["tradier_mid"] == pytest.approx(0.50)
    assert result["tradier_liquidity_passed"] is False
    assert result["liquidity_disagreement"] is True
    assert any(reason.startswith("OI ") for reason in result["tradier_rejection_reasons"])
    assert any(reason.startswith("volume ") for reason in result["tradier_rejection_reasons"])
    assert any(reason.startswith("spread ") for reason in result["tradier_rejection_reasons"])


@pytest.mark.asyncio
async def test_observer_writes_read_only_comparison(tmp_path: Path):
    output = tmp_path / "evaluation" / "market_data_comparisons.jsonl"
    observer = TradierMarketDataObserver(
        token="test-token",
        mode="observe",
        output_file=str(output),
        throttle_seconds=60,
        alpaca_feed_label="indicative",
    )

    async def fake_fetch(option_symbol: str):
        assert option_symbol == "QQQ260724C00710000"
        return ({
            "symbol": option_symbol,
            "bid": 0.49,
            "ask": 0.51,
            "volume": 400,
            "open_interest": 1000,
            "bid_date": 1784742855000,
            "ask_date": 1784742855000,
            "greeks": {"delta": 0.41, "smv_vol": 0.22},
        }, {"available": "119"})

    observer._fetch_quote = fake_fetch  # type: ignore[method-assign]
    contract = SimpleNamespace(
        option_symbol="QQQ260724C00710000",
        bid=Decimal("0.48"),
        ask=Decimal("0.52"),
        volume=300,
        open_interest=900,
        implied_volatility=0.20,
        delta=0.40,
    )
    thresholds = {
        "min_open_interest": 100,
        "min_volume": 50,
        "max_spread_pct": 0.10,
        "delta_target_min": 0.35,
        "delta_target_max": 0.45,
        "max_contract_cost": 1000,
    }

    scheduled = observer.submit_selected_contract(
        contract=contract,
        underlying_symbol="QQQ",
        strategy_id="vwap_reclaim",
        direction="long",
        thresholds=thresholds,
    )
    assert scheduled is True
    assert observer.submit_selected_contract(
        contract=contract,
        underlying_symbol="QQQ",
        strategy_id="vwap_reclaim",
        direction="long",
        thresholds=thresholds,
    ) is False

    tasks = list(observer._tasks)
    if tasks:
        await asyncio.gather(*tasks)

    row = json.loads(output.read_text().strip())
    assert row["status"] == "ok"
    assert row["affects_trading"] is False
    assert row["alpaca"]["feed"] == "indicative"
    assert row["comparison"]["tradier_liquidity_passed"] is True


def test_disabled_observer_is_inert():
    observer = TradierMarketDataObserver(token=None, mode="observe")
    contract = SimpleNamespace(option_symbol="SPY260724C00640000")
    assert observer.submit_selected_contract(
        contract=contract,
        underlying_symbol="SPY",
        strategy_id="orb",
        direction="long",
        thresholds={},
    ) is False


def test_market_data_review_summary(tmp_path: Path):
    path = tmp_path / "evaluation" / "market_data_comparisons.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "event": "selected_contract_comparison",
            "session_date": "2026-07-23",
            "ts": "2026-07-23T14:00:00+00:00",
            "status": "ok",
            "option_symbol": "QQQ260724C00710000",
            "strategy_id": "orb",
            "comparison": {
                "mid_diff": 0.02,
                "mid_diff_pct": 0.04,
                "tradier_quote_age_secs": 1.2,
                "liquidity_disagreement": True,
                "tradier_liquidity_passed": False,
            },
        },
        {
            "event": "selected_contract_comparison",
            "session_date": "2026-07-23",
            "ts": "2026-07-23T14:01:00+00:00",
            "status": "error",
            "option_symbol": "SPY260724P00630000",
            "strategy_id": "vwap_reclaim",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    loaded = read_market_data_observations("2026-07-23", tmp_path)
    summary = summarize_market_data(loaded)
    assert summary["observations"] == 2
    assert summary["successful"] == 1
    assert summary["errors"] == 1
    assert summary["liquidity_disagreements"] == 1
    assert summary["avg_abs_mid_diff"] == pytest.approx(0.02)
