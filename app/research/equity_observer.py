"""Prospective, zero-subscription stock-signal observations on Alpaca IEX.

No order, options, subscription or broker-position methods exist here.
Returns are underlying-price diagnostics, never fills or options P&L.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import yaml

from app.operations.monitor import read_json, write_json
from app.strategies import OpeningRangeBreakoutStrategy, VWAPReclaimStrategy

ET = ZoneInfo('America/New_York')
PROTOCOL = 'iex_equity_signal_screen_v1_2026_10_24'
INTERVAL = timedelta(minutes=5)
HORIZONS = (5, 15, 30)


def stamp(value):
    ts = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(ts, datetime) or ts.tzinfo is None:
        raise ValueError('Bar timestamp must be timezone aware')
    return ts.astimezone(ET)


def universe(root):
    config = yaml.safe_load((root/'config/ticker_universe.yaml').read_text())
    symbols = list(dict.fromkeys(s for group in config['enabled_groups'] for s in config['groups'][group]))
    if not symbols or len(symbols)>40 or any(not s.replace('.','').isalnum() for s in symbols):
        raise ValueError('Invalid research universe')
    return symbols


class EquityData:
    """Only GET /v2/stocks/bars, explicitly feed=iex, with bounded pagination."""
    def __init__(self, key, secret, *, transport=None):
        if not key or not secret:
            raise ValueError('Existing Alpaca credentials are required')
        self.client = httpx.AsyncClient(base_url='https://data.alpaca.markets',
            headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret},
            timeout=20, transport=transport)

    async def close(self):
        await self.client.aclose()

    async def bars(self, symbols, start, end):
        result={s:[] for s in symbols}
        params={'symbols':','.join(symbols),'timeframe':'5Min','feed':'iex',
                'start':start.isoformat(),'end':end.isoformat(),'adjustment':'raw','limit':10000}
        tokens=set()
        for _ in range(100):
            response=await self.client.get('/v2/stocks/bars',params=params)
            response.raise_for_status()
            payload=response.json()
            rows=payload.get('bars')
            if not isinstance(rows,dict):
                raise ValueError('Missing stock-bar mapping')
            for symbol, values in rows.items():
                if symbol not in result or not isinstance(values,list):
                    raise ValueError('Unexpected stock-bar symbol or schema')
                for row in values:
                    ts=stamp(row.get('t'))
                    if ts.minute%5 or ts.second or ts.microsecond:
                        raise ValueError('Stock bar is not aligned to five minutes')
                    prices=[float(row[k]) for k in ('o','h','l','c')]
                    volume=float(row['v'])
                    if (not all(math.isfinite(v) and v>0 for v in prices)
                        or not math.isfinite(volume) or volume<0 or prices[1]<max(prices[0],prices[3])
                        or prices[2]>min(prices[0],prices[3]) or prices[1]<prices[2]):
                        raise ValueError('Invalid stock-bar prices or volume')
                    if not start<=ts<=end:
                        raise ValueError('Stock bar outside requested interval')
                    # IEX coverage is incomplete; omitted bars remain missing.
                    if time(9,30)<=ts.time()<time(16) and ts+INTERVAL<=end:
                        result[symbol].append({'t':ts.isoformat(), **dict(zip(('open','high','low','close'),prices)), 'volume':volume})
            token=payload.get('next_page_token')
            if not token:
                return result
            if token in tokens:
                raise ValueError('Repeated stock-bar page token')
            tokens.add(token);params['page_token']=token
        raise ValueError('Stock-bar pagination limit exceeded')


async def data_canary(data, *, now=None):
    now=now or datetime.now(ET)
    rows=await data.bars(['SPY'],now-timedelta(days=7),now)
    bars=rows['SPY']
    if not bars:
        raise ValueError('IEX probe returned no completed SPY bars')
    latest=max(stamp(b['t']) for b in bars)
    in_session=now.weekday()<5 and time(9,40)<=now.time()<time(12,30)
    if in_session and (latest.date()!=now.date() or (now-latest-INTERVAL).total_seconds()>600):
        raise ValueError('IEX SPY bars are stale during the research window')
    return {'feed':'iex','completed_bars':len(bars),'latest_bar':latest.isoformat(),
            'freshness':'verified' if in_session else 'deferred_until_session',
            'paid_subscription_required':False,'options_results':None}


def frame(rows):
    if not rows:
        return pd.DataFrame(columns=['open','high','low','close','volume'])
    table=pd.DataFrame(rows).set_index('t')
    table.index=pd.DatetimeIndex([stamp(t) for t in table.index])
    return table.sort_index()


def next_entry_bar(observed):
    """First five-minute bar beginning at or after actual signal discovery."""
    seconds=observed.timestamp()
    return datetime.fromtimestamp(math.ceil(seconds/300)*300,ET)


def outcomes(signal, rows, *, now):
    by_time={stamp(b['t']):b for b in rows if stamp(b['t'])+INTERVAL<=now}
    entry_time=stamp(signal['entry_bar'])
    entry=by_time.get(entry_time)
    result={str(h):None for h in HORIZONS}
    if entry is None:
        return result
    direction=1 if signal['direction']=='long' else -1
    for horizon in HORIZONS:
        required=[entry_time+INTERVAL*i for i in range(horizon//5)]
        if all(t in by_time for t in required):
            last=by_time[required[-1]]
            result[str(horizon)]=round(direction*(last['close']/entry['open']-1)*10000,4)
    return result


class Observer:
    def __init__(self, root, evidence, symbols, day, *, code_sha):
        self.root=root;self.symbols=symbols;self.day=day;self.code_sha=code_sha
        self.directory=evidence/'equity_research'
        self.state_file=self.directory/'state'/f'{day}.json'
        self.events=self.directory/'events'/f'{day}.jsonl'
        self.state=read_json(self.state_file) or {'protocol':PROTOCOL,'date':day,'bars':{},'signals':{},
            'code_sha':code_sha,'cycles':0,'request_failures':0,'signal_errors':0,'started_at':None,'last_data_at':None}
        if self.state.get('protocol')!=PROTOCOL or self.state.get('date')!=day or self.state.get('code_sha')!=code_sha:
            raise ValueError('Incompatible research state')
        self.strategies=[OpeningRangeBreakoutStrategy({'range_minutes':15,'min_range_pts':.5,
            'volume_confirmation':True,'confirmation_bars':2}),
            VWAPReclaimStrategy({'proximity_pct':.002,'confirmation_bars':2})]

    def event(self, event, now, **values):
        self.events.parent.mkdir(parents=True,exist_ok=True)
        with self.events.open('a') as f:
            f.write(json.dumps({'event':event,'observed_at':now.isoformat(),'protocol':PROTOCOL,
                'feed':'iex','code_sha':self.code_sha,**values},allow_nan=False)+'\n')

    def cycle(self, incoming, now):
        if now.date().isoformat()!=self.day:
            raise ValueError('Session-date boundary crossed')
        if not self.state['started_at']:
            self.state['started_at']=now.isoformat()
            self.event('session_start',now,symbols=self.symbols,broker_entries_enabled=False)
        self.state['cycles']+=1
        # Keep first-observed completed values. Later revisions never rewrite
        # the information used to generate earlier signals.
        fresh=[]
        for symbol in self.symbols:
            saved=self.state['bars'].setdefault(symbol,{})
            for bar in incoming.get(symbol,[]):
                ts=stamp(bar['t'])
                if ts+INTERVAL<=now and bar['t'] not in saved:
                    saved[bar['t']]=bar
                    self.event('completed_bar',now,symbol=symbol,bar=bar)
            rows=list(saved.values())
            current=[b for b in rows if stamp(b['t']).date()==now.date()]
            if current and 0<=(now-max(stamp(b['t']) for b in current)-INTERVAL).total_seconds()<=600:
                fresh.append(symbol)
            table=frame(rows)
            if len(table)<17:
                continue # historical warm-up is fetched at the first cycle
            for strategy in self.strategies:
                try:
                    signals=strategy.generate_signals(table,symbol)
                except Exception as exc:
                    self.state['signal_errors']+=1
                    self.event('signal_error',now,symbol=symbol,strategy=strategy.strategy_id,error_type=type(exc).__name__)
                    continue
                for signal in signals:
                    confirmed=stamp(signal.timestamp)+INTERVAL
                    if confirmed.date()!=now.date() or confirmed.time()>=time(12):
                        continue
                    key=f'{symbol}|{signal.strategy_id}|{signal.direction.value}|{confirmed.isoformat()}'
                    if key in self.state['signals']:
                        continue
                    lag=(now-confirmed).total_seconds()
                    if lag<0:
                        raise ValueError('Strategy produced a signal before its confirming bar closed')
                    entry_bar=next_entry_bar(now)
                    eligible=lag<=600 and entry_bar.time()<time(12)
                    record={'id':key,'symbol':symbol,'strategy':signal.strategy_id,
                        'direction':signal.direction.value,'confirmed_at':confirmed.isoformat(),
                        'observed_at':now.isoformat(),'signal_lag_seconds':lag,
                        'entry_bar':entry_bar.isoformat(),'prospective':eligible,
                        'exclusion_reason':None if eligible else 'late_discovery_or_entry_cutoff',
                        'entry_proxy':'next five-minute bar open; no fill assumed'}
                    self.state['signals'][key]=record
                    self.event('signal',now,signal=record)
        self.state['fresh_symbols']=fresh
        if fresh:self.state['last_data_at']=now.isoformat()
        self.state['last_cycle_at']=now.isoformat()
        write_json(self.state_file,self.state)
        return self.report(now)

    def failure(self, now, exc):
        self.state['request_failures']+=1
        self.state['last_cycle_at']=now.isoformat()
        self.event('data_error',now,error_type=type(exc).__name__,
            http_status=getattr(getattr(exc,'response',None),'status_code',None))
        write_json(self.state_file,self.state)
        return self.report(now,failed_cycle=True)

    def report(self, now, *, failed_cycle=False, finished=False):
        signals=[]
        for row in self.state['signals'].values():
            signals.append({**row,'gross_forward_bps':outcomes(row,list(self.state['bars'].get(row['symbol'],{}).values()),now=now)})
        groups=[]
        for strategy in ('orb','vwap_reclaim'):
            for horizon in HORIZONS:
                values=[r['gross_forward_bps'][str(horizon)] for r in signals
                        if r['strategy']==strategy and r['prospective'] and r['gross_forward_bps'][str(horizon)] is not None]
                groups.append({'strategy':strategy,'horizon_minutes':horizon,'observations':len(values),
                    'mean_gross_bps':round(statistics.mean(values),4) if values else None,
                    'median_gross_bps':round(statistics.median(values),4) if values else None,
                    'positive_fraction':sum(v>0 for v in values)/len(values) if values else None,
                    'cost_sensitivity_bps':{str(c):round(statistics.mean(values)-c,4) if values else None for c in (5,10,20)}})
        coverage=len(self.state.get('fresh_symbols',[]))/len(self.symbols)
        active=now.weekday()<5 and time(9,40)<=now.time()<time(12,30)
        degraded=failed_cycle or self.state['request_failures']>0 or self.state['signal_errors']>0 or ((active or finished) and ('SPY' not in self.state.get('fresh_symbols',[]) or coverage<.8))
        report={'protocol':PROTOCOL,'date':self.day,'generated_at':now.isoformat(),'code_sha':self.code_sha,
            'mode':'equity_research','feed':'iex','data_status':'degraded' if degraded else 'ok' if active else 'outside_active_window',
            'research_status':'completed' if finished else 'running','symbols':self.symbols,
            'fresh_symbols':self.state.get('fresh_symbols',[]),'coverage_fraction':coverage,
            'cycles':self.state['cycles'],'request_failures':self.state['request_failures'],
            'signal_errors':self.state['signal_errors'],'last_data_at':self.state['last_data_at'],
            'last_cycle_at':self.state.get('last_cycle_at'),'session_start':self.state['started_at'],
            'broker_entries_enabled':False,'counts_toward_options_readiness':False,
            'options_pnl':None,'broker_realized_pnl':None,'signals':signals,'summary':groups,
            'limitations':['IEX covers one exchange; volumes and signals can differ from consolidated data.',
                'Forward returns use a future bar-open proxy. No fills, spread, fees, short availability or options outcomes are established.',
                'Cost sensitivity subtracts assumed round-trip basis points; it is not an execution-cost model.',
                'Signals overlap and are not independent trades. This is a screening experiment; positive gross movement does not establish profitability.']}
        destination=self.directory/'reports'/f'{self.day}.json'
        write_json(destination,report)
        md=['# Free IEX stock-signal research',f'Date: {self.day}',f'Data status: {report["data_status"]}',
            'Order placement: disabled. Options results: unavailable.','',
            '| Strategy | Horizon | Observations | Mean gross bps | Median gross bps |',
            '| --- | ---: | ---: | ---: | ---: |']
        for row in groups:
            md.append(f'| {row["strategy"]} | {row["horizon_minutes"]} min | {row["observations"]} | {row["mean_gross_bps"]} | {row["median_gross_bps"]} |')
        md+=['',*report['limitations']]
        destination.with_suffix('.md').write_text('\n'.join(md)+'\n')
        return report
