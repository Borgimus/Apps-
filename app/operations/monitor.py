"""Independent, read-only session monitoring and bounded ntfy delivery.

No order methods, arming, Git updates or strategy activation occur here.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, time
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def read_json(path: Path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def notify(root: Path, code: str, message: str, *, now=None, sender=None, event_date=None) -> bool:
    """Deduplicate delivered alerts per ET day. Failed delivery stays retryable.

    Callers serialize with the operations lock. URLs and tokens are never logged.
    """
    now = (now or datetime.now(ET)).astimezone(ET)
    if not code.replace("_", "").isalnum():
        raise ValueError("Invalid alert code")
    from datetime import date
    day = date.fromisoformat(event_date) if event_date else now.date()
    path = root / "logs/operations" / f"{day}-{code}.json"
    previous = read_json(path)
    if previous.get("delivered"):
        return True
    record = {"code": code, "message": message, "date": str(day),
              "last_attempt": now.isoformat(), "delivered": False,
              "attempts": previous.get("attempts", 0) + 1}
    try:
        if sender is not None:
            sender(message)
        else:
            url = os.getenv("OPS_NTFY_URL", "")
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("OPS_NTFY_URL must be an HTTPS topic URL")
            headers = {"Title": "Trader operations", "Priority": "high", "Tags": "warning"}
            token = os.getenv("OPS_NTFY_TOKEN")
            if token:
                headers["Authorization"] = "Bearer " + token
            with httpx.Client(timeout=5, follow_redirects=False) as client:
                client.post(url, content=message.encode(), headers=headers).raise_for_status()
        record["delivered"] = True
    except Exception as exc:
        # Exception text can contain the notification topic or credential URL.
        record["delivery_error_type"] = type(exc).__name__
    write_json(path, record)
    print(f"OPS_ALERT code={code} delivered={record['delivered']}", flush=True)
    return record["delivered"]


async def market_day():
    """Verify paper identity before using its exchange calendar, even standalone."""
    from app.config import get_settings
    from app.brokers.factory import get_broker
    settings = get_settings()
    if settings.broker != "alpaca" or settings.live_trading_enabled:
        raise ValueError("Operations require Alpaca paper configuration")
    broker = get_broker(settings)
    try:
        async with asyncio.timeout(20):
            if not broker.verify_paper_endpoint()[0]:
                raise ValueError("Paper endpoint verification failed")
            account = await broker.get_account()
            if not account.is_paper:
                raise ValueError("Broker account is not paper")
            return bool(await broker.is_market_session_today())
    finally:
        await broker.close()


def calendar(root: Path, *, now=None, query=None, refresh=False):
    now = (now or datetime.now(ET)).astimezone(ET)
    if now.weekday() >= 5:
        return False
    path = root / "logs/operations" / f"calendar-{now.date()}.json"
    saved = read_json(path)
    if not refresh and saved.get("date") == str(now.date()) and type(saved.get("market_day")) is bool:
        return saved["market_day"]
    result = query() if query else asyncio.run(market_day())
    if type(result) is not bool:
        raise ValueError("Calendar returned an unknown state")
    write_json(path, {"date": str(now.date()), "market_day": result, "checked_at": now.isoformat()})
    return result


async def verify_flat():
    """Check raw Alpaca responses so adapter parsing cannot hide exposure.

    This is read-only. Any position or working order, missing capability,
    malformed response, timeout or failed paper identity blocks confirmation.
    """
    from app.config import get_settings
    from app.brokers.factory import get_broker
    settings = get_settings()
    if settings.broker != "alpaca" or settings.live_trading_enabled:
        raise ValueError("Close verification requires Alpaca paper configuration")
    broker = get_broker(settings)
    try:
        async with asyncio.timeout(20):
            if not broker.verify_paper_endpoint()[0]:
                raise ValueError("Paper endpoint verification failed")
            if not (await broker.get_account()).is_paper:
                raise ValueError("Broker account is not paper")
            # AlpacaTradierDataBroker inherits the Alpaca trading client.
            for path, params in (("/v2/positions", None),
                                 ("/v2/orders", {"status": "open", "limit": 1})):
                response = await broker._client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or payload:
                    raise ValueError("Broker exposure remains or response is malformed")
    finally:
        await broker.close()


def session_problem(snapshot: dict, now: datetime) -> str | None:
    """A PID or an arming marker does not prove that a runner processes cycles."""
    now = now.astimezone(ET)
    if now.weekday() >= 5 or not time(9, 35) <= now.time() < time(12, 30):
        return None
    if snapshot.get("session_date") != str(now.date()) or snapshot.get("session_active") is False:
        return "No active session heartbeat for today"
    try:
        stamp = datetime.fromisoformat(snapshot["ts"])
        if stamp.tzinfo is None:
            raise ValueError("Naive heartbeat")
        age = (now - stamp).total_seconds()
        if not 0 <= age <= 180 or int(snapshot.get("cycle", 0)) < 1:
            return "Session heartbeat is stale or invalid"
    except (KeyError, TypeError, ValueError, OverflowError):
        return "Session heartbeat is missing or invalid"
    return None


def watchdog(root: Path, *, now=None, query=None, sender=None) -> bool:
    now = (now or datetime.now(ET)).astimezone(ET)
    # Retry unsent preflight/start/publication alerts, including previous days.
    delivered = True
    for path in sorted((root / "logs/operations").glob("*.json")):
        record = read_json(path)
        if record.get("code") and record.get("delivered") is False:
            try:
                delivered &= notify(root, record["code"], record["message"], now=now,
                                    event_date=record["date"], sender=sender)
            except (ValueError, KeyError):
                delivered = False
    if now.weekday() >= 5 or not time(9, 35) <= now.time() < time(12, 30):
        return delivered
    try:
        scheduled = calendar(root, now=now, query=query)
    except Exception:
        notify(root, "calendar_unavailable", "Cannot verify today's market calendar; session monitoring is unverified.", now=now, sender=sender)
        return False
    if not scheduled:
        return delivered
    issue = session_problem(read_json(root / "logs/live_status.json"), now)
    if issue:
        notify(root, "session_missing_or_stale", f"{now.date()}: {issue}. Check preflight and automation logs.", now=now, sender=sender)
        return False
    return delivered
