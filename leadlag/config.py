"""Config for the ES/SPY lead-lag study. Credentials from environment only:
DATABENTO_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "leadlag.yaml"


def _t(s: str) -> dt.time:
    h, m, sec = (list(map(int, s.split(":"))) + [0])[:3]
    return dt.time(h, m, sec)


@dataclass
class Config:
    raw: dict
    fut_symbol: str
    fut_verify_symbol: str
    equity: str
    dataset: str
    cache_dir: Path
    fetch_start: dt.time
    fetch_end: dt.time
    study_start: dt.time
    study_end: dt.time
    history_start: dt.date
    grid: dict
    phase2: dict
    phase3: dict
    phase4: dict
    strategy: dict
    validation: dict
    out_dir: Path
    databento_key: str | None
    alpaca_key: str | None
    alpaca_secret: str | None


def load_config(path: Path | str = CONFIG_PATH) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    d = raw["data"]
    return Config(
        raw=raw,
        fut_symbol=raw["instruments"]["futures_symbol"],
        fut_verify_symbol=raw["instruments"]["futures_verify_symbol"],
        equity=raw["instruments"]["equity"],
        dataset=d["databento_dataset"],
        cache_dir=Path(d["cache_dir"]),
        fetch_start=_t(d["fetch_start_et"]),
        fetch_end=_t(d["fetch_end_et"]),
        study_start=_t(d["study_start_et"]),
        study_end=_t(d["study_end_et"]),
        history_start=dt.date.fromisoformat(d["history_start"]),
        grid=raw["grid"],
        phase2=raw["phase2"],
        phase3=raw["phase3"],
        phase4=raw["phase4"],
        strategy=raw["strategy"],
        validation=raw["validation"],
        out_dir=Path(raw["report"]["out_dir"]),
        databento_key=os.getenv("DATABENTO_API_KEY"),
        alpaca_key=os.getenv("ALPACA_API_KEY"),
        alpaca_secret=os.getenv("ALPACA_SECRET_KEY"),
    )
