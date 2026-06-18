"""
Settings loader.  Values come from (in priority order):
  1. Environment variables
  2. .env file
  3. config.yaml
  4. Hard-coded defaults below

LIVE_TRADING_ENABLED must be set explicitly via env var; the yaml/default value
is always false so there is no accidental live-trading path.
"""

from __future__ import annotations

import os
import warnings
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_CONFIG_PATH = Path(__file__).parents[2] / "config.yaml"


def _load_yaml() -> dict:
    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open() as f:
            return yaml.safe_load(f) or {}
    return {}


_yaml = _load_yaml()


def _yaml_get(*keys, default=None):
    node = _yaml
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, default)
        if node is default:
            return default
    return node


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_", extra="ignore")

    max_risk_per_trade: float = _yaml_get("risk", "max_risk_per_trade", default=0.01)
    max_trades_per_day: int = _yaml_get("risk", "max_trades_per_day", default=3)
    max_daily_loss: float = _yaml_get("risk", "max_daily_loss", default=0.02)
    min_open_interest: int = _yaml_get("risk", "min_open_interest", default=100)
    min_volume: int = _yaml_get("risk", "min_volume", default=50)
    max_spread_pct: float = _yaml_get("risk", "max_spread_pct", default=0.10)
    earnings_blackout_days: int = _yaml_get("risk", "earnings_blackout_days", default=1)
    allow_earnings_trades: bool = _yaml_get("risk", "allow_earnings_trades", default=False)


class OptionsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPTIONS_", extra="ignore")

    preferred_dte: List[int] = _yaml_get("options", "preferred_dte", default=[0, 1, 2])
    delta_target_min: float = _yaml_get("options", "delta_target_min", default=0.35)
    delta_target_max: float = _yaml_get("options", "delta_target_max", default=0.45)
    limit_price_offset_pct: float = _yaml_get("options", "limit_price_offset_pct", default=0.02)


class BacktestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BACKTEST_", extra="ignore")

    output_dir: str = _yaml_get("backtesting", "output_dir", default="./backtest_results")
    default_start: str = _yaml_get("backtesting", "default_start", default="2023-01-01")
    default_end: str = _yaml_get("backtesting", "default_end", default="2024-12-31")
    slippage_per_contract: float = _yaml_get("backtesting", "slippage_per_contract", default=0.05)
    commission_per_contract: float = _yaml_get("backtesting", "commission_per_contract", default=0.65)
    options_spread_assumption: float = _yaml_get("backtesting", "options_spread_assumption", default=0.10)
    warn_synthetic_options: bool = _yaml_get("backtesting", "warn_synthetic_options", default=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Trading mode ─────────────────────────────────────────────────────────
    # SAFETY: this defaults to False. Overriding it via env var requires an
    # intentional act; it cannot be enabled by editing config.yaml alone.
    live_trading_enabled: bool = False

    broker: str = _yaml_get("trading", "broker", default="alpaca")

    watchlist: List[str] = _yaml_get(
        "trading", "watchlist", default=["SPY", "QQQ"]
    )
    market_open: str = _yaml_get("trading", "market_open", default="09:30")
    market_close: str = _yaml_get("trading", "market_close", default="16:00")
    no_trade_open_buffer_minutes: int = _yaml_get(
        "trading", "no_trade_open_buffer_minutes", default=15
    )
    no_trade_close_buffer_minutes: int = _yaml_get(
        "trading", "no_trade_close_buffer_minutes", default=15
    )

    # ── Broker credentials ────────────────────────────────────────────────────
    alpaca_api_key: Optional[str] = None
    alpaca_secret_key: Optional[str] = None
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"
    alpaca_data_feed: str = "iex"  # "iex" free tier; "sip" requires paid subscription

    tradier_access_token: Optional[str] = None
    tradier_base_url: str = "https://sandbox.tradier.com/v1"

    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1

    # ── Infrastructure ────────────────────────────────────────────────────────
    database_url: str = _yaml_get("database", "url", default="sqlite+aiosqlite:///./trading.db")
    kill_switch_file: str = "./KILL_SWITCH"

    log_level: str = _yaml_get("logging", "level", default="INFO")
    log_file: str = _yaml_get("logging", "file", default="./logs/trading.log")

    api_host: str = _yaml_get("api", "host", default="127.0.0.1")
    api_port: int = _yaml_get("api", "port", default=8000)

    # ── Nested settings ───────────────────────────────────────────────────────
    risk: RiskSettings = RiskSettings()
    options: OptionsSettings = OptionsSettings()
    backtesting: BacktestSettings = BacktestSettings()

    @field_validator("live_trading_enabled", mode="before")
    @classmethod
    def guard_live_trading(cls, v):
        if str(v).lower() in ("1", "true", "yes"):
            warnings.warn(
                "\n\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                "  WARNING: LIVE TRADING IS ENABLED.                        \n"
                "  Real money will be placed at risk.                       \n"
                "  Ensure you have fully tested your strategies in paper    \n"
                "  trading mode before proceeding.                          \n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n",
                stacklevel=4,
            )
            return True
        return False

    def is_kill_switch_active(self) -> bool:
        return Path(self.kill_switch_file).exists()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
