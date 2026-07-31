"""Concrete advisory reviewer + a deterministic template model for tests.

The template model produces stable text from structured deterministic inputs (no network, no
credentials), so the AI-review pipeline is testable offline. A production deployment injects a
real ModelClient; the reviewer's method surface is identical and still advisory-only.
"""
from __future__ import annotations

from .interface import AIReview, ModelClient, Reviewer

PROMPT_VERSION = "ai_review_v1"


class DeterministicTemplateClient:
    """A stand-in ModelClient: echoes a compact, deterministic summary of the prompt."""

    model_name = "template-deterministic-1"

    def generate(self, prompt: str) -> tuple[str, int, int, float]:
        text = "SUMMARY: " + " ".join(prompt.split())[:400]
        tokens_in = len(prompt.split())
        tokens_out = len(text.split())
        cost = round((tokens_in + tokens_out) * 0.0, 6)  # template is free
        return text, tokens_in, tokens_out, cost


class TemplateReviewer(Reviewer):
    def __init__(self, client: ModelClient | None = None, model_name: str | None = None):
        self._client = client or DeterministicTemplateClient()
        self._model_name = model_name or getattr(self._client, "model_name", "unknown-model")

    def _review(self, subject_type: str, subject_id: str, prompt: str) -> AIReview:
        text, ti, to, cost = self._client.generate(prompt)
        return AIReview(subject_type=subject_type, subject_id=subject_id,
                        model_name=self._model_name, prompt_version=PROMPT_VERSION,
                        prompt=prompt, output=text, tokens_in=ti, tokens_out=to, cost_usd=cost)

    def explain_setup(self, subject_id: str, structured: dict) -> AIReview:
        prompt = (f"Explain, using ONLY these deterministic indicator/component values, why the "
                  f"setup passed or failed: {structured}. Do not suggest changing any rule.")
        return self._review("setup", subject_id, prompt)

    def summarize_day(self, subject_id: str, structured: dict) -> AIReview:
        return self._review("day", subject_id, f"Summarize the day's structured activity: {structured}")

    def cluster_rejections(self, subject_id: str, rejections: list[dict]) -> AIReview:
        return self._review("rejections", subject_id,
                            f"Cluster these rejected candidates by reason for research: {rejections}")

    def draft_anomaly_issue(self, subject_id: str, anomaly: dict) -> AIReview:
        return self._review("anomaly", subject_id,
                            f"Draft a GitHub issue describing this anomaly for human review: {anomaly}")

    def eod_report(self, subject_id: str, metrics: dict) -> AIReview:
        return self._review("eod_report", subject_id,
                            f"Write an end-of-day report from these reconciled metrics: {metrics}")
