"""
Options Trading Research System — main entry point.

Usage
─────
  # Run backtests on all strategies
  python main.py backtest --symbol SPY --start 2023-01-01 --end 2024-12-31

  # Start dashboard only (no trading)
  python main.py dashboard

  # List available strategies
  python main.py strategies

  # Run unattended trading session
  python scripts/session_runner.py

SAFETY NOTE
───────────
Live trading requires LIVE_TRADING_ENABLED=true in the environment.
This is intentionally a non-default, explicit opt-in.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import click
import uvicorn

# Ensure app package is importable when running from project root
sys.path.insert(0, str(Path(__file__).parent))

from app.config import get_settings
from app.utils.logging_setup import setup_logging


@click.group()
def cli():
    """Options Trading Research System."""
    setup_logging()


# ── Backtest ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--symbol", "-s", multiple=True, default=["SPY", "QQQ"], help="Symbols to backtest")
@click.option("--strategy", "-t", multiple=True, default=[], help="Strategy IDs (default: all)")
@click.option("--start", default=None, help="Start date YYYY-MM-DD")
@click.option("--end", default=None, help="End date YYYY-MM-DD")
@click.option("--interval", default="1d", help="Bar interval (1d, 1h, 5m, etc.)")
@click.option("--equity", default=100_000.0, help="Starting equity")
@click.option("--output-dir", default=None, help="Output directory for reports")
def backtest(symbol, strategy, start, end, interval, equity, output_dir):
    """Run backtests and save reports."""
    asyncio.run(_run_backtest(list(symbol), list(strategy), start, end, interval, equity, output_dir))


async def _run_backtest(symbols, strategy_ids, start, end, interval, equity, output_dir):
    from app.backtesting import BacktestEngine
    from app.strategies import (
        MACompressionStrategy,
        OpeningRangeBreakoutStrategy,
        RSITrendStrategy,
        VWAPReclaimStrategy,
    )

    all_strategies = {
        "orb": OpeningRangeBreakoutStrategy,
        "vwap_reclaim": VWAPReclaimStrategy,
        "rsi_trend": RSITrendStrategy,
        "ma_compression": MACompressionStrategy,
    }

    if strategy_ids:
        selected = {k: v for k, v in all_strategies.items() if k in strategy_ids}
        if not selected:
            click.echo(f"Unknown strategies: {strategy_ids}. Available: {list(all_strategies)}")
            return
    else:
        selected = all_strategies

    engine = BacktestEngine()
    results = []

    for strat_id, strat_cls in selected.items():
        strat = strat_cls()
        for sym in symbols:
            click.echo(f"Running backtest: {strat_id} / {sym} ...")
            result = await engine.run(strat, sym, start, end, interval, equity)
            results.append(result)
            engine.save_report(result, output_dir)

    click.echo(f"\nCompleted {len(results)} backtests.")


# ── Dashboard only ────────────────────────────────────────────────────────────

@cli.command()
def dashboard():
    """Start the FastAPI dashboard without the trading loop."""
    settings = get_settings()

    async def _start():
        from app.api.models import init_db
        await init_db()
        from app.api import create_app
        app = create_app()
        config = uvicorn.Config(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
        )
        server = uvicorn.Server(config)
        await server.serve()

    click.echo(
        f"Dashboard at http://{settings.api_host}:{settings.api_port} (no trading)"
    )
    asyncio.run(_start())


# ── Strategy list ─────────────────────────────────────────────────────────────

@cli.command()
def strategies():
    """List all available strategies."""
    from tabulate import tabulate

    rows = [
        ["orb", "Opening Range Breakout", "SPY, QQQ"],
        ["vwap_reclaim", "VWAP Reclaim / Rejection", "SPY, QQQ, AAPL, TSLA"],
        ["rsi_trend", "RSI + Trend Filter", "SPY, QQQ, NVDA, AMZN"],
        ["ma_compression", "MA Compression Breakout", "SPY, QQQ"],
        ["iv_crush_filter", "IV Crush Avoidance (filter)", "All"],
        ["liquidity_filter", "Options Liquidity Filter", "All"],
    ]
    click.echo(tabulate(rows, headers=["ID", "Name", "Default Symbols"], tablefmt="rounded_grid"))


if __name__ == "__main__":
    cli()
