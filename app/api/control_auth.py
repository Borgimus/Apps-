"""Authentication for dashboard endpoints that change trading state.

The dashboard was built for a localhost bind, but it has been reached over a
tailnet, where a local proxy makes every request look like it came from
127.0.0.1. Source address is therefore never trusted; a bearer token is.
"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException

from app.config import get_settings

MIN_TOKEN_LENGTH = 16


def _expected_token() -> str:
    return get_settings().dashboard_control_token or ""


def require_control_token(authorization: Optional[str] = Header(default=None)) -> None:
    expected = _expected_token()
    if len(expected) < MIN_TOKEN_LENGTH:
        raise HTTPException(
            status_code=503,
            detail="Control endpoints are disabled: set DASHBOARD_CONTROL_TOKEN "
                   f"({MIN_TOKEN_LENGTH}+ characters).",
        )
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status_code=401, detail="Valid bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def allowed_origins(raw: str) -> list[str]:
    return [o.strip() for o in (raw or "").split(",") if o.strip() and o.strip() != "*"]
