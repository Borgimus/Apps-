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
<p><a href="/equity-research/review">Cumulative review, portfolio proxies and session checkpoints</a></p>
<div class="card">Date: '''+fmt(report.get('date'))+'<br>Collection: '+fmt(report.get('research_status',report.get('status')))+'<br>Data: '+fmt(report.get('data_status'))+'<br>Fresh-symbol coverage: '+fmt(report.get('coverage_fraction'))+'<br>Last data received: '+fmt(report.get('last_data_at'))+'''</div>
<div class="card"><table><thead><tr><th>Strategy</th><th>Minutes</th><th>Observations</th><th>Mean gross bps</th><th>Median gross bps</th></tr></thead><tbody>'''+rows+'''</tbody></table></div>
<p>These are prospective underlying-price diagnostics using a future bar-open proxy. IEX volume covers one exchange. Overlapping signals are not independent trades. Fills, option returns, and profitability have not been established. Missing observations remain unavailable.</p><a href="/api/equity-research">Full data and cost sensitivity</a></body></html>'''


def cumulative_review_data():
    path=Path(os.getenv('TRADER_EVIDENCE_DIR','/root/trader-evidence'))/'equity_research/review/latest.json'
    result=read_json(path)
    return result if result else {'status':'awaiting_cumulative_review','broker_entries_enabled':False,
        'options_pnl':None,'broker_realized_pnl':None}


@router.get('/api/equity-research/review')
def cumulative_review_json():
    return cumulative_review_data()


@router.get('/equity-research/review',response_class=HTMLResponse)
def cumulative_review_page():
    report=cumulative_review_data()
    def fmt(value):
        return 'Unavailable' if value is None else escape(str(round(value,4) if isinstance(value,float) else value))
    def table(headings,rows):
        return '<div class="card"><table><thead><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headings)+\
            '</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+fmt(v)+'</td>' for v in row)+'</tr>' for row in rows)+\
            '</tbody></table></div>'
    stages={'collecting_to_10':'Collecting toward 10 full sessions','collecting_to_20':'Collecting toward 20 full sessions',
        'development_screen_failed':'Development screen did not pass','collecting_frozen_holdout':'Collecting the separate holdout',
        'holdout_screen_failed':'Holdout screen did not pass','candidate_for_manual_paper_review':'Candidate for a manual paper-trading review',
        'evidence_review_required':'Evidence needs review'}
    body='<h1>Cumulative research review</h1><p>Order placement disabled · Actual broker and options P&amp;L unavailable</p>'
    body+='<p>Completed-session data through '+fmt(report.get('as_of_session'))+'. Latest input timestamp: '+fmt(report.get('generated_at'))+'.</p>'
    body+='<div class="card">'+fmt(stages.get(report.get('stage'),report.get('status')))+\
        '<br>Full valid sessions: '+fmt(report.get('full_valid_sessions'))+\
        '<br>Partial sessions: '+fmt(report.get('partial_sessions'))+\
        '<br>Invalid sessions: '+fmt(report.get('invalid_sessions'))+\
        '<br>Eligible signals in full sessions: '+fmt(report.get('eligible_signals_full_sessions'))+'</div>'
    body+=table(['Full-session checkpoint','Reached'],[(r['full_sessions'],r['reached']) for r in report.get('checkpoints',[])])
    body+='<h2>Constrained portfolio proxies</h2><p>One long position, up to three entries per day, whole shares, $1,000 position budget, fixed 30-minute exit. These use completed-bar prices and assumed costs. An unresolved selected outcome blocks further replay exposure.</p>'
    body+=table(['Assumed round-trip cost (bps)','Closed proxies','Unresolved','Proxy net ($)','Positive days','Net excluding best day ($)'],
        [(r['cost_bps'],r['closed_proxies'],r['unresolved'],r['proxy_net_pnl_dollars'],r['positive_days'],r['net_without_best_day_dollars'])
         for r in report.get('portfolio_scenarios',{}).values()])
    body+='<h2>All-signal diagnostics</h2><p>Signals and horizons overlap. These averages describe stock-price movement in the predicted direction.</p>'
    body+=table(['Strategy','Minutes','Outcomes','Mean gross bps','After assumed 10 bps'],
        [(r['strategy'],r['horizon_minutes'],r['observations'],r['mean_gross_bps'],r['cost_sensitivity_bps']['10'])
         for r in report.get('signal_diagnostics',[])])
    body+='<h2>Development and holdout</h2><p>The first 20 full sessions form development. The next 10 form a disjoint holdout with the same frozen rules. Reaching a checkpoint never enables orders.</p>'
    body+=table(['Screen','Condition','Passed'],[(label,k.replace('_',' '),v)
        for label,key in [('Development','development_screen'),('Holdout','holdout_screen')]
        for k,v in report.get(key,{}).get('checks',{}).items()])
    body+='<h2>Session audit</h2>'
    body+=table(['Date','Classification','Eligible signals','Review notes'],
        [(r['date'],r['category'],r.get('eligible_signals'),', '.join(r.get('reasons',[]))) for r in report.get('sessions',[])])
    body+='<h2>Limits</h2><ul>'+''.join('<li>'+escape(t)+'</li>' for t in report.get('limitations',[]))+'</ul>'
    body+='<p><a href="/equity-research">Latest daily report</a> · <a href="/api/equity-research/review">Full review JSON, costs, input hashes and selected proxies</a></p>'
    return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cumulative research review</title><style>body{font:16px system-ui;background:#101722;color:#eef3f8;max-width:1100px;margin:24px auto;padding:20px}p,li{line-height:1.6;color:#c3cdd9}.card{padding:20px;border:1px solid #334155;border-radius:12px;margin:20px 0;overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:10px;text-align:left;border-bottom:1px solid #334155}a{color:#8dc5ff}</style></head><body>'+body+'</body></html>'
