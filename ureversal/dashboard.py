"""Performance dashboard — FastAPI over the trader's journal files.

Reads `status.json` / `events.jsonl` written by the live trader (file-based on
purpose: the trading loop never blocks on the UI). Also exposes the kill
switch and computed session metrics.

Run: python -m ureversal dashboard
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .config import Config, load_config


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="U-Reversal Dashboard")
    events_path = cfg.cache_dir / "events.jsonl"
    status_path = cfg.cache_dir / "status.json"
    kill_path = Path(cfg.risk["kill_switch_file"])

    def _events() -> list[dict]:
        if not events_path.exists():
            return []
        return [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/status")
    def status():
        if status_path.exists():
            return json.loads(status_path.read_text())
        return {"note": "trader not running (no status.json)"}

    @app.get("/events")
    def events(limit: int = 200):
        return _events()[-limit:]

    @app.get("/metrics")
    def metrics():
        evs = _events()
        exits = [e for e in evs if e["kind"] == "exit"]
        if not exits:
            return {"trades": 0}
        pnls = [e["net_pnl"] for e in exits]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        return {
            "trades": len(exits),
            "net_pnl": sum(pnls),
            "win_rate": len(wins) / len(exits),
            "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
            "avg_hold_s": sum(e["hold_s"] for e in exits) / len(exits),
            "exit_reasons": {r: sum(1 for e in exits if e["reason"] == r)
                             for r in {e["reason"] for e in exits}},
            "unfilled_entries": sum(1 for e in evs if e["kind"] == "entry_unfilled"),
            "suppressed_triggers": sum(1 for e in evs
                                       if e["kind"] == "trigger" and not e["allowed"]),
        }

    @app.post("/kill-switch/activate")
    def kill_on():
        kill_path.touch()
        return {"kill_switch": True}

    @app.delete("/kill-switch")
    def kill_off():
        kill_path.unlink(missing_ok=True)
        return {"kill_switch": False}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return """<!doctype html><meta charset="utf-8">
<title>U-Reversal</title>
<style>body{font-family:ui-monospace,monospace;margin:2rem;max-width:70rem}
pre{background:#f4f4f8;padding:1rem;border-radius:6px;overflow:auto}
button{margin-right:1rem;padding:.4rem .8rem}</style>
<h2>SPY/DIA U-Reversal</h2>
<div>
<button onclick="fetch('/kill-switch/activate',{method:'POST'}).then(r)">KILL SWITCH</button>
<button onclick="fetch('/kill-switch',{method:'DELETE'}).then(r)">clear kill switch</button>
</div>
<h3>Status</h3><pre id="s"></pre>
<h3>Metrics</h3><pre id="m"></pre>
<h3>Recent events</h3><pre id="e"></pre>
<script>
const r=()=>load();
async function load(){
 for(const [id,u] of [['s','/status'],['m','/metrics'],['e','/events?limit=30']]){
  document.getElementById(id).textContent=JSON.stringify(await (await fetch(u)).json(),null,1);
 }}
load();setInterval(load,2000);
</script>"""

    return app


def run_dashboard(cfg: Config | None = None) -> None:
    import uvicorn

    cfg = cfg or load_config()
    uvicorn.run(create_app(cfg), host=cfg.dashboard["host"], port=cfg.dashboard["port"])
