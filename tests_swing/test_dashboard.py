"""Dashboard state assembly and bearer auth."""
from datetime import datetime
from zoneinfo import ZoneInfo

from src.api.auth import extract_bearer, is_authorized
from src.api.dashboard import (
    DashboardInputs,
    PositionView,
    build_dashboard_state,
    render_html,
)

ET = ZoneInfo("America/New_York")


def _inputs(**kw):
    base = dict(
        now=datetime(2026, 7, 31, 10, 0, tzinfo=ET),  # regular session
        config_version="1.0.0",
        mode="SHADOW",
        paper_endpoint="https://paper-api.alpaca.markets",
        broker_connected=True,
        data_connected=True,
        account={"equity": 100000, "buying_power": 200000, "realized_pnl": 0,
                 "unrealized_pnl": 0, "drawdown": 0},
        positions=[PositionView("AAA", 100, 50.0, 47.5, 1.2, "OPEN_INITIAL_RISK")],
        latest_import={"received_at": datetime(2026, 7, 31, 9, 0, tzinfo=ET).isoformat(),
                       "market_date": "2026-07-31", "status": "ACCEPTED"},
    )
    base.update(kw)
    return DashboardInputs(**base)


def test_all_sections_present_and_paper_verified():
    state = build_dashboard_state(_inputs())
    for key in ("health", "market_session", "tc2000_batch", "candidates", "setups",
                "positions", "open_orders", "risk", "reconciliation", "reports"):
        assert key in state
    assert state["health"]["paper_endpoint_verified"] is True
    assert state["market_session"] == "REGULAR"
    assert state["tc2000_batch"]["fresh"] is True
    assert state["risk"]["committed_risk"] == 250.0  # (50-47.5)*100


def test_non_paper_endpoint_flagged_and_masked():
    state = build_dashboard_state(_inputs(paper_endpoint="https://api.alpaca.markets"))
    assert state["health"]["paper_endpoint_verified"] is False
    assert "rejected" in state["health"]["paper_endpoint"]


def test_missing_stop_blocks_new_risk():
    pos = [PositionView("AAA", 100, 50.0, None, None, "OPEN_INITIAL_RISK")]
    state = build_dashboard_state(_inputs(positions=pos))
    assert state["reconciliation"]["positions_missing_stop"] == ["AAA"]
    assert state["reconciliation"]["blocks_new_risk"] is True


def test_stale_import_not_fresh():
    old = {"received_at": datetime(2026, 7, 28, 9, 0, tzinfo=ET).isoformat(),
           "market_date": "2026-07-28", "status": "ACCEPTED"}
    state = build_dashboard_state(_inputs(latest_import=old))
    assert state["tc2000_batch"]["fresh"] is False


def test_render_html_contains_banner():
    html = render_html(build_dashboard_state(_inputs()))
    assert "PAPER VERIFIED" in html and "Swing Dashboard" in html


def test_bearer_auth():
    assert extract_bearer("Bearer abc") == "abc"
    assert extract_bearer("Basic abc") is None
    assert is_authorized("Bearer secret", "secret") is True
    assert is_authorized("Bearer wrong", "secret") is False
    assert is_authorized("Bearer x", "") is False   # no configured token => fail closed
    assert is_authorized(None, "secret") is False
