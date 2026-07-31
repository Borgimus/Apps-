"""AI review: advisory-only interfaces and metadata.

AI may explain, summarize, cluster, draft anomaly issues, and produce reports. AI may NOT change
risk limits or config, invent market data, place/resize/cancel/replace orders, override
reconciliation/stale-data/session rules, or promote a shadow rule into execution. Those powers do
not exist on these interfaces by construction — there is no method that returns anything the
deterministic pipeline consumes as an instruction. Every review stores model name, prompt version,
timestamp, and token/cost metadata. The deterministic decision record remains authoritative.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class AIReview:
    subject_type: str
    subject_id: str
    model_name: str
    prompt_version: str
    prompt: str
    output: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    advisory_only: bool = True   # invariant: never authoritative

    def as_record(self) -> dict:
        return {
            "subject_type": self.subject_type, "subject_id": self.subject_id,
            "model_name": self.model_name, "prompt_version": self.prompt_version,
            "prompt": self.prompt, "output": self.output, "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out, "cost_usd": self.cost_usd,
            "at": self.at.isoformat(), "advisory_only": self.advisory_only,
        }


class ModelClient(Protocol):
    """Injected text model. Returns (text, tokens_in, tokens_out, cost_usd). No side effects."""
    def generate(self, prompt: str) -> tuple[str, int, int, float]: ...


class Reviewer(abc.ABC):
    """Advisory reviewer. NOTE: deliberately exposes no order/risk/config mutation methods."""

    @abc.abstractmethod
    def explain_setup(self, subject_id: str, structured: dict) -> AIReview: ...

    @abc.abstractmethod
    def summarize_day(self, subject_id: str, structured: dict) -> AIReview: ...

    @abc.abstractmethod
    def cluster_rejections(self, subject_id: str, rejections: list[dict]) -> AIReview: ...

    @abc.abstractmethod
    def draft_anomaly_issue(self, subject_id: str, anomaly: dict) -> AIReview: ...

    @abc.abstractmethod
    def eod_report(self, subject_id: str, metrics: dict) -> AIReview: ...
