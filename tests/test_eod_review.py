import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import eod_review


def test_clean_date_validates_format_and_calendar():
    assert eod_review.clean_date("2026-07-22") == "2026-07-22"
    with pytest.raises(HTTPException):
        eod_review.clean_date("07/22/2026")
    with pytest.raises(HTTPException):
        eod_review.clean_date("2026-02-30")


def test_file_review_aggregates_only_requested_shadow_day(tmp_path: Path):
    reports = tmp_path / "evaluation" / "reports"
    reports.mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    (reports / "2026-07-22.json").write_text(json.dumps({
        "realized_pnl": -10,
        "trades_filled": 3,
        "win_rate": 1 / 3,
        "fill_rate": 1,
    }), encoding="utf-8")
    (tmp_path / "evaluation" / "shadow_book.jsonl").write_text("\n".join([
        json.dumps({
            "event": "shadow_close",
            "ts": "2026-07-22T11:00:00-04:00",
            "shadow_pnl": 28,
            "fill_validated": True,
        }),
        json.dumps({
            "event": "shadow_close",
            "ts": "2026-07-22T11:10:00-04:00",
            "shadow_pnl": -11,
            "fill_validated": False,
        }),
        json.dumps({
            "event": "shadow_close",
            "ts": "2026-07-21T11:10:00-04:00",
            "shadow_pnl": 99,
            "fill_validated": True,
        }),
    ]), encoding="utf-8")

    review = eod_review.file_review("2026-07-22", tmp_path)

    assert review["realized_pnl"] == -10
    assert review["shadow_pnl"] == 17
    assert len(review["shadow_trades"]) == 2


def test_render_is_accessible_printable_and_escapes_values():
    review = {
        "date": "2026-07-22",
        "status": "diagnostic",
        "counts_toward_phase3": False,
        "realized_pnl": -6,
        "trades_filled": 1,
        "win_rate": 0,
        "fill_rate": 1,
        "api_errors": 0,
        "sample_warning": None,
        "notes": [],
        "recommendations": [],
        "by_strategy": [],
        "contamination_flags": [],
        "contamination_note": None,
        "shadow_trades": [],
        "shadow_pnl": 0,
        "broker_trades": [{
            "symbol": "SPY",
            "contract": "SPY<unsafe>",
            "strategy": "orb",
            "direction": "long",
            "entry_time": "2026-07-22T10:00:00-04:00",
            "entry_price": 0.45,
            "exit_time": "2026-07-22T10:07:00-04:00",
            "exit_price": 0.39,
            "hold_seconds": 420,
            "pnl": -6,
            "reason": "trailing_stop",
        }],
    }

    html = eod_review.render(review, ["2026-07-22"])

    assert '<main id="review">' in html
    assert 'href="#review"' in html
    assert 'aria-label="Review controls"' in html
    assert "Print / save PDF" in html
    assert "@media print" in html
    assert "SPY&lt;unsafe&gt;" in html
    assert "SPY<unsafe>" not in html
