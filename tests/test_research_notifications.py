"""Research lifecycle alerts reach the delivery path without altering collection."""
import importlib.util
import json
from datetime import datetime
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.operations import monitor

NOW=datetime(2026,11,5,13,0,tzinfo=monitor.ET)


@pytest.fixture
def cli(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('research_notifications',
        Path(__file__).resolve().parents[1]/'scripts/equity_research.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root=tmp_path/'code';root.mkdir()
    monkeypatch.setattr(module,'ROOT',root)
    monkeypatch.setattr(module,'datetime',SimpleNamespace(now=lambda tz:NOW))
    monkeypatch.setattr(module,'configured',lambda:(tmp_path/'evidence',tmp_path/'locks'))
    monkeypatch.setattr(module,'verify_flat',AsyncMock())
    monkeypatch.setattr(module,'calendar',lambda *a,**k:True)
    return module


@pytest.fixture
def report():
    return {'date':str(NOW.date()),'protocol':monitor.read_json(
        Path(__file__).resolve().parents[1]/'evaluation/equity_research_baseline_2026_11_03.json')['protocol'],
        'feed':'iex','broker_entries_enabled':False,'session_complete':True,
        'termination':'scheduled_end','data_status':'outside_active_window',
        'cycles':164,'symbols':['SPY','QQQ'],'fresh_symbols':['SPY','QQQ'],
        'coverage_fraction':1.0,'request_failures':0,'signal_errors':0,
        'signals':[{'prospective':True},{'prospective':False}],
        'summary':[{'strategy':'orb','horizon_minutes':30,'observations':29,'mean_gross_bps':104.9765},
                   {'strategy':'vwap_reclaim','horizon_minutes':30,'observations':0,'mean_gross_bps':None}]}


@pytest.mark.parametrize('change',[{}, {'session_complete':False}, {'request_failures':1},
    {'signal_errors':1}, {'coverage_fraction':.5}, {'fresh_symbols':['QQQ']}])
def test_summary_is_truthful_and_daily_retries_do_not_duplicate(cli,report,monkeypatch,change):
    sent=[]
    monkeypatch.setattr(cli,'notify',partial(monitor.notify,now=NOW,sender=sent.append))
    report.update(change)
    assert cli.notify_session_finished(report)
    assert cli.notify_session_finished(report)
    assert len(sent)==1
    assert ('completed on schedule' in sent[0])==(not change)
    assert '1 eligible signals' in sent[0] and '+1.05% (29 observations)' in sent[0]
    assert 'not trading profit. Orders disabled.' in sent[0]


def test_completion_survives_review_and_publication_failures(cli,report,monkeypatch):
    sent=[]
    monkeypatch.setattr(cli,'notify',partial(monitor.notify,now=NOW,sender=sent.append))
    monkeypatch.setattr(cli,'current_report',lambda *a:report)
    def failed(*a):raise RuntimeError('unavailable')
    monkeypatch.setattr(cli,'build_review',failed)
    monkeypatch.setattr(cli,'publish',failed)
    monkeypatch.setattr('sys.argv',['equity_research.py','finish'])
    assert cli.main()==1
    assert cli.main()==1
    assert sum('completed on schedule' in s for s in sent)==1
    assert len(sent)==3 # summary, review failure, upload failure
    assert (cli.ROOT/'KILL_SWITCH').exists()


def test_equity_watchdog_retries_failed_delivery_after_hours(cli,report,monkeypatch):
    def failed(message):raise TimeoutError('SECRET_TOPIC')
    monkeypatch.setattr(cli,'notify',partial(monitor.notify,now=NOW,sender=failed))
    assert not cli.notify_session_finished(report)
    path=cli.ROOT/'logs/operations'/f'{NOW.date()}-equity_research_completed.json'
    assert 'SECRET_TOPIC' not in path.read_text()
    sent=[]
    monkeypatch.setattr(cli,'retry_notifications',partial(monitor.retry_notifications,sender=sent.append))
    monkeypatch.setattr('sys.argv',['equity_research.py','watchdog'])
    assert cli.main()==0
    assert cli.main()==0
    assert len(sent)==1
    assert json.loads(path.read_text())['attempts']==2


@pytest.mark.asyncio
async def test_start_alert_follows_successful_collection_and_deduplicates_restarts(cli,report,monkeypatch,tmp_path):
    now=NOW.replace(hour=10)
    monkeypatch.setattr(cli,'datetime',SimpleNamespace(now=lambda tz:now))
    monkeypatch.setattr(cli,'universe',lambda root:['SPY','QQQ'])
    monkeypatch.setattr(cli,'frozen_baseline',lambda root:{})
    monkeypatch.setattr(cli,'baseline_identity',lambda baseline:{})
    monkeypatch.setattr(cli,'identity',lambda:'test-source')
    monkeypatch.setattr(cli.signal,'signal',lambda *a:None)
    data=SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(cli,'client',lambda:data)
    observer=SimpleNamespace(state={'bars':{}},report=lambda *a,**k:dict(report))
    monkeypatch.setattr(cli,'Observer',lambda *a,**k:observer)
    sent=[]
    def send(message):
        assert collected.await_count>0
        sent.append(message)
    monkeypatch.setattr(cli,'notify',partial(monitor.notify,now=now,sender=send))
    collected=AsyncMock(return_value=report)
    monkeypatch.setattr(cli,'collect_cycle',collected)
    for _ in range(2):assert await cli.run_loop(tmp_path/'evidence',once=True)==0
    assert len(sent)==1 and 'research started' in sent[0]
    assert data.close.await_count==2 and (cli.ROOT/'KILL_SWITCH').exists()


def test_finish_rejects_foreign_report_before_sending_summary(cli,report,monkeypatch):
    sent=[]
    monkeypatch.setattr(cli,'notify',partial(monitor.notify,now=NOW,sender=sent.append))
    monkeypatch.setattr(cli,'current_report',lambda *a:dict(report,feed='other'))
    monkeypatch.setattr('sys.argv',['equity_research.py','finish'])
    assert cli.main()==1
    assert len(sent)==1 and 'free research finish failed' in sent[0]
