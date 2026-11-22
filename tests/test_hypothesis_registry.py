from datetime import datetime, timezone
import json

import pytest

from app.api.hypotheses import hypotheses_page, router
from app.hypotheses.registry import REGISTRY, load_registry, transition


def test_dashboard_registry_only_exposes_read_routes():
    assert all(route.methods == {"GET"} for route in router.routes)
    page = hypotheses_page()
    assert "Sector-relative reversal" in page
    assert "Broker entries disabled" in page


def test_registry_status_history_and_required_blocker(tmp_path):
    path = tmp_path / REGISTRY
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": 1, "hypotheses": {
        "S1": {"name": "Fixture", "status": "registered", "transitions": []}}}))
    options = {"commit": "a" * 40, "path": path.relative_to(tmp_path),
               "now": datetime(2026, 11, 22, tzinfo=timezone.utc)}
    transition(tmp_path, "S1", "screening", **options)
    with pytest.raises(ValueError, match="reason"):
        transition(tmp_path, "S1", "blocked", **options)
    transition(tmp_path, "S1", "blocked", reason="No data", **options)
    item = load_registry(tmp_path)["hypotheses"]["S1"]
    assert [t["status"] for t in item["transitions"]] == ["screening", "blocked"]
