"""Config loading and safety-invariant validation."""
import copy

import pytest

from src.config import ConfigError, load_config
from src.config.loader import StrategyConfig


def test_default_config_loads_and_is_paper_shadow():
    cfg = load_config()
    assert cfg.risk_fraction <= 0.01
    assert cfg.active_dollar_volume_floor >= 5_000_000
    assert cfg.get("allow_live") is False
    assert cfg.mode in {"BACKTEST", "SHADOW", "PAPER_CONFIRM", "PAPER_AUTO"}


def _base():
    return copy.deepcopy(load_config().as_dict())


def test_allow_live_true_rejected():
    d = _base()
    d["allow_live"] = True
    with pytest.raises(ConfigError):
        StrategyConfig(d)


def test_risk_fraction_above_one_percent_rejected():
    d = _base()
    d["risk"]["risk_fraction"] = 0.02
    with pytest.raises(ConfigError):
        StrategyConfig(d)


def test_dollar_volume_floor_below_5m_rejected():
    d = _base()
    d["universe"]["active_min_dollar_volume"] = 1_000_000
    with pytest.raises(ConfigError):
        StrategyConfig(d)


def test_min_price_below_one_rejected():
    d = _base()
    d["universe"]["min_price"] = 0.50
    with pytest.raises(ConfigError):
        StrategyConfig(d)


def test_bad_candidate_mode_rejected():
    d = _base()
    d["candidate_mode"] = "whatever"
    with pytest.raises(ConfigError):
        StrategyConfig(d)
