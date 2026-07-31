"""Hard boundaries proving AI output is advisory and never authoritative.

- ``assert_advisory_only`` fails if a reviewer object exposes any order/risk/config mutation
  method — the AI surface must not offer a path to act.
- ``authoritative_decision`` returns the DETERMINISTIC decision unchanged regardless of any AI
  review content, and refuses to let a review flip a rejection into an acceptance (or vice versa).
- ``review_can_promote_shadow`` is always False: shadow rules never auto-promote via AI.
"""
from __future__ import annotations

from .interface import AIReview

# Method names that would grant the AI action authority — none may exist on a reviewer.
FORBIDDEN_METHODS = {
    "place_order", "submit_order", "cancel_order", "replace_order", "resize_order",
    "set_config", "update_config", "set_risk", "set_risk_fraction", "override",
    "set_endpoint", "promote_shadow", "approve", "force_entry", "force_exit",
}


class AdvisoryBoundaryError(RuntimeError):
    pass


def assert_advisory_only(reviewer: object) -> None:
    offending = [m for m in FORBIDDEN_METHODS if callable(getattr(reviewer, m, None))]
    if offending:
        raise AdvisoryBoundaryError(f"reviewer exposes forbidden action methods: {offending}")


def authoritative_decision(deterministic_decision: dict, review: AIReview | None) -> dict:
    """Return the deterministic decision unchanged. The AI review is attached for audit only and
    can never alter the accept/reject outcome, sizing, prices, or stops."""
    if review is not None and not review.advisory_only:
        raise AdvisoryBoundaryError("review marked non-advisory — refusing to apply")
    out = dict(deterministic_decision)
    out["ai_review"] = review.as_record() if review is not None else None
    # The authoritative fields are copied verbatim; nothing from the review touches them.
    return out


def review_can_promote_shadow() -> bool:
    return False
