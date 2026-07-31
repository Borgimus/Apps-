"""AI review: advisory-only boundaries, metadata, and non-authoritative output."""
import pytest

from src.ai_review.boundary import (
    AdvisoryBoundaryError,
    assert_advisory_only,
    authoritative_decision,
    review_can_promote_shadow,
)
from src.ai_review.interface import AIReview, Reviewer
from src.ai_review.reviewer import TemplateReviewer


def test_reviewer_produces_metadata():
    r = TemplateReviewer()
    review = r.explain_setup("setup-1", {"slope10_positive": True, "score": 3.5})
    assert isinstance(review, AIReview)
    assert review.model_name and review.prompt_version == "ai_review_v1"
    assert review.tokens_in > 0 and review.tokens_out > 0
    assert review.cost_usd >= 0.0
    rec = review.as_record()
    assert rec["advisory_only"] is True and rec["subject_id"] == "setup-1"


def test_all_advisory_methods_available():
    r = TemplateReviewer()
    assert r.summarize_day("d", {}).subject_type == "day"
    assert r.cluster_rejections("r", [{"reason": "stale"}]).subject_type == "rejections"
    assert r.draft_anomaly_issue("a", {"kind": "drift"}).subject_type == "anomaly"
    assert r.eod_report("e", {"win_rate": 0.5}).subject_type == "eod_report"


def test_reviewer_exposes_no_action_methods():
    assert_advisory_only(TemplateReviewer())  # must not raise


def test_reviewer_with_forbidden_method_is_rejected():
    class Rogue(TemplateReviewer):
        def place_order(self, *a, **k):
            return "boom"

    with pytest.raises(AdvisoryBoundaryError):
        assert_advisory_only(Rogue())


def test_ai_cannot_flip_deterministic_decision():
    r = TemplateReviewer()
    review = r.explain_setup("s1", {"required_pass": False})
    deterministic = {"accepted": False, "shares": 0, "reject_reason": "STOP_TOO_WIDE"}
    out = authoritative_decision(deterministic, review)
    # Deterministic fields are untouched; the review rides along only as an audit record.
    assert out["accepted"] is False and out["shares"] == 0
    assert out["reject_reason"] == "STOP_TOO_WIDE"
    assert out["ai_review"]["subject_id"] == "s1"


def test_non_advisory_review_refused():
    bad = AIReview("setup", "s", "m", "v", "p", "o", advisory_only=False)
    with pytest.raises(AdvisoryBoundaryError):
        authoritative_decision({"accepted": True}, bad)


def test_shadow_never_auto_promotes():
    assert review_can_promote_shadow() is False


def test_reviewer_is_a_reviewer_subclass():
    assert isinstance(TemplateReviewer(), Reviewer)
