"""Shared fixtures/helpers for the swing-trading deterministic test suite.

Ensures the repo root is importable as ``src`` and provides small bar builders.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def bar(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


@pytest.fixture
def make_bar():
    return bar


@pytest.fixture
def strategy_cfg():
    from src.config import load_config
    return load_config()
