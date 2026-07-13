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
from pydantic import field_validator, model_validator  # noqa: F401 (model_validator used below)
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
    model_config = SettingsConfigDict(env_prefix="RISK_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    max_risk_per_trade: float = _yaml_get("risk", "max_risk_per_trade", default=0.01)
    max_trades_per_day: int = _yaml_get("risk", "max_trades_per_day", default=3)
    max_daily_loss: float = _yaml_get("risk", "max_daily_loss", default=0.02)
    min_open_interest: int = _yaml_get("risk", "min_open_interest", default=100)
    min_volume: int = _yaml_get("risk", "min_volume", default=50)
    max_spread_pct: float = _yaml_get("risk", "max_spread_pct", default=0.10)
    earnings_blackout_days: int = _yaml_get("risk", "earnings_blackout_days", default=1)
    allow_earnings_trades: bool = _yaml_get("risk", "allow_earnings_trades", default=False)
    # Underlying-level liquidity guards (applied in CandidateScorer)
    min_underlying_price: float = _yaml_get("risk", "min_underlying_price", default=5.0)
    min_underlying_avg_volume: int = _yaml_get(
        "risk", "min_underlying_avg_volume", default=500_000
    )


class OptionsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPTIONS_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    preferred_dte: List[int] = _yaml_get("options", "preferred_dte", default=[0, 1, 2])
    delta_target_min: float = _yaml_get("options", "delta_target_min", default=0.35)
    delta_target_max: float = _yaml_get("options", "delta_target_max", default=0.45)
    limit_price_offset_pct: float = _yaml_get("options", "limit_price_offset_pct", default=0.02)

    # Pricing modes: bid | mid | ask | marketable_limit
    entry_limit_price_mode: str = _yaml_get("options", "entry_limit_price_mode", default="mid")
    exit_limit_price_mode: str = _yaml_get("options", "exit_limit_price_mode", default="mid")
    # Fraction of spread width added above ask in marketable_limit mode
    entry_marketable_offset_pct: float = _yaml_get("options", "entry_marketable_offset_pct", default=0.01)
    exit_marketable_offset_pct: float = _yaml_get("options", "exit_marketable_offset_pct", default=0.01)


class PositionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSITION_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    stop_loss_pct: float = _yaml_get("position", "stop_loss_pct", default=0.50)
    take_profit_pct: float = _yaml_get("position", "take_profit_pct", default=1.00)
    trailing_stop_pct: float = _yaml_get("position", "trailing_stop_pct", default=0.25)
    max_hold_minutes: int = _yaml_get("position", "max_hold_minutes", default=120)
    eod_exit_time: str = _yaml_get("position", "eod_exit_time", default="15:45")
    cooldown_after_loss_minutes: int = _yaml_get("position", "cooldown_after_loss_minutes", default=15)
    # Minimum minutes remaining before eod_exit_time required to allow a new entry.
    # Prevents entering positions that will be force-closed before they can develop.
    min_entry_minutes_before_eod: int = _yaml_get(
        "position", "min_entry_minutes_before_eod", default=30
    )


class UniverseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UNIVERSE_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mode: str = _yaml_get("universe", "mode", default="manual")
    file: str = _yaml_get("universe", "file", default="./config/ticker_universe.yaml")
    max_symbols_per_scan: int = _yaml_get("universe", "max_symbols_per_scan", default=10)
    max_active_symbols: int = _yaml_get("universe", "max_active_symbols", default=3)
    max_symbols_traded_per_day: int = _yaml_get("universe", "max_symbols_traded_per_day", default=1)
    max_active_positions: int = _yaml_get("universe", "max_active_positions", default=1)
    min_scan_score: float = _yaml_get("universe", "min_scan_score", default=40.0)
    scan_interval_minutes: int = _yaml_get("universe", "scan_interval_minutes", default=30)
    # When all scanner candidates are rejected, block CLI fallback trades (safe default=False).
    # If True, fallback is still gated by fallback_min_rvol.
    allow_cli_fallback_when_scanner_rejects: bool = _yaml_get(
        "universe", "allow_cli_fallback_when_scanner_rejects", default=False
    )
    fallback_min_rvol: float = _yaml_get("universe", "fallback_min_rvol", default=0.20)
    # Hard cap on contracts per individual position (1 = never size up).
    max_contracts_per_position: int = _yaml_get(
        "universe", "max_contracts_per_position", default=1
    )
    # Group-based universe settings
    groups_enabled: str = _yaml_get(
        "universe", "groups_enabled",
        default="core_etfs,mega_cap,liquid_growth",
    )
    include_experimental: bool = _yaml_get(
        "universe", "include_experimental", default=False
    )
    max_total_symbols: int = _yaml_get("universe", "max_total_symbols", default=40)
    max_per_group: int = _yaml_get("universe", "max_per_group", default=15)


class RSITrendSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RSI_TREND_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    enabled: bool = _yaml_get("strategies", "rsi_trend", "enabled", default=True)
    rsi_period: int = _yaml_get("strategies", "rsi_trend", "rsi_period", default=14)
    rsi_oversold: float = _yaml_get("strategies", "rsi_trend", "rsi_oversold", default=35.0)
    rsi_overbought: float = _yaml_get("strategies", "rsi_trend", "rsi_overbought", default=65.0)
    trend_ema_period: int = _yaml_get("strategies", "rsi_trend", "trend_ema_period", default=50)
    bar_interval: str = _yaml_get("strategies", "rsi_trend", "bar_interval", default="5m")
    # Allowed: "standard" | "fast_intraday_diagnostic" (paper-only, disabled by default)
    mode: str = _yaml_get("strategies", "rsi_trend", "mode", default="standard")


class BacktestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BACKTEST_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    position: PositionSettings = PositionSettings()
    universe: UniverseSettings = UniverseSettings()
    rsi_trend: RSITrendSettings = RSITrendSettings()

    # ── Paper evaluation permissive entry mode ────────────────────────────────
    # When true: single qualifying strategy signal can enter if scanner, liquidity,
    # spread, and risk gates all pass.  Requires paper_evaluation_mode=true and
    # live_trading_enabled=false.  Adds signal-to-trade bridge diagnostics and
    # deterministic signal ranking.  No effect on existing behavior when false.
    paper_eval_permissive_entry_mode: bool = False

    # ── ORB slot reservation ──────────────────────────────────────────────────
    # Before this ET time, if non-ORB entries used ≥ (max_trades_per_day − 1),
    # remaining entry slots are reserved for ORB signals only.
    # Active only when paper_eval_permissive_entry_mode=true.
    orb_slot_reserve_until: str = "11:30"

    # ── Paper evaluation mode ─────────────────────────────────────────────────
    # When enabled: paper-only session with pre/post checklists, daily reports,
    # and a cumulative ledger.  Incompatible with live_trading_enabled=true.
    paper_evaluation_mode: bool = False
    evaluation_output_dir: str = "./evaluation"
    evaluation_ledger_file: str = "./evaluation/ledger.json"

    # ── Realistic fill test mode ──────────────────────────────────────────────
    # Forces marketable_limit pricing, SPY-only, qty=1, detailed telemetry.
    # Requires paper account.  Hard-stops if spread exceeds fill_test_max_spread_pct.
    realistic_fill_test_mode: bool = False
    entry_order_timeout_secs: int = 120    # stale cancel threshold for entry orders
    exit_order_timeout_secs: int = 120     # stale cancel threshold for exit orders
    fill_test_max_spread_pct: float = 0.20 # abort contract if spread/mid > this

    @model_validator(mode="after")
    def guard_eval_mode(self):
        if self.paper_evaluation_mode and self.live_trading_enabled:
            raise ValueError(
                "paper_evaluation_mode and live_trading_enabled cannot both be true."
            )
        if self.paper_eval_permissive_entry_mode:
            if self.live_trading_enabled:
                raise ValueError(
                    "paper_eval_permissive_entry_mode cannot be used with live_trading_enabled=true."
                )
            if not self.paper_evaluation_mode:
                raise ValueError(
                    "paper_eval_permissive_entry_mode requires paper_evaluation_mode=true."
                )
        return self

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
