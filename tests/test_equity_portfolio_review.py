"""Portfolio admission is causal, bounded, reproducible and never places orders."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import sys

import pytest

from app.research import portfolio_review as p
from app.research.baseline import frozen_baseline, baseline_identity, STRATEGY_PARAMETERS

ROOT = Path(__file__).resolve().parents[1]
POLICY = p.load_policy(ROOT)
SHA = 'a' * 40


def at(day, hour, minute, second=0):
    return datetime.combine(day, datetime.min.time(), p.ET).replace(hour=hour, minute=minute, second=second)


def fixture_day(tmp_path, *, day=date(2026, 11, 2), specs=None, missing=(), close_overrides=None, start=None):
    """Small authentic report/event pair with known 100-dollar entry prices."""
    symbols = ['SPY', 'QQQ']
    baseline = {'protocol': p.PROTOCOL, 'baseline_sha256': 'baseline', 'files_sha256': {},
        'symbols': symbols, 'verified_legacy_source_commits': [SHA]}
    evidence = tmp_path / 'evidence'
    base = evidence / 'equity_research'
    (base / 'reports').mkdir(parents=True, exist_ok=True)
    (base / 'events').mkdir(exist_ok=True)
    start = start or at(day, 9, 30, 10)
    events = [{'event': 'session_start', 'observed_at': start.isoformat(), 'symbols': symbols,
               'broker_entries_enabled': False}]
    bars = {symbol: {} for symbol in symbols}
    for symbol in symbols:
        for i in range(36):
            ts = at(day, 9, 30) + i * p.STEP
            if (symbol, ts.hour, ts.minute) in missing:continue
            close = (close_overrides or {}).get((symbol, ts.hour, ts.minute), 100)
            bar = {'t': ts.isoformat(), 'open': 100, 'high': max(100, close) + 1,
                'low': min(100, close) - 1, 'close': close, 'volume': 1000}
            bars[symbol][ts] = bar
            received = ts + p.STEP + timedelta(seconds=5)
            events.append({'event': 'completed_bar', 'observed_at': max(start, received).isoformat(), 'symbol': symbol, 'bar': bar})
    specs = specs if specs is not None else [('SPY', 'long', 10, 0, 10)]
    signals = []
    for symbol, direction, hour, minute, second in specs:
        observed = at(day, hour, minute, second)
        confirmed = observed.replace(second=0, minute=(observed.minute // 5) * 5)
        if observed < confirmed + timedelta(seconds=5):confirmed -= p.STEP
        entry = datetime.fromtimestamp(__import__('math').ceil(observed.timestamp() / 300) * 300, p.ET)
        signal = {'id': f'{symbol}|orb|{direction}|{confirmed.isoformat()}', 'symbol': symbol,
            'strategy': 'orb', 'direction': direction, 'confirmed_at': confirmed.isoformat(),
            'observed_at': observed.isoformat(), 'entry_bar': entry.isoformat(),
            'signal_lag_seconds': (observed - confirmed).total_seconds(),
            'prospective': True, 'exclusion_reason': None, 'entry_proxy': 'next five-minute bar open; no fill assumed'}
        events.append({'event': 'signal', 'observed_at': observed.isoformat(), 'signal': deepcopy(signal)})
        values = {}
        for horizon in (5, 15, 30):
            times = [entry + i * p.STEP for i in range(horizon // 5)]
            values[str(horizon)] = (round((1 if direction == 'long' else -1) *
                (bars[symbol][times[-1]]['close'] / 100 - 1) * 10000, 4)
                if all(t in bars[symbol] for t in times) else None)
        signals.append({**signal, 'gross_forward_bps': values})
    for event in events:event.update(protocol=p.PROTOCOL, feed='iex', code_sha=SHA)
    events.sort(key=lambda event: p.stamp(event['observed_at']))
    report = {'protocol': p.PROTOCOL, 'date': str(day), 'code_sha': SHA, 'mode': 'equity_research',
        'feed': 'iex', 'broker_entries_enabled': False, 'symbols': symbols, 'fresh_symbols': symbols,
        'coverage_fraction': 1, 'cycles': 170, 'request_failures': 0, 'signal_errors': 0,
        'session_start': start.isoformat(), 'last_cycle_at': at(day, 12, 30, 5).isoformat(),
        'generated_at': at(day, 12, 30, 10).isoformat(), 'research_status': 'completed',
        'session_complete': True, 'termination': 'scheduled_end', 'data_status': 'outside_active_window', 'signals': signals}
    path = base / 'reports' / f'{day}.json'
    path.write_text(json.dumps(report))
    event_path = base / 'events' / f'{day}.jsonl'
    event_path.write_text(''.join(json.dumps(e) + '\n' for e in events))
    return path, evidence, baseline


def session(tmp_path, **kwargs):
    path, evidence, baseline = fixture_day(tmp_path, **kwargs)
    return p.load_session(path, evidence, baseline)


def test_known_whole_share_pnl_and_costs_without_network(tmp_path, monkeypatch):
    s = session(tmp_path, close_overrides={('SPY', 10, 30): 102})
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('offline replay used network'))
    result = p.replay([s], POLICY, 10)
    assert result['closed_proxies'] == 1 and result['unresolved'] == 0
    trade = result['trades'][0]
    assert trade['quantity'] == 10
    assert trade['proxy_gross_pnl_dollars'] == 20
    assert trade['assumed_cost_dollars'] == 1
    assert result['proxy_net_pnl_dollars'] == 19
    assert result['proxy_return_on_initial_equity_pct'] == pytest.approx(.038)
    assert trade['settled_at'].startswith('2026-11-02T10:35:05')


def test_slot_remains_reserved_until_exit_evidence_is_received(tmp_path):
    s = session(tmp_path, specs=[('SPY','long',10,0,10),('QQQ','long',10,35,1),('QQQ','long',10,40,10)])
    r = p.replay([s], POLICY, 10)
    assert [t['symbol'] for t in r['trades']] == ['SPY','QQQ']
    assert r['trades'][1]['decision_at'].startswith('2026-11-02T10:40:10')
    assert r['skipped']['position_slot_reserved'] == 1


def test_ties_use_frozen_universe_order_instead_of_future_winners(tmp_path):
    s = session(tmp_path, specs=[('QQQ','long',10,0,10),('SPY','long',10,0,10)],
        close_overrides={('QQQ',10,30): 150, ('SPY',10,30): 99})
    r = p.replay([s], POLICY, 5)
    assert [t['symbol'] for t in r['trades']] == ['SPY']
    assert r['proxy_net_pnl_dollars'] < 0


def test_no_more_than_three_entries_and_no_unverified_shorts(tmp_path):
    s = session(tmp_path, specs=[('QQQ','short',9,45,10),('SPY','long',9,50,10),
        ('SPY','long',10,25,10),('SPY','long',11,0,10),('SPY','long',11,35,10)])
    r = p.replay([s], POLICY, 5)
    assert len(r['trades']) == 3
    assert r['skipped']['unverified_short_availability'] == 1
    assert r['skipped']['daily_entry_limit'] == 1


def test_loss_entry_gate_uses_only_settled_losses(tmp_path):
    s = session(tmp_path, specs=[('SPY','long',10,0,10),('SPY','long',10,40,10)],
        close_overrides={('SPY',10,30): 96})
    r = p.replay([s], POLICY, 10)
    assert len(r['trades']) == 1
    assert r['proxy_net_pnl_dollars'] == -41
    assert r['skipped']['daily_realized_loss_entry_gate'] == 1


def test_missing_selected_outcome_blocks_exposure_and_keeps_total_unavailable(tmp_path):
    s = session(tmp_path, specs=[('SPY','long',10,0,10),('QQQ','long',11,0,10)], missing=[('SPY',10,15)])
    next_day = session(tmp_path, day=date(2026,11,3))
    r = p.replay([s, next_day], POLICY, 10)
    assert r['unresolved'] == 1 and r['proxy_net_pnl_dollars'] is None
    assert r['known_proxy_net_pnl_dollars'] == 0
    assert r['skipped']['position_slot_reserved'] == 1
    assert r['skipped']['unresolved_prior_exposure'] == 1


@pytest.mark.parametrize('damage',['outcome','baseline','missing_events','forming_bar','rewrite','signal_future','shorts_enabled','stale_freshness'])
def test_invalid_evidence_cannot_become_a_valid_session(tmp_path,damage):
    path, evidence, baseline = fixture_day(tmp_path)
    report=json.loads(path.read_text())
    events_path=evidence/'equity_research/events'/f'{path.stem}.jsonl'
    events=[json.loads(line) for line in events_path.read_text().splitlines()]
    if damage=='outcome':report['signals'][0]['gross_forward_bps']['30']=999
    elif damage=='baseline':report['baseline']={'baseline_sha256':'wrong'}
    elif damage=='missing_events':events_path.unlink()
    elif damage=='forming_bar':
        event=next(e for e in events if e['event']=='completed_bar')
        event['bar']['t']=p.stamp(event['observed_at']).replace(second=0).isoformat()
    elif damage=='rewrite':
        event=deepcopy(next(e for e in events if e['event']=='completed_bar'))
        event['bar']['close']=100.5;event['observed_at']=report['last_cycle_at'];events.append(event)
    elif damage=='signal_future':report['signals'][0]['observed_at']='2026-11-02T09:00:00-05:00'
    elif damage=='shorts_enabled':report['broker_entries_enabled']=True
    elif damage=='stale_freshness':events=[e for e in events if not (e['event']=='completed_bar' and e['symbol']=='QQQ' and p.stamp(e['bar']['t']).time()>=at(date(2026,11,2),12,15).time())]
    path.write_text(json.dumps(report))
    if damage!='missing_events':events_path.write_text(''.join(json.dumps(e)+'\n' for e in events))
    with pytest.raises((p.EvidenceError,OSError)):p.load_session(path,evidence,baseline)


def test_partial_and_in_progress_sessions_are_separate_from_full_count(tmp_path):
    path,evidence,baseline=fixture_day(tmp_path,start=at(date(2026,11,2),9,40))
    assert p.load_session(path,evidence,baseline).category=='partial'
    report=json.loads(path.read_text());report['research_status']='running';path.write_text(json.dumps(report))
    assert p.load_session(path,evidence,baseline).category=='in_progress'


def test_baseline_and_policy_refuse_silent_changes(tmp_path):
    baseline=frozen_baseline(ROOT)
    assert baseline['strategy_parameters']==STRATEGY_PARAMETERS
    assert len(baseline['symbols'])==38
    from shutil import copy2
    for path in [p.POLICY_FILE,'evaluation/equity_research_baseline_2026_11_03.json',*baseline['files_sha256']]:
        dest=tmp_path/path;dest.parent.mkdir(parents=True,exist_ok=True);copy2(ROOT/path,dest)
    (tmp_path/'app/strategies/opening_range_breakout.py').write_text('changed')
    with pytest.raises(ValueError,match='Frozen research source'):frozen_baseline(tmp_path)
    # Re-signing a changed source file cannot merge old sessions into its cohort.
    import hashlib
    path=tmp_path/'evaluation/equity_research_baseline_2026_11_03.json'
    data=json.loads(path.read_text())
    data['files_sha256']['app/strategies/opening_range_breakout.py']=hashlib.sha256(b'changed').hexdigest()
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Legacy strategy source'):frozen_baseline(tmp_path)
    (tmp_path/p.POLICY_FILE).write_text('{}')
    with pytest.raises(p.EvidenceError,match='policy_changed'):p.load_policy(tmp_path)


def build_fixture_review(tmp_path,monkeypatch,days,*,bad_holdout=False):
    day=date(2026,10,1)
    for i in range(days):
        while day.weekday()>=5:day+=timedelta(days=1)
        close=96 if bad_holdout and i>=20 else 102
        _,evidence,baseline=fixture_day(tmp_path,day=day,specs=[('SPY','long',10,0,10),('SPY','long',11,0,10)],
            close_overrides={('SPY',10,30):close,('SPY',11,30):close})
        day+=timedelta(days=1)
    monkeypatch.setattr(p,'frozen_baseline',lambda root:baseline)
    monkeypatch.setattr(p,'load_policy',lambda root:POLICY)
    return p.build_review(tmp_path/'code',evidence),evidence


def test_checkpoints_and_holdout_are_disjoint_and_never_enable_orders(tmp_path,monkeypatch):
    first,evidence=build_fixture_review(tmp_path,monkeypatch,20)
    assert first['development_screen']['passed']
    assert first['stage']=='collecting_frozen_holdout'
    assert not first['candidate_for_manual_paper_review']
    final,_=build_fixture_review(tmp_path,monkeypatch,30)
    assert not set(final['development_dates']) & set(final['holdout_dates'])
    assert final['development_portfolios']==first['development_portfolios']
    assert final['candidate_for_manual_paper_review']
    assert final['broker_entries_enabled'] is False
    assert final['options_pnl'] is None and final['broker_realized_pnl'] is None
    failed,_=build_fixture_review(tmp_path,monkeypatch,30,bad_holdout=True)
    assert failed['development_portfolios']==final['development_portfolios']
    assert not failed['holdout_screen']['passed']
    assert failed['stage']=='holdout_screen_failed'


def test_invalid_day_blocks_review_and_missing_outcomes_are_not_zero(tmp_path,monkeypatch):
    r,evidence=build_fixture_review(tmp_path,monkeypatch,5)
    path=sorted((evidence/'equity_research/reports').glob('*.json'))[-1]
    report=json.loads(path.read_text());report['signals'][0]['gross_forward_bps']['30']=10000
    path.write_text(json.dumps(report))
    r=p.build_review(tmp_path/'code',evidence)
    assert r['full_valid_sessions']==4 and r['invalid_sessions']==1
    assert r['stage']=='evidence_review_required'
    assert r['sessions'][-1]['reasons']==['forward_return_mismatch']


def test_rebuild_is_idempotent_and_archives_only_current_review(tmp_path,monkeypatch):
    from app.operations.artifacts import archive, verify_archive
    import subprocess
    review,evidence=build_fixture_review(tmp_path,monkeypatch,5)
    output=evidence/'equity_research/review/latest.json'
    before=output.read_bytes()
    p.build_review(tmp_path/'code',evidence)
    assert output.read_bytes()==before
    root=tmp_path/'code';root.mkdir()
    def git(*args):subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
    git('init');git('config','user.name','Test');git('config','user.email','test@example.test')
    (root/'source').write_text('source');git('add','.');git('commit','-m','source')
    today=review['as_of_session']
    first=archive(root,evidence,today);verify_archive(first)
    assert (first/'equity_research/review.json').exists()
    assert archive(root,evidence,today)==first
    older=review['sessions'][0]['date']
    assert not (archive(root,evidence,older)/'equity_research/review.json').exists()


def test_daily_aggregation_and_outlier_checks(tmp_path):
    first=session(tmp_path,close_overrides={('SPY',10,30):110})
    second=session(tmp_path,day=date(2026,11,3),close_overrides={('SPY',10,30):99})
    rows=p.signal_summary([first,second]);row=next(r for r in rows if r['strategy']=='orb' and r['horizon_minutes']==30)
    assert row['mean_gross_bps']==450
    assert row['mean_without_best_signal_bps']==-100
    assert row['leave_one_day_out_mean_bps']['2026-11-02']==-100
    replay=p.replay([first,second],POLICY,10)
    assert replay['net_without_best_day_dollars']==-11


def test_cumulative_dashboard_is_read_only_and_escapes_evidence(tmp_path,monkeypatch):
    from app.api import equity_research as api
    monkeypatch.setenv('TRADER_EVIDENCE_DIR',str(tmp_path))
    assert api.cumulative_review_json()['status']=='awaiting_cumulative_review'
    directory=tmp_path/'equity_research/review';directory.mkdir(parents=True)
    (directory/'latest.json').write_text(json.dumps({'as_of_session':'<script>alert(1)</script>',
        'full_valid_sessions':5,'partial_sessions':1,'eligible_signals_full_sessions':260}))
    page=api.cumulative_review_page()
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert 'Order placement disabled' in page and 'first 20 full sessions' in page
    for route in api.router.routes:
        if 'review' in route.path:assert route.methods=={'GET'}


def test_finish_archives_daily_evidence_even_when_cumulative_review_fails(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('review_finish',ROOT/'scripts/equity_research.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    root=tmp_path/'code';root.mkdir()
    monkeypatch.setattr(cli,'ROOT',root)
    monkeypatch.setattr(cli,'configured',lambda:(tmp_path/'evidence',tmp_path/'locks'))
    monkeypatch.setattr(cli,'verify_flat',AsyncMock())
    monkeypatch.setattr(cli,'current_report',lambda *a:{'date':'2026-11-02'})
    monkeypatch.setattr(cli,'build_review',Mock(side_effect=ValueError('broken analysis')))
    monkeypatch.setattr(cli,'notify',Mock())
    publisher=Mock(return_value=tmp_path/'snapshot');monkeypatch.setattr(cli,'publish',publisher)
    monkeypatch.setattr(sys,'argv',['equity_research.py','finish'])
    assert cli.main()==1
    publisher.assert_called_once()
    assert (root/'KILL_SWITCH').exists() and not (root.parent/'.session_armed').exists()
