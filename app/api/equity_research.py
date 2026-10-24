"""Read-only view of the free IEX signal-screening cohort."""
import os
from pathlib import Path
from datetime import datetime
from html import escape
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.operations.monitor import ET, read_json

router=APIRouter(tags=['free-equity-research'])


def review():
    root=Path(os.getenv('TRADER_EVIDENCE_DIR','/root/trader-evidence'))/'equity_research/reports'
    today=root/f'{datetime.now(ET).date()}.json'
    paths=sorted(root.glob('*.json'))
    path=today if today.exists() else paths[-1] if paths else None
    return read_json(path) if path else {'mode':'equity_research','status':'awaiting_first_session',
        'broker_entries_enabled':False,'options_pnl':None,'paid_subscription_required':False}


@router.get('/api/equity-research')
def research_json():
    return review()


@router.get('/equity-research',response_class=HTMLResponse)
def research_page():
    report=review()
    def fmt(value):return 'Unavailable' if value is None else escape(str(round(value,3) if isinstance(value,float) else value))
    rows=''.join('<tr>'+''.join(f'<td>{fmt(row.get(k))}</td>' for k in
        ('strategy','horizon_minutes','observations','mean_gross_bps','median_gross_bps'))+'</tr>' for row in report.get('summary',[]))
    return '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Free stock-signal research</title><style>body{font:16px system-ui;background:#101722;color:#eef3f8;max-width:1000px;margin:32px auto;padding:20px}h1{font-size:28px}p{line-height:1.6;color:#c3cdd9}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:12px;text-align:left;border-bottom:1px solid #334155}.card{padding:20px;border:1px solid #334155;border-radius:12px;margin:20px 0;overflow:auto}a{color:#8dc5ff}</style></head><body>
<h1>Free stock-signal research</h1><p>Alpaca Basic · IEX equities · Order placement disabled · Options results unavailable</p>
<div class="card">Date: '''+fmt(report.get('date'))+'<br>Collection: '+fmt(report.get('research_status',report.get('status')))+'<br>Data: '+fmt(report.get('data_status'))+'<br>Fresh-symbol coverage: '+fmt(report.get('coverage_fraction'))+'<br>Last data received: '+fmt(report.get('last_data_at'))+'''</div>
<div class="card"><table><thead><tr><th>Strategy</th><th>Minutes</th><th>Observations</th><th>Mean gross bps</th><th>Median gross bps</th></tr></thead><tbody>'''+rows+'''</tbody></table></div>
<p>These are prospective underlying-price diagnostics using a future bar-open proxy. IEX volume covers one exchange. Overlapping signals are not independent trades. Fills, option returns, and profitability have not been established. Missing observations remain unavailable.</p><a href="/api/equity-research">Full data and cost sensitivity</a></body></html>'''
