#!/usr/bin/env python3
"""Manage the free, read-only IEX research observer. Never arms broker trading."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from contextlib import ExitStack
from datetime import datetime, time, timedelta

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from app.operations.monitor import ET, calendar, notify, read_json, write_json, verify_flat
from app.operations.artifacts import publish
from app.research.equity_observer import EquityData, Observer, data_canary, universe, PROTOCOL

STOP=False


def stop(signum, frame):
    global STOP
    STOP=True


def identity():
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()


def configured():
    load_dotenv(os.getenv('TRADER_OPS_ENV','/root/trader-ops.env'),override=False)
    load_dotenv(ROOT/'.env',override=False)
    if os.getenv('TRADER_RUN_MODE')!='equity_research' or os.getenv('LIVE_TRADING_ENABLED','false').lower()!='false':
        raise ValueError('Free observer requires equity_research mode and live trading disabled')
    evidence=Path(os.getenv('TRADER_EVIDENCE_DIR','/root/trader-evidence'))
    if evidence.resolve()==ROOT or ROOT in evidence.resolve().parents:
        raise ValueError('Evidence must be outside the source checkout')
    return evidence,Path(os.getenv('TRADER_LOCK_DIR',str(ROOT.parent)))


def client():
    return EquityData(os.getenv('ALPACA_API_KEY'),os.getenv('ALPACA_SECRET_KEY'))


async def check_data():
    data=client()
    try:return await data_canary(data)
    finally:await data.close()


async def collect_cycle(observer, data, symbols, start, requested_at):
    incoming=await data.bars(symbols,start,requested_at)
    # Discovery happens after the HTTP response, never when it was requested.
    return observer.cycle(incoming,datetime.now(ET))


def current_report(evidence, day):
    return read_json(evidence/'equity_research/reports'/f'{day}.json')


def ensure_disarmed():
    (ROOT/'KILL_SWITCH').touch()
    (ROOT.parent/'.session_armed').unlink(missing_ok=True)


def lock(stack, path, *, shared=False):
    handle=stack.enter_context(path.open('a'))
    fcntl.flock(handle,(fcntl.LOCK_SH if shared else fcntl.LOCK_EX)|fcntl.LOCK_NB)
    return handle


def watchdog_problem(report, now):
    if report.get('date')!=str(now.date()) or report.get('research_status')!='running':
        return 'No running equity-research report for today'
    if (report.get('protocol')!=PROTOCOL or report.get('feed')!='iex'
            or report.get('broker_entries_enabled') is not False):
        return 'Equity-research provenance or order-disable state is missing'
    try:
        ts=datetime.fromisoformat(report['last_cycle_at'])
        if ts.tzinfo is None or not 0<=(now-ts).total_seconds()<=180:
            return 'Equity-research heartbeat is stale'
    except (KeyError,TypeError,ValueError):
        return 'Equity-research heartbeat is missing'
    if now.time()>=time(9,40) and report.get('data_status')!='ok':
        return 'Equity research reports missing, stale or failed IEX data'
    return None


async def run_loop(evidence, *, once=False):
    global STOP
    now=datetime.now(ET)
    if not calendar(ROOT,now=now):
        print('MARKET_CLOSED: observer remains disarmed');return 0
    if not time(9,30)<=now.time()<time(12,30):
        raise ValueError('Observer runs only 09:30-12:30 Eastern')
    symbols=universe(ROOT)
    observer=Observer(ROOT,evidence,symbols,str(now.date()),code_sha=identity())
    data=client()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    deadline=now.replace(hour=12,minute=30,second=0,microsecond=0)
    try:
        while not STOP and datetime.now(ET)<deadline:
            now=datetime.now(ET)
            # Prior sessions supply causal strategy warm-up. Subsequent
            # requests refresh today's bars only; first-observed values persist.
            start=(now-timedelta(days=7)).replace(hour=9,minute=30,second=0,microsecond=0) if not observer.state['bars'] else now.replace(hour=9,minute=30,second=0,microsecond=0)
            try:
                report=await collect_cycle(observer,data,symbols,start,now)
            except Exception as exc:
                now=datetime.now(ET)
                report=observer.failure(now,exc)
                notify(ROOT,'equity_research_data_failed',f'{now.date()}: IEX equity research encountered {type(exc).__name__}. No orders are enabled.')
                if getattr(getattr(exc,'response',None),'status_code',None) in (401,403):
                    break # credentials rejected: stop hammering the provider
            write_json(ROOT/'logs/equity_research_status.json',report)
            if once:break
            for _ in range(60):
                if STOP or datetime.now(ET)>=deadline:break
                await asyncio.sleep(1)
        # One final completed-bar fetch at the scheduled end makes the final
        # horizon observable; forced/stopped runs retain missing outcomes.
        now=datetime.now(ET)
        if not STOP and now>=deadline:
            try:
                await collect_cycle(observer,data,symbols,deadline.replace(hour=9,minute=30),now)
            except Exception as exc:
                observer.failure(now,exc)
        now=datetime.now(ET)
        report=observer.report(now,finished=True)
        report['termination']='scheduled_end' if now>=deadline and not STOP else 'early_stop_or_single_cycle'
        report['session_complete']=now>=deadline and not STOP
        if not report['session_complete']:report['research_status']='interrupted'
        write_json(evidence/'equity_research/reports'/f'{now.date()}.json',report)
        write_json(ROOT/'logs/equity_research_status.json',report)
        return 3 if report['data_status']=='degraded' else 0
    finally:
        await data.close()
        ensure_disarmed()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['check','preflight','run','watchdog','finish'])
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    evidence,locks=configured();locks.mkdir(parents=True,exist_ok=True)
    now=datetime.now(ET);day=str(now.date())
    ready=ROOT/'logs/equity_research_ready.json'
    try:
        with ExitStack() as stack:
            if args.command!='run':lock(stack,locks/'.equity-research-operations.lock')
            if args.command=='check':
                print('FREE_IEX_DATA_READY '+json.dumps(asyncio.run(check_data())))
                return 0
            if args.command=='watchdog':
                if now.weekday()>=5 or not time(9,35)<=now.time()<time(12,30) or not calendar(ROOT,now=now):return 0
                issue=watchdog_problem(current_report(evidence,day),now)
                if issue:
                    notify(ROOT,'equity_research_unhealthy',f'{day}: {issue}. Options and orders remain disabled.')
                    print('RESEARCH_UNHEALTHY '+issue);return 1
                return 0
            eod=lock(stack,locks/'.eod_close.lock',shared=args.command!='finish')
            lock(stack,locks/'.session.lock')
            ensure_disarmed()
            if args.command=='preflight':
                lock(stack,locks/'.session-preflight.lock')
                ready.unlink(missing_ok=True)
                # No source changes or silently advanced software releases.
                changes=subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=ROOT,text=True)
                if changes.strip():raise ValueError('Research source checkout is dirty')
                asyncio.run(verify_flat())
                result=asyncio.run(check_data())
                scheduled=calendar(ROOT,now=now,refresh=True)
                write_json(ready,{'date':day,'code_sha':identity(),'market_day':scheduled,'data':result,'orders_enabled':False})
                print('EQUITY_PREFLIGHT_READY: free IEX; broker trading stays disarmed')
                return 0
            if args.command=='run':
                marker=read_json(ready)
                if marker.get('date')==day and marker.get('code_sha')==identity() and marker.get('market_day') is False:
                    ready.unlink(missing_ok=True)
                    print('MARKET_CLOSED: research remains disarmed');return 0
                if marker.get('date')!=day or marker.get('code_sha')!=identity() or marker.get('market_day') is not True:
                    raise ValueError('No matching successful research preflight')
                ready.unlink(missing_ok=True)
                fcntl.flock(eod,fcntl.LOCK_UN)
                return asyncio.run(run_loop(evidence,once=args.once))
            if args.command=='finish':
                ready.unlink(missing_ok=True)
                asyncio.run(verify_flat())
                report=current_report(evidence,day)
                if not report:
                    if calendar(ROOT,now=now):
                        notify(ROOT,'equity_research_report_missing',f'{day}: research report is missing. No orders are enabled.')
                        return 1
                    return 0
                checkout=Path(os.getenv('TRADER_ARTIFACT_CHECKOUT','/root/trader-artifacts'))
                try:
                    snapshot=publish(ROOT,evidence,checkout,day)
                    print('RESEARCH_ARTIFACTS_PUBLISHED '+snapshot.name)
                except Exception as exc:
                    notify(ROOT,'equity_research_upload_failed',f'{day}: research upload failed ({type(exc).__name__}); local evidence is preserved.')
                    print('UPLOAD_PENDING: local research is preserved');return 1
                return 0
    except BlockingIOError:
        print('RESEARCH_BUSY: an existing session or operation holds a lock',file=sys.stderr)
        return 75
    except Exception as exc:
        status=getattr(getattr(exc,'response',None),'status_code',None)
        print(f'RESEARCH_FAILED command={args.command} type={type(exc).__name__} http_status={status}',file=sys.stderr)
        notify(ROOT,'equity_research_'+args.command+'_failed',f'{day}: free research {args.command} failed ({type(exc).__name__}). No orders are enabled.')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
