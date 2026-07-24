"""
Deterministic bar-by-bar historical session replay.

Fetches historical 5-minute bars from yfinance, feeds them through the full
signal → risk → position-management pipeline, and prints a P&L report.
Option pricing uses Black-Scholes (no broker connection needed).

Usage examples:
  python scripts/replay.py --symbol SPY --days 30
  python scripts/replay.py --symbol SPY --start 2024-01-01 --end 2024-03-31
  python scripts/replay.py --symbol QQQ --strategy vwap_reclaim --equity 50000
  python scripts/replay.py --symbol SPY --days 5 --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))


def _header(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def _row(label: str, value):
    print(f"  {label:<28} {value}")


async def run(args: argparse.Namespace):
    from app.config import get_settings
    from app.data import YFinanceDataSource
    from app.replay import ReplayEngine
    from app.strategies import (
        MACompressionStrategy,
        OpeningRangeBreakoutStrategy,
        RSITrendStrategy,
        VWAPReclaimStrategy,
    )

    settings = get_settings()
    data = YFinanceDataSource()

    strategy_map = {
        "orb": OpeningRangeBreakoutStrategy,
        "vwap_reclaim": VWAPReclaimStrategy,
        "rsi_trend": RSITrendStrategy,
        "ma_compression": MACompressionStrategy,
    }
    if args.strategy not in strategy_map:
        print(f"Unknown strategy '{args.strategy}'. Choose from: {list(strategy_map)}")
        sys.exit(1)

    strategy = strategy_map[args.strategy]()

    # ── Fetch bars ────────────────────────────────────────────────────────────
    _header(f"Replay: {strategy.strategy_id} | {args.symbol}")

    if args.start and args.end:
        print(f"  Period  : {args.start} → {args.end}")
        bars = await data.get_bars(args.symbol, args.start, args.end, "5m")
    else:
        days = args.days
        print(f"  Period  : last {days} calendar days")
        bars = await data.get_intraday_bars(args.symbol, interval="5m", days_back=days)

    if bars.empty:
        print(f"  ERROR: no bars returned for {args.symbol}")
        sys.exit(1)

    print(f"  Bars    : {len(bars)}")
    print(f"  Equity  : ${args.equity:,.0f}")

    # ── Run replay ────────────────────────────────────────────────────────────
    engine = ReplayEngine(
        strategy=strategy,
        symbol=args.symbol,
        settings=settings,
        starting_equity=args.equity,
    )
    result = engine.replay(bars)

    # ── Print results ─────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    _row("Total trades:", result.total_trades)
    _row("Win rate:", f"{result.win_rate:.1%}")
    _row("Total P&L:", f"${result.total_pnl:,.2f}")
    _row("Avg winner:", f"${result.avg_win:,.2f}")
    _row("Avg loser:", f"${result.avg_loss:,.2f}")
    _row("Expectancy:", f"${result.expectancy:,.2f}")
    _row("Max drawdown:", f"{result.max_drawdown:.1%}")
    _row("Sharpe ratio:", f"{result.sharpe_ratio:.2f}")
    _row("Signals skipped:", len(result.skips))

    if args.verbose and result.trades:
        print(f"\n{'─' * 60}")
        print("  Trade log:")
        for t in result.trades:
            et = t.entry_time.strftime("%m-%d %H:%M")
            xt = t.exit_time.strftime("%H:%M") if t.exit_time else "?"
            pnl_str = f"+${t.pnl:.2f}" if t.pnl >= 0 else f"-${abs(t.pnl):.2f}"
            print(f"    {et}→{xt}  {t.direction:<5}  {pnl_str:>10}  [{t.exit_reason}]")

    if args.verbose and result.skips:
        print(f"\n  Skips:")
        from collections import Counter
        counts = Counter(s.reason for s in result.skips)
        for reason, count in counts.most_common():
            print(f"    {reason}: {count}")

    print()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Deterministic bar-by-bar historical replay.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--symbol", default="SPY", help="Underlying ticker (default: SPY)")
    p.add_argument(
        "--strategy",
        default="orb",
        choices=["orb", "vwap_reclaim", "rsi_trend", "ma_compression"],
        help="Strategy to replay (default: orb)",
    )
    p.add_argument("--start", help="Start date YYYY-MM-DD (use with --end)")
    p.add_argument("--end", help="End date YYYY-MM-DD")
    p.add_argument("--days", type=int, default=30, help="Calendar days back (default: 30)")
    p.add_argument("--equity", type=float, default=100_000.0, help="Starting equity (default: 100000)")
    p.add_argument("--verbose", "-v", action="store_true", help="Print trade-by-trade log")
    p.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Library log level (default: WARNING)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run(args))
