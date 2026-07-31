"""Bearer-token authentication for the dashboard (constant-time comparison).

The token comes from the environment (SWING_DASHBOARD_TOKEN) — never committed. An empty or
missing configured token denies all access (fail-closed) rather than allowing everyone.
"""
from __future__ import annotations

import hmac


def extract_bearer(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def is_authorized(authorization_header: str | None, configured_token: str | None) -> bool:
    """True only if a non-empty configured token matches the presented bearer token."""
    if not configured_token:
        return False  # fail closed: no token configured => no access
    presented = extract_bearer(authorization_header)
    if not presented:
        return False
    return hmac.compare_digest(presented, configured_token)
