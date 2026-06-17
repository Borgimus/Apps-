"""
Options Trading Research System — main entry point.

Usage
─────
  # Run backtests on all strategies
  python main.py backtest --symbol SPY --start 2023-01-01 --end 2024-12-31

  # Start paper trading + dashboard
  python main.py trade

  # Start dashboard only (no trading)
  python main.py dashboard

  # List available strategies
  python main.py strategies

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


# ── Paper/Live Trading ────────────────────────────────────────────────────────

@cli.command()
def trade():
    """Start paper trading loop + dashboard API."""
    settings = get_settings()

    if settings.live_trading_enabled:
        click.echo(
            "\n⚠️  WARNING: LIVE_TRADING_ENABLED=true\n"
            "   Real money will be placed at risk.\n"
            "   Press Ctrl+C within 5 seconds to abort.\n"
        )
        import time
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            click.echo("Aborted.")
            return

    click.echo(
        f"Starting {'LIVE' if settings.live_trading_enabled else 'PAPER'} trading "
        f"| broker={settings.broker} | dashboard=http://{settings.api_host}:{settings.api_port}"
    )
    from paper_trader import main as trader_main
    asyncio.run(trader_main())


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


# ── ICT Strategy commands ─────────────────────────────────────────────────────

@cli.group()
def ict():
    """ICT Liquidity Sweep & FVG Reversal strategy commands."""


@ict.command("signals")
@click.option("--symbol", "-s", multiple=True, default=["SPY"], help="Symbols to analyse")
@click.option("--params", "-p", default="{}", help="JSON params for ICTConfig overrides")
def ict_signals(symbol, params):
    """Generate ICT signals from the latest 1-minute bar data."""
    import json as _json
    import warnings

    from app.strategies.ict import ICTStrategy

    try:
        param_dict = _json.loads(params)
    except _json.JSONDecodeError:
        click.echo(f"Invalid JSON params: {params}")
        return

    strategy = ICTStrategy(params=param_dict)

    for sym in symbol:
        click.echo(f"\nScanning {sym} for ICT signals...")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from app.data.data_fetcher import fetch_1min_bars
                bars = fetch_1min_bars(sym, warn=False)

            if bars.empty:
                click.echo(f"  No bar data available for {sym}")
                continue

            sigs = strategy.generate_signals(bars, sym)
            if not sigs:
                click.echo(f"  No signals found for {sym}")
            else:
                for sig in sigs:
                    click.echo(
                        f"  [{sig.direction.value.upper()}] {sym} @ {sig.entry_price:.4f} "
                        f"| SL={sig.stop_loss:.4f} TP={sig.take_profit:.4f} "
                        f"| Conf={sig.confidence:.2f} | {sig.notes[:80]}"
                    )
        except Exception as exc:
            click.echo(f"  Error scanning {sym}: {exc}")


@ict.command("backtest")
@click.option("--symbol", "-s", default="SPY", help="Symbol to backtest")
@click.option("--start", default=None, help="Start date YYYY-MM-DD")
@click.option("--end", default=None, help="End date YYYY-MM-DD")
@click.option("--equity", default=100_000.0, help="Starting equity")
@click.option("--params", "-p", default="{}", help="JSON ICTConfig overrides")
@click.option("--exit-mode", default="fixed_rr",
              type=click.Choice(["fixed_rr", "liquidity_target", "major_structure", "hybrid"]),
              help="Exit mode")
def ict_backtest(symbol, start, end, equity, params, exit_mode):
    """Run an ICT strategy backtest on 1-minute data."""
    import json as _json
    import warnings

    from app.backtesting.ict_backtester import ICTBacktester

    try:
        param_dict = _json.loads(params)
    except _json.JSONDecodeError:
        click.echo(f"Invalid JSON params: {params}")
        return

    param_dict.setdefault("exit_mode", exit_mode)
    param_dict.setdefault("account_size", equity)

    click.echo(f"Fetching 1-minute bars for {symbol}...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from app.data.data_fetcher import fetch_1min_bars
        bars = fetch_1min_bars(symbol, start=start, end=end, warn=False)

    if bars.empty:
        click.echo("No bar data returned — cannot run backtest.")
        return

    click.echo(f"Running ICT backtest: {symbol} | {len(bars)} bars | equity=${equity:,.0f}")
    bt = ICTBacktester(params=param_dict, starting_equity=equity)
    result = bt.run(bars, symbol, start, end)
    click.echo(result.summary())


@ict.command("scan")
@click.option("--symbol", "-s", multiple=True, default=["SPY", "QQQ"],
              help="Symbols to scan")
@click.option("--params", "-p", default="{}", help="JSON ICTConfig overrides")
def ict_scan(symbol, params):
    """Run the ICT multi-symbol scanner."""
    import asyncio
    import json as _json
    import warnings

    from app.scanner import ICTScanner

    try:
        param_dict = _json.loads(params)
    except _json.JSONDecodeError:
        click.echo(f"Invalid JSON params: {params}")
        return

    def _fetcher(sym: str):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from app.data.data_fetcher import fetch_1min_bars
            return fetch_1min_bars(sym, warn=False)

    scanner = ICTScanner(list(symbol), _fetcher, param_dict)
    results = asyncio.run(scanner.scan_all())

    click.echo(f"\nICT Scanner Results ({len(results)} symbols scanned):")
    for r in results:
        if r.error:
            click.echo(f"  {r.symbol}: ERROR — {r.error}")
        elif r.signal:
            sig = r.signal
            click.echo(
                f"  {r.symbol}: SIGNAL [{sig.direction.value.upper()}] "
                f"@ {sig.entry_price:.4f} conf={r.confidence:.2f}"
            )
        else:
            click.echo(f"  {r.symbol}: no signal (conf={r.confidence:.2f})")


@ict.command("config")
def ict_config():
    """Print the default ICT strategy configuration."""
    from app.strategies.ict.config import ICTConfig

    cfg = ICTConfig()
    import json as _json
    click.echo(_json.dumps(cfg.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    cli()
