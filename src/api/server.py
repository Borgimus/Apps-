"""Minimal authenticated dashboard server (stdlib http.server; no web framework dependency).

Routes:
  GET /health   -> 200 liveness (no auth)
  GET /ready    -> 200 only after startup reconciliation reports ready (no auth)
  GET /          -> HTML dashboard (bearer auth)
  GET /state    -> JSON dashboard state (bearer auth)

Auth uses a constant-time bearer-token check (src/api/auth.py). The token and any provider
credentials come from the environment and are never logged. ``state_provider`` is a callable
returning the current dashboard state dict, injected so the server stays deterministic/testable.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .auth import is_authorized
from .dashboard import render_html


def make_handler(*, token: str | None, state_provider: Callable[[], dict],
                 ready_provider: Callable[[], bool]):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str, content_type: str = "text/plain") -> None:
            payload = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _authed(self) -> bool:
            return is_authorized(self.headers.get("Authorization"), token)

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            if self.path == "/health":
                self._send(200, "ok")
                return
            if self.path == "/ready":
                self._send(200 if ready_provider() else 503,
                           "ready" if ready_provider() else "not-ready")
                return
            if not self._authed():
                self._send(401, "unauthorized")
                return
            state = state_provider()
            if self.path == "/state":
                self._send(200, json.dumps(state), "application/json")
            elif self.path == "/":
                self._send(200, render_html(state), "text/html; charset=utf-8")
            else:
                self._send(404, "not found")

        def log_message(self, *args) -> None:  # never log auth headers / secrets
            return

    return Handler


def serve(host: str, port: int, *, token: str | None, state_provider, ready_provider):
    handler = make_handler(token=token, state_provider=state_provider, ready_provider=ready_provider)
    return ThreadingHTTPServer((host, port), handler)
