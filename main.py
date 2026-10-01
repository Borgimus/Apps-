"""
Options Trading Research System — main entry point.

Usage
─────
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
        ["iv_crush_filter", "IV Crush Avoidance (filter)", "All"],
        ["liquidity_filter", "Options Liquidity Filter", "All"],
    ]
    click.echo(tabulate(rows, headers=["ID", "Name", "Default Symbols"], tablefmt="rounded_grid"))


if __name__ == "__main__":
    cli()
