"""Frozen research inputs. This module has no broker or network operations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROTOCOL = 'iex_equity_signal_screen_v1_2026_10_24'
BASELINE_FILE = 'evaluation/equity_research_baseline_2026_11_03.json'
FROZEN_FILES = (
    'app/strategies/opening_range_breakout.py', 'app/strategies/vwap_strategy.py',
    'app/strategies/strategy_base.py', 'config/ticker_universe.yaml',
    'app/research/equity_observer.py', 'app/research/baseline.py',
)
STRATEGY_PARAMETERS = {
    'orb': {'range_minutes': 15, 'min_range_pts': .5,
            'volume_confirmation': True, 'confirmation_bars': 2},
    'vwap_reclaim': {'proximity_pct': .002, 'confirmation_bars': 2},
}


def frozen_baseline(root: Path) -> dict:
    """Refuse source/parameter drift instead of silently mixing experiments."""
    raw = (root / BASELINE_FILE).read_bytes()
    baseline = json.loads(raw)
    if (baseline.get('protocol') != PROTOCOL
            or baseline.get('strategy_parameters') != STRATEGY_PARAMETERS
            or set(baseline.get('files_sha256', {})) != set(FROZEN_FILES)):
        raise ValueError('Frozen research parameters differ')
    if baseline['verified_legacy_source_commits']:
        if baseline['verified_legacy_strategy_parameters'] != STRATEGY_PARAMETERS:
            raise ValueError('Legacy strategy parameters differ; declare a new cohort')
        for commit in baseline['verified_legacy_source_commits']:
            legacy = baseline['legacy_source_files_sha256'][commit]
            for name in FROZEN_FILES[:4]:
                if legacy[name] != baseline['files_sha256'][name]:
                    raise ValueError('Legacy strategy source differs; declare a new cohort')
    for name, expected in baseline['files_sha256'].items():
        path = root / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen research source differs: ' + name)
    return {**baseline, 'baseline_sha256': hashlib.sha256(raw).hexdigest()}


def baseline_identity(baseline: dict) -> dict:
    return {key: baseline[key] for key in ('protocol', 'baseline_sha256', 'files_sha256')}
