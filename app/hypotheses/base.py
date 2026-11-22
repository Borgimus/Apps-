"""Plug-in interface for intraday replay."""
from typing import Iterable, Protocol

from app.research.replay.engine import HypothesisSignal, View


class Hypothesis(Protocol):
    id: str
    def __call__(self, view: View) -> Iterable[HypothesisSignal]: ...
