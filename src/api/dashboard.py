"""Dashboard state assembly (pure function) + minimal HTML rendering.

``build_dashboard_state`` returns a JSON-able dict with every section the spec requires so the
same data drives the HTML page, a JSON API, and tests. It performs no I/O itself — callers pass
already-fetched broker/account/reconciliation data — which keeps it deterministic and testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.broker.alpaca_paper import PAPER_HOST
from src.data.calendar import session_state
from src.risk.portfolio import OpenRisk, total_committed_risk, total_exposure


@dataclass
class PositionView:
    symbol: str
    qty: int
    entry_price: float
    stop_price: float | None
    r_multiple: float | None
    exit_state: str


@dataclass
class DashboardInputs:
    now: datetime
    config_version: str
    mode: str
    paper_endpoint: str
    broker_connected: bool
    data_connected: bool
    account: dict                      # equity/cash/buying_power/drawdown/realized_pnl
    positions: list[PositionView] = field(default_factory=list)
    open_orders: list[dict] = field(default_factory=list)
    latest_import: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    setup_scores: list[dict] = field(default_factory=list)
    recon_incidents: list[dict] = field(default_factory=list)
    reports: dict = field(default_factory=dict)


def _paper_verified(endpoint: str) -> bool:
    from urllib.parse import urlparse
    return urlparse(endpoint).hostname == PAPER_HOST and urlparse(endpoint).scheme == "https"


def _batch_freshness(latest_import: dict | None, now: datetime, max_age_hours: float) -> dict:
    if not latest_import:
        return {"present": False, "fresh": False, "reason": "no import"}
    try:
        received = datetime.fromisoformat(latest_import["received_at"])
    except (KeyError, ValueError):
        return {"present": True, "fresh": False, "reason": "bad timestamp"}
    age_h = (now - received).total_seconds() / 3600.0
    return {
        "present": True,
        "market_date": latest_import.get("market_date"),
        "status": latest_import.get("status"),
        "age_hours": round(age_h, 2),
        "fresh": age_h <= max_age_hours and latest_import.get("status") == "ACCEPTED",
    }


def build_dashboard_state(inp: DashboardInputs, *, max_import_age_hours: float = 20.0) -> dict[str, Any]:
    paper_ok = _paper_verified(inp.paper_endpoint)

    open_risk = [
        OpenRisk(p.symbol, p.qty, p.entry_price, p.stop_price)
        for p in inp.positions if p.stop_price is not None
    ]
    committed_risk = total_committed_risk(open_risk)
    exposure = total_exposure([OpenRisk(p.symbol, p.qty, p.entry_price, p.stop_price or p.entry_price)
                               for p in inp.positions])

    positions_missing_stop = [p.symbol for p in inp.positions if p.qty > 0 and p.stop_price is None]

    return {
        "health": {
            "service_ok": True,
            "broker_connected": inp.broker_connected,
            "data_connected": inp.data_connected,
            "paper_endpoint_verified": paper_ok,
            "paper_endpoint": inp.paper_endpoint if paper_ok else "***rejected-non-paper***",
            "config_version": inp.config_version,
            "mode": inp.mode,
        },
        "market_session": session_state(inp.now).value,
        "tc2000_batch": _batch_freshness(inp.latest_import, inp.now, max_import_age_hours),
        "candidates": inp.candidates,
        "setups": inp.setup_scores,
        "positions": [
            {"symbol": p.symbol, "qty": p.qty, "entry": p.entry_price, "stop": p.stop_price,
             "r_multiple": p.r_multiple, "exit_state": p.exit_state}
            for p in inp.positions
        ],
        "open_orders": inp.open_orders,
        "risk": {
            "equity": inp.account.get("equity"),
            "buying_power": inp.account.get("buying_power"),
            "committed_risk": round(committed_risk, 2),
            "exposure": round(exposure, 2),
            "realized_pnl": inp.account.get("realized_pnl"),
            "unrealized_pnl": inp.account.get("unrealized_pnl"),
            "drawdown": inp.account.get("drawdown"),
        },
        "reconciliation": {
            "ok": len(inp.recon_incidents) == 0 and not positions_missing_stop,
            "incidents": inp.recon_incidents,
            "positions_missing_stop": positions_missing_stop,
            "blocks_new_risk": bool(inp.recon_incidents) or bool(positions_missing_stop),
        },
        "reports": inp.reports,
        "generated_at": inp.now.isoformat(),
    }


def render_html(state: dict) -> str:
    """Tiny, dependency-free HTML view (server-rendered; escapes nothing external)."""
    h = state["health"]
    r = state["risk"]
    rec = state["reconciliation"]
    rows = "".join(
        f"<tr><td>{p['symbol']}</td><td>{p['qty']}</td><td>{p['entry']}</td>"
        f"<td>{p['stop']}</td><td>{p['r_multiple']}</td><td>{p['exit_state']}</td></tr>"
        for p in state["positions"]
    )
    banner = "PAPER VERIFIED" if h["paper_endpoint_verified"] else "NON-PAPER ENDPOINT — BLOCKED"
    return f"""<!doctype html><meta charset=utf-8><title>Swing Dashboard</title>
<h1>Swing Dashboard <small>({h['mode']}, cfg {h['config_version']})</small></h1>
<p><b>{banner}</b> · session={state['market_session']} ·
broker={'up' if h['broker_connected'] else 'DOWN'} ·
data={'up' if h['data_connected'] else 'DOWN'}</p>
<p>Equity={r['equity']} · committed risk={r['committed_risk']} · exposure={r['exposure']} ·
realized P&amp;L={r['realized_pnl']} · drawdown={r['drawdown']}</p>
<p>Reconciliation: {'OK' if rec['ok'] else 'BLOCKED — ' + ', '.join(rec['positions_missing_stop'])}</p>
<table border=1><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Stop</th><th>R</th><th>Exit</th></tr>
{rows}</table>
<p>TC2000 batch: {state['tc2000_batch']}</p>"""
