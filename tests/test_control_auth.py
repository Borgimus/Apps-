"""Dashboard endpoints that change trading state require a bearer token."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import control_auth
from app.api import sessions_router as sessions

TOKEN = "t" * 24


@pytest.fixture
def client(monkeypatch):
    start = AsyncMock(return_value={"started": True})
    monkeypatch.setattr(sessions._supervisor, "start", start)
    app = FastAPI()
    app.include_router(sessions.router)
    return TestClient(app), start


def _token(monkeypatch, value):
    monkeypatch.setattr(control_auth, "_expected_token", lambda: value)


def test_control_endpoints_are_refused_when_no_token_is_configured(client, monkeypatch):
    c, start = client
    _token(monkeypatch, "")
    resp = c.post("/sessions/start")
    assert resp.status_code == 503
    start.assert_not_awaited()


def test_short_configured_token_is_treated_as_unset(client, monkeypatch):
    c, start = client
    _token(monkeypatch, "short")
    assert c.post("/sessions/start", headers={"Authorization": "Bearer short"}).status_code == 503
    start.assert_not_awaited()


@pytest.mark.parametrize("header", [None, "Bearer wrong-token-value-xx", f"Basic {TOKEN}", TOKEN])
def test_missing_or_wrong_token_is_rejected(client, monkeypatch, header):
    c, start = client
    _token(monkeypatch, TOKEN)
    headers = {"Authorization": header} if header else {}
    assert c.post("/sessions/start", headers=headers).status_code == 401
    start.assert_not_awaited()


def test_correct_token_reaches_the_handler(client, monkeypatch):
    c, start = client
    _token(monkeypatch, TOKEN)
    resp = c.post("/sessions/start", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    start.assert_awaited_once()


def test_status_remains_readable_without_token(client, monkeypatch):
    c, _ = client
    _token(monkeypatch, "")
    monkeypatch.setattr(sessions._supervisor, "status", AsyncMock(return_value={}), raising=False)
    assert c.get("/sessions/status").status_code != 503


def test_wildcard_origin_is_never_allowed():
    assert control_auth.allowed_origins("*") == []
    assert control_auth.allowed_origins("") == []
    assert control_auth.allowed_origins("https://a.example, *, https://b.example") == [
        "https://a.example", "https://b.example"]


def test_dashboard_routes_are_guarded():
    """The kill switch can always be activated; everything that removes a
    protection or changes what runs needs the token."""
    from app.api.dashboard_api import create_app

    guarded = {}
    routes = [r for r in create_app().routes if hasattr(r, "path")] + list(sessions.router.routes)
    for route in routes:
        deps = getattr(route, "dependencies", []) or []
        methods = getattr(route, "methods", set()) or set()
        for method in methods - {"HEAD", "OPTIONS", "GET"}:
            guarded[(method, route.path)] = any(
                d.dependency is control_auth.require_control_token for d in deps)
    assert guarded.pop(("POST", "/kill-switch/activate")) is False
    assert ("DELETE", "/kill-switch") in guarded
    for path in ("/sessions/start", "/sessions/stop", "/sessions/restart"):
        assert ("POST", path) in guarded
    unguarded = [route for route, ok in guarded.items() if not ok]
    assert unguarded == [], f"state-changing routes without the control token: {unguarded}"
