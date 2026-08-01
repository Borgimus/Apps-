"""Alpaca PAPER REST trading client (implements the client protocol AlpacaPaperBroker expects).

Paper-only: the base URL is validated against the paper host at construction. Credentials come
from the environment and are sent as headers on every request — they are NEVER logged. The HTTP
transport is injected (`send`) so request-building is unit-testable without httpx or network; the
default transport is built lazily from httpx only when no sender is supplied.

send(method, url, headers, json_body, params) -> (status_code:int, json:dict)
"""
from __future__ import annotations

from typing import Callable

from .alpaca_paper import assert_paper_endpoint

Sender = Callable[[str, str, dict, dict | None, dict | None], tuple[int, dict]]


def _default_httpx_sender() -> Sender:  # pragma: no cover - requires httpx + network
    import httpx

    client = httpx.Client(timeout=10.0)

    def send(method, url, headers, json_body, params):
        resp = client.request(method, url, headers=headers, json=json_body, params=params)
        try:
            body = resp.json()
        except Exception:
            body = {}
        return resp.status_code, body

    return send


class AlpacaPaperRESTClient:
    def __init__(self, base_url: str, key_id: str, secret_key: str, send: Sender | None = None):
        self.base = assert_paper_endpoint(base_url) and base_url.rstrip("/")
        if not key_id or not secret_key:
            raise ValueError("paper credentials required")
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Content-Type": "application/json",
        }
        self._send = send or _default_httpx_sender()

    def _request(self, method: str, path: str, *, json_body=None, params=None) -> dict:
        status, body = self._send(method, f"{self.base}{path}", self._headers, json_body, params)
        if status >= 400:
            # Do not include headers/credentials in the error.
            raise RuntimeError(f"alpaca {method} {path} -> HTTP {status}: {body}")
        return body

    # -- client protocol used by AlpacaPaperBroker --------------------------
    def get_account(self) -> dict:
        return self._request("GET", "/v2/account")

    def list_positions(self) -> list[dict]:
        return self._request("GET", "/v2/positions")  # Alpaca returns a JSON array

    def list_orders(self, status: str = "open") -> list[dict]:
        return self._request("GET", "/v2/orders", params={"status": status})

    def submit_order(self, *, symbol, side, qty, type, limit_price, stop_price,
                     client_order_id, time_in_force) -> dict:
        payload = {
            "symbol": symbol, "side": side, "qty": str(qty), "type": type,
            "time_in_force": time_in_force, "client_order_id": client_order_id,
        }
        if limit_price is not None:
            payload["limit_price"] = str(limit_price)
        if stop_price is not None:
            payload["stop_price"] = str(stop_price)
        return self._request("POST", "/v2/orders", json_body=payload)

    def cancel_order(self, broker_order_id: str) -> None:
        self._request("DELETE", f"/v2/orders/{broker_order_id}")

    def replace_order(self, broker_order_id: str, *, stop_price=None, limit_price=None) -> dict:
        body = {}
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        return self._request("PATCH", f"/v2/orders/{broker_order_id}", json_body=body)
