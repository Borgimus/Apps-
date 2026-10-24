"""Authentication for dashboard endpoints that change trading state.

The dashboard was built for a localhost bind, but it has been reached over a
tailnet, where a local proxy makes every request look like it came from
127.0.0.1. Source address is therefore never trusted; a bearer token is.
"""
from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException

MIN_TOKEN_LENGTH = 16


# Read from the environment rather than Settings: app/config/settings.py is a
# frozen file the operations installer will not deploy changes to.
def _expected_token() -> str:
    return os.getenv("DASHBOARD_CONTROL_TOKEN", "")


def configured_origins() -> list[str]:
    return allowed_origins(os.getenv("DASHBOARD_ALLOWED_ORIGINS", ""))


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
