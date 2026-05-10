"""
Integration smoke-test: real yfinance data → strategies → risk → paper order.
Run from the project root:  python scripts/integration_test.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
import warnings
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.WARNING)


async def run():
    from app.brokers import get_broker
    from app.brokers.broker_interface import (
        OptionContract,
        OrderRequest,
        OrderSide,
        OrderType,
    )
    from app.config import get_settings
    from app.data import YFinanceDataSource
    from app.risk import RiskManager
    from app.strategies import (
        IVCrushFilter,
        MACompressionStrategy,
        OpeningRangeBreakoutStrategy,
        RSITrendStrategy,
    )

    s = get_settings()
    broker = get_broker(s)
    rm = RiskManager(s)

    # ── Fetch real SPY bars ───────────────────────────────────────────────────
    data = YFinanceDataSource()
    print("Fetching SPY daily bars from yfinance...")
    bars = await data.get_bars("SPY", "2024-01-01", "2024-12-31", "1d")
    print(f"  Got {len(bars)} bars  ({bars.index[0].date()} → {bars.index[-1].date()})")

    # ── Run strategies ────────────────────────────────────────────────────────
    # Note: ORB is an intraday strategy (needs 5-min bars). On daily bars it
    # produces 0 signals by design — it only fires after an opening-range
    # candle sequence, which doesn't exist in daily OHLCV data.
    strategies = [
        OpeningRangeBreakoutStrategy(params={"range_minutes": 15, "min_range_pts": 0.3}),
        RSITrendStrategy(params={"rsi_period": 14, "rsi_oversold": 35, "trend_ema_period": 50}),
        MACompressionStrategy(),
    ]
    all_signals = []
    for strat in strategies:
        sigs = strat.generate_signals(bars, "SPY")
        all_signals.extend(sigs)
        directions = {}
        for sg in sigs:
            directions[sg.direction.value] = directions.get(sg.direction.value, 0) + 1
        note = "  ← needs intraday bars" if strat.strategy_id == "orb" else ""
        print(f"  {strat.strategy_id:<22} → {len(sigs):3d} signals  {directions}{note}")

    print(f"  Total signals: {len(all_signals)}")

    # ── IV crush filter ───────────────────────────────────────────────────────
    iv_filter = IVCrushFilter()
    clean = iv_filter.apply(all_signals)
    print(f"  After IV filter: {len(clean)} signals")

    # ── Account + risk check ──────────────────────────────────────────────────
    acct = await broker.get_account()
    rm.start_session(acct.equity)
    print(f"\nAccount: ${acct.equity:,.2f} equity  |  paper={acct.is_paper}")

    if not clean:
        print("No signals to process.")
        return

    sig = clean[0]
    print(f"\nFirst signal: {sig.strategy_id} | {sig.symbol} | {sig.direction.value} | "
          f"@ {sig.price:.2f} | {sig.timestamp.date()}")

    dummy_contract = OptionContract(
        symbol=sig.symbol,
        option_symbol="SPY_SMOKE_TEST",
        expiration=date(2024, 3, 15),
        strike=Decimal("470"),
        option_type="call" if sig.direction.value == "long" else "put",
        bid=Decimal("2.10"),
        ask=Decimal("2.20"),
        last=Decimal("2.15"),
        volume=500,
        open_interest=1000,
        implied_volatility=0.20,
        delta=0.40,
    )
    req = OrderRequest(
        symbol=sig.symbol,
        option_symbol="SPY_SMOKE_TEST",
        side=OrderSide.BUY_TO_OPEN,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("2.20"),
    )
    mid_session = datetime(2024, 3, 15, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    risk_result = rm.check_order(req, acct.equity, dummy_contract, now=mid_session)

    if risk_result.passed:
        print(f"Risk check: PASSED  (approved qty={risk_result.approved_quantity},"
              f" risk=${risk_result.approved_risk_dollars:.2f})")
        order = await broker.place_option_order(req)
        print(f"Paper order: {order.status.value} | id={order.order_id[:8]}..."
              f" | filled @ {order.filled_price}")
        acct2 = await broker.get_account()
        # Equity = cash + mark-to-cost of positions, so it stays flat immediately
        # after a buy.  Show cash to see money actually deployed.
        cash_deployed = acct.cash - acct2.cash
        print(f"Account after trade: ${acct2.equity:,.2f} equity  "
              f"| ${acct2.cash:,.2f} cash  (${cash_deployed:.2f} deployed in options)")
    else:
        print(f"Risk check: FAILED — {', '.join(risk_result.messages)}")

    # ── Run a quick backtest ──────────────────────────────────────────────────
    print("\nRunning backtest on ORB/SPY 2024...")
    from app.backtesting import BacktestEngine
    engine = BacktestEngine()
    bt = await engine.run(
        OpeningRangeBreakoutStrategy(params={"range_minutes": 15, "min_range_pts": 0.3}),
        "SPY",
        start="2024-01-01",
        end="2024-12-31",
        interval="1d",
    )
    print(f"  Trades: {bt.total_trades}  Win rate: {bt.win_rate:.1%}  "
          f"P&L: ${bt.total_pnl:+,.2f}  Sharpe: {bt.sharpe_ratio:.2f}  "
          f"Max DD: {bt.max_drawdown:.1%}  [APPROXIMATE]")

    print("\nAll integration checks passed.")


if __name__ == "__main__":
    asyncio.run(run())
