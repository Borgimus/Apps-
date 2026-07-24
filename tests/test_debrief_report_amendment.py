from types import SimpleNamespace

from app.evaluation.post_session import _apply_debrief_amendments


def _report(**overrides):
    values = {
        "total_signals": 0,
        "bridge_entries_count": 0,
        "trades_filled": 0,
        "notes": [],
        "recommendations": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_bridge_count_replaces_missing_signal_rows_with_explanation():
    report = _report(bridge_entries_count=727, trades_filled=3)

    _apply_debrief_amendments(report)

    assert report.total_signals == 727
    assert any("signal-bridge evaluations" in note for note in report.notes)
    assert any("dedup/cooldown" in note for note in report.notes)


def test_low_sample_suppresses_parameter_change_recommendations():
    report = _report(
        trades_filled=3,
        recommendations=[
            "Review signal filters — high rejection rate may indicate overly strict criteria",
            "Review universe scan settings; consider adjusting min_scan_score or rvol threshold",
            "Investigate API errors in logs",
        ],
    )

    _apply_debrief_amendments(report)

    joined = " ".join(report.recommendations).lower()
    assert "review signal filters" not in joined
    assert "min_scan_score" not in joined
    assert "investigate api errors" in joined
    assert "hold strategy thresholds" in joined
    assert any("only 3 fill(s)" in note for note in report.notes)


def test_sample_gate_does_not_suppress_after_thirty_fills():
    original = "Review signal filters — inspect rejection quality"
    report = _report(trades_filled=30, recommendations=[original])

    _apply_debrief_amendments(report)

    assert report.recommendations == [original]


def test_exit_reason_breakdown_lists_all_closed_trade_reasons():
    report = _report(trades_filled=3)

    _apply_debrief_amendments(
        report,
        ["trailing_stop", "take_profit", "trailing_stop"],
    )

    note = next(note for note in report.notes if "Exit reason breakdown" in note)
    assert "take_profit=1" in note
    assert "trailing_stop=2" in note


def test_unclassified_exit_reason_creates_observability_recommendation():
    report = _report(trades_filled=2)

    _apply_debrief_amendments(report, ["take_profit", None])

    assert any("unclassified=1" in note for note in report.notes)
    assert any("missing exit_reason" in rec for rec in report.recommendations)


def test_amendment_is_idempotent():
    report = _report(
        bridge_entries_count=12,
        trades_filled=2,
        recommendations=["Review signal filters — inspect rejection quality"],
    )

    _apply_debrief_amendments(report, ["trailing_stop", "take_profit"])
    _apply_debrief_amendments(report, ["trailing_stop", "take_profit"])

    assert len(report.notes) == len(set(report.notes))
    assert len(report.recommendations) == len(set(report.recommendations))
