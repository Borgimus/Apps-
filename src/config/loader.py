"""Load and validate the versioned strategy configuration.

Enforces hard safety invariants at load time:
  * allow_live must be false (no live trading, ever)
  * risk_fraction must be > 0 and <= 0.01 (1% cap; configurable DOWN only)
  * the active dollar-volume floor must be >= $5,000,000
  * candidate_mode / mode must be recognized values
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise ImportError("pyyaml is required to load strategy config") from exc


class ConfigError(ValueError):
    """Raised when the strategy configuration violates a safety invariant."""


VALID_MODES = {"BACKTEST", "SHADOW", "PAPER_CONFIRM", "PAPER_AUTO"}
VALID_CANDIDATE_MODES = {"intersection_3_of_3", "agreement_2_of_3", "union_ranked"}
MAX_RISK_FRACTION = 0.01
MIN_DOLLAR_VOLUME_FLOOR = 5_000_000


class StrategyConfig:
    """Thin validated wrapper over the parsed YAML config."""

    def __init__(self, data: dict[str, Any]):
        self._data = data
        self.validate()

    # -- access --------------------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    @property
    def version(self) -> str:
        return self._data["config_version"]

    @property
    def mode(self) -> str:
        return self._data["mode"]

    @property
    def risk_fraction(self) -> float:
        return float(self._data["risk"]["risk_fraction"])

    @property
    def active_dollar_volume_floor(self) -> float:
        return float(self._data["universe"]["active_min_dollar_volume"])

    @property
    def candidate_mode(self) -> str:
        return self._data["candidate_mode"]

    def as_dict(self) -> dict[str, Any]:
        return self._data

    # -- validation ----------------------------------------------------------
    def validate(self) -> None:
        d = self._data
        for key in ("config_version", "mode", "candidate_mode", "risk", "universe"):
            if key not in d:
                raise ConfigError(f"missing required top-level key: {key}")

        if d.get("allow_live", False):
            raise ConfigError("allow_live must be false — live trading is never permitted")

        if d["mode"] not in VALID_MODES:
            raise ConfigError(f"invalid mode {d['mode']!r}; expected one of {sorted(VALID_MODES)}")

        if d["candidate_mode"] not in VALID_CANDIDATE_MODES:
            raise ConfigError(
                f"invalid candidate_mode {d['candidate_mode']!r}; "
                f"expected one of {sorted(VALID_CANDIDATE_MODES)}"
            )

        rf = d["risk"].get("risk_fraction")
        if rf is None or not (0 < float(rf) <= MAX_RISK_FRACTION):
            raise ConfigError(
                f"risk_fraction must be in (0, {MAX_RISK_FRACTION}]; got {rf!r}. "
                "It is configurable DOWNWARD only."
            )

        floor = d["universe"].get("active_min_dollar_volume")
        if floor is None or float(floor) < MIN_DOLLAR_VOLUME_FLOOR:
            raise ConfigError(
                f"active_min_dollar_volume must be >= {MIN_DOLLAR_VOLUME_FLOOR}; got {floor!r}"
            )

        vol = d.get("volatility", {}).get("metric", "ADR_PCT")
        if vol not in {"ADR_PCT", "ATR_PCT"}:
            raise ConfigError(f"volatility.metric must be ADR_PCT or ATR_PCT; got {vol!r}")

        if float(d["universe"].get("min_price", 0)) < 1.0:
            raise ConfigError("universe.min_price must be >= 1.0 (exclude <=$1 stocks)")


def load_config(path: str | os.PathLike | None = None) -> StrategyConfig:
    """Load config from ``path`` (defaults to config/strategy.yaml at repo root)."""
    if path is None:
        path = Path(__file__).resolve().parents[2] / "config" / "strategy.yaml"
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError("config root must be a mapping")
    return StrategyConfig(data)
