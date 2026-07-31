"""Live round-trip of the stdlib dashboard server: health/ready public, state/root authed."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from src.api.server import serve


@pytest.fixture
def running_server():
    state = {"health": {"mode": "SHADOW"}, "positions": [], "risk": {"equity": 1},
             "market_session": "REGULAR", "tc2000_batch": {}, "reconciliation": {"ok": True,
             "positions_missing_stop": []}}
    ready = {"v": False}
    httpd = serve("127.0.0.1", 0, token="s3cr3t",
                  state_provider=lambda: state, ready_provider=lambda: ready["v"])
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port, ready
    httpd.shutdown()


def _get(port, path, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=5)


def test_health_is_public(running_server):
    port, _ = running_server
    assert _get(port, "/health").status == 200


def test_ready_reflects_provider(running_server):
    port, ready = running_server
    with pytest.raises(urllib.error.HTTPError) as ei:
        _get(port, "/ready")
    assert ei.value.code == 503   # not ready until startup reconciliation completes
    ready["v"] = True
    assert _get(port, "/ready").status == 200


def test_state_requires_auth(running_server):
    port, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as ei:
        _get(port, "/state")
    assert ei.value.code == 401
    resp = _get(port, "/state", token="s3cr3t")
    assert resp.status == 200
    assert json.loads(resp.read())["market_session"] == "REGULAR"


def test_wrong_token_rejected(running_server):
    port, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as ei:
        _get(port, "/", token="wrong")
    assert ei.value.code == 401
