"""Read-only, accessible end-of-day broker and shadow review."""
from __future__ import annotations

import json
import re
from datetime import date as today
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DBTradeJournal, get_db

router = APIRouter(tags=["end-of-day-review"])
ROOT = Path(__file__).resolve().parents[2]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_date(value: str) -> str:
    if not DATE_RE.fullmatch(value or ""):
        raise HTTPException(400, "Date must use YYYY-MM-DD format")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(400, "Invalid calendar date") from exc
    return value


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def report_dates(root: Optional[Path] = None) -> list[str]:
    base = root or ROOT
    dates = {
        path.stem
        for path in (base / "evaluation" / "reports").glob("*.json")
        if DATE_RE.fullmatch(path.stem)
    }
    dates.update(
        path.stem.removeprefix("session_")
        for path in (base / "logs").glob("session_*.json")
        if DATE_RE.fullmatch(path.stem.removeprefix("session_"))
    )
    return sorted(dates, reverse=True)


def phase3_record(day: str, root: Optional[Path] = None) -> dict[str, Any]:
    base = root or ROOT
    records = read_json(base / "evaluation" / "phase3_tracking.json").get("phase3_sessions", [])
    return next(
        (row for row in records if isinstance(row, dict) and row.get("date") == day),
        {},
    ) if isinstance(records, list) else {}


def shadow_trades(day: str, root: Optional[Path] = None) -> list[dict[str, Any]]:
    base = root or ROOT
    try:
        lines = (base / "evaluation" / "shadow_book.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if row.get("event") == "shadow_close" and str(row.get("ts", "")).startswith(day):
            result.append(row)
    return sorted(result, key=lambda row: str(row.get("ts", "")))


async def broker_trades(db: AsyncSession, day: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(DBTradeJournal)
            .where(DBTradeJournal.session_date == day)
            .where(DBTradeJournal.status == "closed")
            .order_by(DBTradeJournal.entry_time.asc())
        )
    ).scalars().all()
    return [{
        "symbol": row.underlying_symbol,
        "contract": row.option_symbol,
        "strategy": row.strategy_id,
        "direction": row.signal_direction,
        "entry_time": row.entry_time.isoformat() if row.entry_time else None,
        "entry_price": row.fill_price if row.fill_price is not None else row.limit_price,
        "exit_time": row.exit_time.isoformat() if row.exit_time else None,
        "exit_price": row.exit_price,
        "hold_seconds": row.hold_duration_secs,
        "pnl": row.realized_pnl,
        "reason": row.exit_reason,
    } for row in rows]


def file_review(day: str, root: Optional[Path] = None) -> dict[str, Any]:
    base = root or ROOT
    report = read_json(base / "evaluation" / "reports" / f"{day}.json")
    session = read_json(base / "logs" / f"session_{day}.json")
    phase3 = phase3_record(day, base)
    shadow = shadow_trades(day, base)
    return {
        "date": day,
        "report_available": bool(report or session),
        "status": phase3.get("status") or report.get("evidence_type") or "unclassified",
        "counts_toward_phase3": phase3.get("counts_toward_phase3"),
        "realized_pnl": report.get("realized_pnl", session.get("realized_pnl")),
        "trades_filled": report.get("trades_filled", (session.get("trades") or {}).get("total_closed")),
        "win_rate": report.get("win_rate", (session.get("trades") or {}).get("win_rate")),
        "fill_rate": report.get("fill_rate"),
        "api_errors": report.get("api_errors", session.get("api_errors")),
        "session_start": report.get("session_start"),
        "session_end": report.get("session_end"),
        "sample_warning": report.get("sample_size_warning"),
        "notes": report.get("notes") or [],
        "recommendations": report.get("recommendations") or [],
        "by_strategy": report.get("by_strategy") or [],
        "contamination_flags": phase3.get("contamination_flags") or [],
        "contamination_note": phase3.get("contamination_note"),
        "shadow_trades": shadow,
        "shadow_pnl": round(sum(float(row.get("shadow_pnl") or 0) for row in shadow), 2),
        "broker_trades": [],
    }


def money(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if amount < 0 else "+" if amount > 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def duration(value: Any) -> str:
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "—"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds}s"


def clock(value: Any) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return escape(str(value))


def cell(value: Any) -> str:
    return escape("—" if value in (None, "") else str(value))


def tone(value: Any) -> str:
    try:
        return "gain" if float(value) > 0 else "loss" if float(value) < 0 else ""
    except (TypeError, ValueError):
        return ""


def render(review: dict[str, Any], dates: list[str]) -> str:
    day = review["date"]
    broker_rows = "".join(
        f"<tr><td>{cell(t['symbol'])}</td><td><code>{cell(t['contract'])}</code></td>"
        f"<td>{cell(t['strategy'])}</td><td>{cell(t['direction'])}</td>"
        f"<td>{clock(t['entry_time'])}<br>{money(t['entry_price'])}</td>"
        f"<td>{clock(t['exit_time'])}<br>{money(t['exit_price'])}</td>"
        f"<td>{duration(t['hold_seconds'])}</td><td class='{tone(t['pnl'])}'>{money(t['pnl'])}</td>"
        f"<td>{cell(t['reason'])}</td></tr>" for t in review["broker_trades"]
    ) or '<tr><td colspan="9">No closed broker trades found.</td></tr>'
    shadow_rows = "".join(
        f"<tr><td>{cell(t.get('symbol'))}</td><td><code>{cell(t.get('option_symbol'))}</code></td>"
        f"<td>{cell(t.get('strategy_id'))}</td><td>{cell(t.get('block_reason'))}</td>"
        f"<td>{money(t.get('entry_price'))}</td><td>{money(t.get('exit_price'))}</td>"
        f"<td>{duration(t.get('hold_seconds'))}</td><td class='{tone(t.get('shadow_pnl'))}'>{money(t.get('shadow_pnl'))}</td>"
        f"<td>{cell(t.get('category'))}</td><td>{cell(t.get('exit_reason'))}</td></tr>"
        for t in review["shadow_trades"]
    ) or '<tr><td colspan="10">No closed shadow trades found.</td></tr>'
    strategy_rows = "".join(
        f"<tr><td>{cell(t.get('strategy_id'))}</td><td>{cell(t.get('fills'))}</td>"
        f"<td>{percent(t.get('win_rate'))}</td><td class='{tone(t.get('realized_pnl'))}'>{money(t.get('realized_pnl'))}</td></tr>"
        for t in review["by_strategy"]
    ) or '<tr><td colspan="4">No strategy breakdown available.</td></tr>'
    options = "".join(
        f'<option value="{cell(d)}"{" selected" if d == day else ""}>{cell(d)}</option>' for d in dates
    ) or f'<option value="{cell(day)}">{cell(day)}</option>'
    notes = "".join(f"<li>{cell(item)}</li>" for item in review["notes"]) or "<li>None recorded.</li>"
    recs = "".join(f"<li>{cell(item)}</li>" for item in review["recommendations"]) or "<li>None recorded.</li>"
    flags = "".join(f"<li>{cell(item)}</li>" for item in review["contamination_flags"])
    cohort = review["counts_toward_phase3"]
    cohort_text = "Counts toward Phase 3" if cohort is True else "Does not count toward Phase 3" if cohort is False else "Phase 3 classification not recorded"
    notices = ""
    if review["sample_warning"]:
        notices += f'<div class="notice"><strong>Sample warning:</strong> {cell(review["sample_warning"])}</div>'
    if review["contamination_note"]:
        notices += f'<div class="notice"><strong>Data note:</strong> {cell(review["contamination_note"])}</div>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>End-of-Day Review — {cell(day)}</title><style>
:root{{color-scheme:light dark;--bg:#0b1020;--panel:#151c31;--text:#f5f7ff;--muted:#aab4cc;--line:#33405f;--link:#8bb8ff;--gain:#74e0a7;--loss:#ff9292}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,sans-serif}}a{{color:var(--link)}}header,main,footer{{width:min(1180px,calc(100% - 2rem));margin:auto}}header{{padding:1rem 0;display:flex;justify-content:space-between;gap:1rem;align-items:end;flex-wrap:wrap}}h1{{margin:0}}.controls{{display:flex;gap:.5rem;align-items:end;flex-wrap:wrap}}label{{display:block;font-weight:700}}select,button,.button{{font:inherit;min-height:44px;padding:.5rem .75rem;border:1px solid var(--line);border-radius:.5rem;background:var(--panel);color:var(--text)}}.button{{text-decoration:none;display:inline-flex;align-items:center}}:focus-visible{{outline:3px solid var(--link);outline-offset:2px}}.skip{{position:absolute;left:-9999px}}.skip:focus{{left:1rem;top:1rem;background:white;color:black;padding:.5rem;z-index:5}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.75rem}}.card,section{{background:var(--panel);border:1px solid var(--line);border-radius:.75rem;padding:1rem;margin:1rem 0}}.label,.muted{{color:var(--muted)}}.value{{font-size:1.45rem;font-weight:800}}.gain{{color:var(--gain);font-weight:800}}.loss{{color:var(--loss);font-weight:800}}.notice{{background:#fff1b8;color:#241b00;border-left:5px solid #d8a500;padding:.75rem;margin:1rem 0}}.table{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;min-width:760px}}caption{{text-align:left;font-weight:700;padding-bottom:.5rem}}th,td{{border-bottom:1px solid var(--line);padding:.6rem;text-align:left;vertical-align:top}}th{{color:var(--muted)}}code{{font-size:.85em;overflow-wrap:anywhere}}footer{{color:var(--muted);padding:1rem 0 2rem}}
@media print{{:root{{color-scheme:light}}body{{background:white;color:black}}.controls,.skip,footer{{display:none}}header,main{{width:100%}}.card,section{{background:white;border-color:#bbb;break-inside:avoid}}.gain,.loss{{color:black}}table{{min-width:0;font-size:11px}}}}
</style></head><body><a class="skip" href="#review">Skip to review</a><header><div><div class="muted">Options Trading Research System</div><h1>End-of-Day Review</h1></div><div class="controls" aria-label="Review controls"><form method="get" action="/review"><label for="date">Review date</label><select id="date" name="date" onchange="this.form.submit()">{options}</select><noscript><button>Load</button></noscript></form><button type="button" onclick="window.print()">Print / save PDF</button><a class="button" href="/api/reviews/{cell(day)}">View JSON</a></div></header>
<main id="review"><p><strong>{cell(day)}</strong> · {cell(review['status'])} · {cell(cohort_text)} · API errors: {cell(review['api_errors'])}</p>{notices}<div class="cards"><div class="card"><div class="label">Broker P&amp;L</div><div class="value {tone(review['realized_pnl'])}">{money(review['realized_pnl'])}</div></div><div class="card"><div class="label">Closed trades</div><div class="value">{cell(review['trades_filled'])}</div></div><div class="card"><div class="label">Win rate</div><div class="value">{percent(review['win_rate'])}</div></div><div class="card"><div class="label">Fill rate</div><div class="value">{percent(review['fill_rate'])}</div></div><div class="card"><div class="label">Shadow P&amp;L</div><div class="value {tone(review['shadow_pnl'])}">{money(review['shadow_pnl'])}</div></div><div class="card"><div class="label">Shadow closes</div><div class="value">{len(review['shadow_trades'])}</div></div></div>
<section><h2>Broker-executed trades</h2><p class="muted">Actual paper-broker fills. These are the primary trading results.</p><div class="table"><table><caption>Closed broker trades</caption><thead><tr><th>Symbol</th><th>Contract</th><th>Strategy</th><th>Direction</th><th>Entry</th><th>Exit</th><th>Hold</th><th>P&amp;L</th><th>Reason</th></tr></thead><tbody>{broker_rows}</tbody></table></div></section>
<section><h2>Shadow-book trades</h2><p class="muted">Counterfactual results for qualified trades blocked by capacity rules. They do not count toward account P&amp;L.</p><div class="table"><table><caption>Closed shadow trades</caption><thead><tr><th>Symbol</th><th>Contract</th><th>Strategy</th><th>Blocked by</th><th>Entry</th><th>Exit</th><th>Hold</th><th>P&amp;L</th><th>Category</th><th>Reason</th></tr></thead><tbody>{shadow_rows}</tbody></table></div></section>
<section><h2>Strategy breakdown</h2><div class="table"><table><caption>Broker results by strategy</caption><thead><tr><th>Strategy</th><th>Fills</th><th>Win rate</th><th>P&amp;L</th></tr></thead><tbody>{strategy_rows}</tbody></table></div></section>
<section><h2>Review notes</h2><h3>Observed</h3><ul>{notes}</ul><h3>Recommendations</h3><ul>{recs}</ul>{f'<h3>Contamination flags</h3><ul>{flags}</ul>' if flags else ''}</section></main><footer>Built from the trade journal, daily report, Phase 3 tracking, and shadow book.</footer></body></html>'''


@router.get("/api/reviews")
async def list_reviews():
    return {"dates": report_dates()}


@router.get("/api/reviews/{session_date}")
async def review_json(session_date: str, db: AsyncSession = Depends(get_db)):
    day = clean_date(session_date)
    review = file_review(day)
    review["broker_trades"] = await broker_trades(db, day)
    if not review["report_available"] and not review["broker_trades"] and not review["shadow_trades"]:
        raise HTTPException(404, f"No review data found for {day}")
    return review


@router.get("/review", response_class=HTMLResponse, include_in_schema=False)
async def review_page(date: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    dates = report_dates()
    day = clean_date(date) if date else (dates[0] if dates else str(today.today()))
    review = file_review(day)
    review["broker_trades"] = await broker_trades(db, day)
    return HTMLResponse(render(review, dates))
