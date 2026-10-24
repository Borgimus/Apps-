"""Free observations use IEX GETs, causal bars and no order/option endpoints."""
import asyncio
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import importlib.util
import json
import os
import subprocess
import sys

import httpx
import pytest

from app.research.equity_observer import EquityData,Observer,ET,data_canary,outcomes,next_entry_bar
from app.strategies.strategy_base import Signal,SignalDirection

DAY=datetime(2026,10,26,10,0,30,tzinfo=ET)


def bar(at,close=100,open=100):
    return {'t':at.isoformat(),'open':open,'high':max(close,open)+1,'low':min(close,open)-1,'close':close,'volume':1000}


def raw(at):
    return {'t':at.isoformat(),'o':100,'h':102,'l':99,'c':101,'v':12}


@pytest.mark.asyncio
async def test_data_client_only_uses_free_iex_gets_and_paginates():
    requests=[]
    def route(request):
        requests.append(request)
        assert request.method=='GET' and request.url.path=='/v2/stocks/bars'
        assert request.url.params['feed']=='iex'
        if 'page_token' not in request.url.params:
            return httpx.Response(200,json={'bars':{'SPY':[raw(DAY.replace(minute=45,hour=9,second=0))]},'next_page_token':'next'})
        return httpx.Response(200,json={'bars':{'AAPL':[raw(DAY.replace(minute=50,hour=9,second=0))]}})
    data=EquityData('PKTEST','private',transport=httpx.MockTransport(route))
    try:
        rows=await data.bars(['SPY','AAPL'],DAY-timedelta(hours=1),DAY)
        assert len(requests)==2 and len(rows['SPY'])==len(rows['AAPL'])==1
        assert rows['SPY'][0]['volume']==12
    finally:await data.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['403','schema','repeat','nan','future','wrong_grid'])
async def test_bad_data_never_becomes_a_healthy_empty_result(kind):
    def route(r):
        row=raw(DAY.replace(minute=55,hour=9,second=0))
        if kind=='403':return httpx.Response(403,json={})
        if kind=='schema':return httpx.Response(200,json={})
        if kind=='repeat':return httpx.Response(200,json={'bars':{},'next_page_token':'same'})
        if kind=='nan':row['c']='NaN'
        if kind=='future':row['t']=(DAY+timedelta(hours=1)).replace(second=0).isoformat()
        if kind=='wrong_grid':row['t']=DAY.replace(minute=53,hour=9,second=0).isoformat()
        return httpx.Response(200,json={'bars':{'SPY':[row]}})
    data=EquityData('PKTEST','private',transport=httpx.MockTransport(route))
    try:
        with pytest.raises((httpx.HTTPStatusError,ValueError)):
            await data.bars(['SPY'],DAY-timedelta(hours=1),DAY)
    finally:await data.close()


@pytest.mark.asyncio
async def test_forming_bar_is_excluded_and_market_hours_canary_requires_freshness():
    recent=DAY.replace(minute=55,hour=9,second=0)
    data=SimpleNamespace(bars=AsyncMock(return_value={'SPY':[bar(recent)]}))
    assert (await data_canary(data,now=DAY))['freshness']=='verified'
    data.bars.return_value={'SPY':[bar(recent-timedelta(days=1))]}
    with pytest.raises(ValueError,match='stale'):await data_canary(data,now=DAY)
    weekend=DAY-timedelta(days=2)
    assert (await data_canary(data,now=weekend))['freshness']=='deferred_until_session'
    client=EquityData('PKTEST','private',transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'bars':{'SPY':[raw(DAY.replace(second=0))]}})))
    try:assert (await client.bars(['SPY'],DAY-timedelta(hours=1),DAY))['SPY']==[]
    finally:await client.close()


def test_outcomes_wait_for_future_entry_and_complete_horizon_and_missing_bars_stay_null():
    entry=next_entry_bar(DAY)
    assert entry==DAY.replace(minute=5,second=0)
    signal={'entry_bar':entry.isoformat(),'direction':'long'}
    bars=[bar(entry,101),bar(entry+timedelta(minutes=10),103)]
    assert outcomes(signal,bars,now=DAY)==dict.fromkeys(['5','15','30'])
    result=outcomes(signal,bars,now=DAY+timedelta(minutes=20))
    assert result['5']==pytest.approx(100) and result['15'] is None and result['30'] is None
    assert outcomes(dict(signal,direction='short'),bars,now=DAY+timedelta(minutes=20))['5']==-100


def observer(tmp_path):
    o=Observer(tmp_path,tmp_path/'evidence',['SPY'],str(DAY.date()),code_sha='abc')
    signal=Signal('orb','SPY',SignalDirection.LONG,DAY.replace(hour=9,minute=55,second=0),100)
    o.strategies=[SimpleNamespace(strategy_id='orb',generate_signals=lambda table,symbol:[signal])]
    historical=[bar((DAY-timedelta(days=3)).replace(hour=9,minute=30,second=0)+timedelta(minutes=5*i)) for i in range(17)]
    current=[bar(DAY.replace(hour=9,minute=30,second=0)+timedelta(minutes=5*i)) for i in range(6)]
    return o,historical+current


def test_prospective_signals_are_deduplicated_and_provider_revisions_do_not_rewrite_decisions(tmp_path):
    o,bars=observer(tmp_path)
    first=o.cycle({'SPY':bars},DAY)
    assert len(first['signals'])==1
    signal=first['signals'][0]
    assert signal['prospective'] and signal['gross_forward_bps']['5'] is None
    entry=next_entry_bar(DAY)
    second=o.cycle({'SPY':[bar(entry,101)]},DAY+timedelta(minutes=10))
    assert len(second['signals'])==1 and second['signals'][0]['gross_forward_bps']['5']==100
    third=o.cycle({'SPY':[bar(entry,150)]},DAY+timedelta(minutes=11))
    assert third['signals'][0]['gross_forward_bps']['5']==100
    assert third['options_pnl'] is None and third['broker_realized_pnl'] is None
    assert not third['broker_entries_enabled'] and not third['counts_toward_options_readiness']
    restored=Observer(tmp_path,tmp_path/'evidence',['SPY'],str(DAY.date()),code_sha='abc')
    assert len(restored.state['signals'])==1
    events=[json.loads(line) for line in o.events.read_text().splitlines()]
    assert sum(e['event']=='signal' for e in events)==1


def test_old_signal_discovered_after_startup_is_excluded_and_outage_is_visible(tmp_path):
    o,bars=observer(tmp_path)
    report=o.cycle({'SPY':bars},DAY+timedelta(minutes=20))
    assert not report['signals'][0]['prospective']
    assert all(r['observations']==0 and r['mean_gross_bps'] is None for r in report['summary'])
    failure=o.failure(DAY+timedelta(minutes=21),RuntimeError('data unavailable'))
    assert failure['data_status']=='degraded' and failure['request_failures']==1
    assert o.report(DAY+timedelta(hours=3),finished=True)['data_status']=='degraded'


def test_zero_cost_settings_preserve_risk_controls_and_do_not_require_opra():
    spec=importlib.util.spec_from_file_location('free_recover',Path(__file__).resolve().parents[1]/'scripts/ops/recover.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    config=module.settings_env({'ALPACA_API_KEY':'PKTEST'},mode='equity_research')
    assert config['TRADER_RUN_MODE']=='equity_research'
    assert config['LIVE_TRADING_ENABLED']=='false' and config['ALPACA_OPTIONS_FEED']=='indicative'
    assert config['TRADIER_MARKET_DATA_MODE']=='off'


def test_free_dashboard_preserves_unavailable_options_results(tmp_path,monkeypatch):
    from app.api import equity_research as api
    monkeypatch.setenv('TRADER_EVIDENCE_DIR',str(tmp_path))
    result=api.research_json()
    assert result['options_pnl'] is None and not result['broker_entries_enabled']
    assert 'Options results unavailable' in api.research_page()


@pytest.mark.asyncio
async def test_free_dashboard_cannot_start_original_broker_runner(monkeypatch):
    from app.api.sessions_router import sessions_start,sessions_restart
    from fastapi import HTTPException
    monkeypatch.setenv('TRADER_RUN_MODE','equity_research')
    for operation in (sessions_start,sessions_restart):
        with pytest.raises(HTTPException) as exc:await operation()
        assert exc.value.status_code==409


@pytest.mark.parametrize('failure',[None,'check','preflight'])
def test_free_install_keeps_broker_disarmed_and_hands_off_locks_before_preflight(tmp_path,monkeypatch,failure):
    import fcntl
    spec=importlib.util.spec_from_file_location('free_install',Path(__file__).resolve().parents[1]/'scripts/ops/recover.py')
    r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
    base=tmp_path/'root';root=base/'trader-release';(root/'logs').mkdir(parents=True)
    (root/'KILL_SWITCH').touch();backup=base/'backup';backup.mkdir()
    names=['.eod_close.lock','.session.lock','.session-preflight.lock']
    held=[(base/name).open('a') for name in names]
    for h in held:fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB)
    original=r.BEGIN+'\n# Paused until recovery checks pass\n'+r.END+'\n'
    state={'cron':original};operations=[]
    def mapped(p):
        value=str(p)
        return base/value.removeprefix('/root').lstrip('/') if value=='/root' or value.startswith('/root/') else Path(p)
    monkeypatch.setattr(r,'Path',mapped)
    monkeypatch.setattr(r,'restart_dashboard',lambda root,env:True)
    def fake(*args,check=True,cwd=None,env=None,input=None):
        if args[0]=='crontab':state['cron']=input
        else:
            assert 'scripts/equity_research.py' in args
            op=args[2];operations.append(op)
            if op==failure:raise RuntimeError('simulated failure')
            if op=='preflight':
                # A separate process can acquire all three launch locks here.
                probe='''import fcntl,sys
from pathlib import Path
for name in ('.eod_close.lock','.session.lock','.session-preflight.lock'):
 with (Path(sys.argv[1])/name).open('a') as h:fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB)
'''
                subprocess.run([sys.executable,'-c',probe,str(base)],check=True)
                (root/'logs/equity_research_ready.json').write_text(json.dumps({'code_sha':'b'*40,'orders_enabled':False,'date':str(datetime.now(ET).date())}))
        return subprocess.CompletedProcess(args,0,stdout='FREE_IEX_DATA_READY',stderr='')
    monkeypatch.setattr(r,'command',fake)
    try:
        if failure:
            with pytest.raises(RuntimeError):r.finish_equity_recovery(root,{},original,backup,held,'b'*40)
            assert state['cron']==original
        else:
            r.finish_equity_recovery(root,{},original,backup,held,'b'*40)
            assert operations==['check','preflight']
            assert 'research.sh watchdog' in state['cron']
            assert 'session_runner.py' not in state['cron']
            status=json.loads((base/'trader-recovery-status.json').read_text())
            assert status['new_subscription_cost']==0 and status['broker_trading_disarmed']
            assert status['options_pnl'] is None and not status['alert_server_accepted']
        assert (root/'KILL_SWITCH').exists() and not (base/'.session_armed').exists()
    finally:
        for h in held:h.close()


def test_free_research_evidence_is_included_in_immutable_archive(tmp_path):
    from app.operations.artifacts import archive,verify_archive
    root=tmp_path/'code';root.mkdir()
    subprocess.run(['git','init',str(root)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'config','user.name','Test'],check=True)
    subprocess.run(['git','-C',str(root),'config','user.email','test@example.test'],check=True)
    (root/'file').write_text('source')
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'commit','-m','source'],check=True,capture_output=True)
    evidence=tmp_path/'evidence';reports=evidence/'equity_research/reports';reports.mkdir(parents=True)
    (reports/'2026-10-26.json').write_text('{"mode":"equity_research","options_pnl":null}')
    (reports/'2026-10-26.md').write_text('Free IEX research')
    events=evidence/'equity_research/events';events.mkdir()
    (events/'2026-10-26.jsonl').write_text('{"event":"signal"}\n')
    snapshot=archive(root,evidence,'2026-10-26');verify_archive(snapshot)
    saved=json.loads((snapshot/'equity_research/2026-10-26.json').read_text())
    assert saved['options_pnl'] is None
    assert (snapshot/'equity_research/2026-10-26.events.jsonl').exists()


def test_free_watchdog_rejects_stale_heartbeat_and_unverified_provenance():
    spec=importlib.util.spec_from_file_location('free_cli',Path(__file__).resolve().parents[1]/'scripts/equity_research.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    report={'date':str(DAY.date()),'research_status':'running','last_cycle_at':DAY.isoformat(),
        'protocol':cli.PROTOCOL,'feed':'iex','broker_entries_enabled':False,'data_status':'ok'}
    assert cli.watchdog_problem(report,DAY) is None
    assert cli.watchdog_problem(dict(report,feed='unknown'),DAY)
    assert cli.watchdog_problem(dict(report,data_status=None),DAY)
    assert cli.watchdog_problem(report,DAY+timedelta(minutes=4))


@pytest.mark.asyncio
async def test_signal_discovery_timestamp_is_after_http_response(monkeypatch):
    spec=importlib.util.spec_from_file_location('free_collection',Path(__file__).resolve().parents[1]/'scripts/equity_research.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    received=DAY+timedelta(seconds=45)
    state={'response_received':False}
    async def bars(symbols,start,end):
        assert end==DAY
        state['response_received']=True
        return {'SPY':[]}
    def now(tz):
        assert state['response_received']
        return received
    captured=[]
    observer=SimpleNamespace(cycle=lambda incoming,at:captured.append(at))
    monkeypatch.setattr(cli,'datetime',SimpleNamespace(now=now))
    await cli.collect_cycle(observer,SimpleNamespace(bars=bars),['SPY'],DAY-timedelta(hours=1),DAY)
    assert captured==[received]


def test_restart_with_different_source_cannot_mix_same_day_evidence(tmp_path):
    o,bars=observer(tmp_path);o.cycle({'SPY':bars},DAY)
    with pytest.raises(ValueError,match='Incompatible'):
        Observer(tmp_path,tmp_path/'evidence',['SPY'],str(DAY.date()),code_sha='different')
