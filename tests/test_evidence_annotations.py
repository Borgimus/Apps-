from copy import deepcopy
from pathlib import Path
from app.config import Settings

from app.evaluation.evidence_annotations import exit_trigger_coverage, health_review
from app.evaluation.shadow_book import ShadowBook
from app.evaluation.shadow_funnel import rejection_breakdown
from app.evaluation.shadow_summary import read_events, summarize, to_markdown


def test_exit_trigger_coverage_separates_unknown_and_whole_contract_eligibility():
    def close(pair, quantity, peak, **changes):
        return dict(pair_id=pair, variant="baseline", quantity=quantity, entry_price=1.0,
                    peak_price=peak, fill_validated=True, sized_shadow_pnl=0, **changes)
    rows = [close("one", 1, 1.3), close("two", 2, 1.25), close("below", 2, 1.24),
            close("unknown", 3, None)]
    result = exit_trigger_coverage(rows, {"replay_policy": {"variant_trigger_pct": .25}})
    assert result["baseline"] == dict(opportunities=4, known_peak_paths=3,
                                       observed_threshold_reached=2, unknown_peak_paths=1)
    assert result["partial_executable"]["opportunities"] == 3
    assert result["partial_executable"]["observed_threshold_reached"] == 1
    assert result["partial_ineligible_or_quantity_unknown"] == 1
    assert exit_trigger_coverage(rows, {})["baseline"]["known_peak_paths"] == 0


def test_sole_blockers_do_not_double_count_repeated_or_overlapping_rejections():
    def signal(pair, reasons):
        return dict(event="signal", session_id="s", opportunity_id=pair,
                    entry_filter_eligible=False, entry_filter_reasons=reasons)
    events = [signal("a", ["budget"])] * 20 + [signal("b", ["budget", "regime"]), signal("c", [])]
    result = rejection_breakdown(events)
    assert result["unique_setups"] == 3
    assert result["sole_initial_blockers"] == {"budget": 1}
    assert result["initial_multiple_blockers"] == 1


def test_historical_checkpoint_retains_review_and_reports_nontriggering_exits():
    source = Path(__file__).resolve().parents[1] / "evaluation/shadow_book.jsonl"
    events = read_events(source, date="2026-09-18")
    report = summarize(events, "2026-09-18")
    progress = report["observation_progress"]
    assert (progress["completed_scheduled_sessions"], progress["eligible_opportunities"]) == (5, 6)
    assert progress["reviewed_health_exceptions"][0]["date"] == "2026-09-11"
    subset = progress["without_known_reviewed_outages"]
    assert (subset["sessions"], subset["eligible_opportunities"]) == (4, 5)
    assert not progress["automatic_strategy_activation"]
    known = crossings = 0
    for day in progress["dates"]:
        coverage = summarize(events, day)["exit_trigger_coverage"]["baseline"]
        known += coverage["known_peak_paths"]
        crossings += coverage["observed_threshold_reached"]
    assert (known, crossings) == (6, 0)
    assert "Health exception retained: 2026-09-11" in to_markdown(report)
    context = next(r["session_context"] for r in events
                   if r.get("event") == "shadow_session_start" and r["ts"].startswith("2026-09-11"))
    assert health_review(context)
    different = deepcopy(context)
    different["replay_policy_hash"] = "another-policy"
    assert health_review(different) is None


def test_shadow_book_follows_external_evidence_directory_and_explicit_override(tmp_path):
    settings = Settings(_env_file=None, evaluation_output_dir=str(tmp_path / "external"))
    default = ShadowBook(settings, state_path=tmp_path / "state.json")
    assert default._events_path == tmp_path / "external/shadow_book.jsonl"
    override = ShadowBook(settings, events_path=tmp_path / "custom/events.jsonl", state_path=tmp_path / "state2.json")
    assert override._events_path == tmp_path / "custom/events.jsonl"
