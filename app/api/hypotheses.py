"""Read-only hypotheses pages served through the existing private dashboard."""
from html import escape
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.hypotheses.registry import IDENTIFIER, REGISTRY, load_registry

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(tags=["research-hypotheses"])


@router.get("/api/hypotheses")
def hypotheses_json():
    registry = load_registry(ROOT)
    entries = []
    for key, item in registry["hypotheses"].items():
        report_path = ROOT / REGISTRY.parent / key / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        entries.append({"id": key, **item, "primary": report.get("primary")})
    return {"broker_entries_enabled": False, "hypotheses": entries}


@router.get("/api/hypotheses/{hypothesis_id}")
def hypothesis_json(hypothesis_id: str):
    if not IDENTIFIER.fullmatch(hypothesis_id):
        raise HTTPException(status_code=404, detail="Unknown hypothesis")
    items = load_registry(ROOT)["hypotheses"]
    if hypothesis_id not in items:
        raise HTTPException(status_code=404, detail="Unknown hypothesis")
    folder = ROOT / REGISTRY.parent / hypothesis_id
    result = {"id": hypothesis_id, **items[hypothesis_id], "broker_entries_enabled": False}
    for name in ("report", "archive-replay"):
        p = folder / f"{name}.json"
        result[name] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    return result


@router.get("/hypotheses", response_class=HTMLResponse)
def hypotheses_page():
    def cell(value):
        return "<td>" + escape("Unavailable" if value is None else str(value)) + "</td>"

    rendered = []
    for item in hypotheses_json()["hypotheses"]:
        primary = item.get("primary") or {}
        identifier = escape(item["id"])
        rendered.append(f'<tr><td><a href="/api/hypotheses/{identifier}">{identifier}</a></td>' +
                        "".join(cell(v) for v in (item["name"], item["status"],
                               primary.get("candidate_days"), primary.get("mean_bps"), primary.get("t"),
                               item.get("reason"))) + "</tr>")
    return """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Strategy research</title>
<style>body{font:16px system-ui;max-width:72rem;margin:2rem auto;padding:0 1rem;color:#162133}
section{overflow:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.65rem;
border-bottom:1px solid #ccd5df;vertical-align:top}thead{background:#eef4fa}</style>
<h1>Strategy research</h1><p>Prices and volatility proxies. Broker entries disabled.</p>
<p><a href="/equity-research/review">Frozen equity review</a></p>
<section><table><thead><tr><th scope="col">ID</th><th scope="col">Hypothesis</th>
<th scope="col">Status</th><th scope="col">Independent days or months</th>
<th scope="col">Incremental bps</th><th scope="col">t-statistic</th><th scope="col">Reason</th>
</tr></thead><tbody>""" + "".join(rendered) + "</tbody></table></section></html>"
