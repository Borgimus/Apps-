#!/usr/bin/env python3
"""One-console-command recovery of the existing paper observation service.

Original code, database and credentials stay on the host. A separate release
checkout prevents daily artifacts from moving the deployed software branch.
Never enables a broker entry strategy or buys a market-data subscription.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

BRANCH = 'agent/operational-recovery-2026-10-24'
PUBLIC_REMOTE = 'https://github.com/Borgimus/Apps-.git'
BEGIN = '# BEGIN PHASE3 SESSION AUTOMATION'
END = '# END PHASE3 SESSION AUTOMATION'
ET = ZoneInfo('America/New_York')
JOB_TOKENS = ('preflight_session.sh', 'start_session.sh', 'auto_close_session.sh',
              'eod_close.sh', 'scripts/ops/', 'scripts/session_runner.py', 'keepalive.sh')


def command(*args, check=True, cwd=None, env=None, input=None):
    result = subprocess.run(args, cwd=cwd, env=env, input=input, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if check and result.returncode:
        # Never echo subprocess stderr: Git and HTTP errors may contain secrets.
        raise RuntimeError(f'{Path(str(args[0])).name} failed (exit {result.returncode})')
    return result


def replace_cron(original, jobs):
    """Preserve unrelated cron jobs and remove only identified trader jobs."""
    lines = original.splitlines()
    if lines.count(BEGIN) != lines.count(END) or lines.count(BEGIN) > 1:
        raise ValueError('Ambiguous Phase 3 cron markers; nothing was changed')
    result, inside = [], False
    for line in lines:
        if line == BEGIN:
            inside = True
        elif line == END:
            inside = False
        elif not inside:
            if line.lstrip().startswith('#') or not any(t in line for t in JOB_TOKENS):
                result.append(line)
    return '\n'.join(result + [BEGIN, *jobs, END, ''])


def settings_env(original):
    """Explicit recovery policy: preserve research thresholds, suspend entries."""
    values = {k: str(v) for k, v in original.items() if v is not None}
    values.update(BROKER='alpaca', LIVE_TRADING_ENABLED='false', PAPER_EVALUATION_MODE='true',
        OPTIONS_DATA_PROVIDER='alpaca', ALPACA_OPTIONS_FEED='opra', API_HOST='127.0.0.1',
        TRADIER_MARKET_DATA_MODE='off', TRADIER_CONTRACT_GATE_MODE='off',
        PAPER_SCALED_SIZING_ENABLED='true', PAPER_SCALED_GUARDRAILS_ENABLED='true',
        PAPER_SCALED_SHADOW_ONLY_STRATEGIES='vwap_reclaim,orb',
        PAPER_SCALED_GUARDRAIL_COHORT='guardrails_v3_shadow_validation',
        TRADER_CODE_BRANCH=BRANCH, TRADER_ROOT='/root/trader-release',
        TRADER_BASELINE_PATH='/root/trader-release/evaluation/recovery_baseline_2026_10_24.json',
        TRADER_EVIDENCE_DIR='/root/trader-evidence', TRADER_ARTIFACT_CHECKOUT='/root/trader-artifacts',
        KILL_SWITCH_FILE='/root/trader-release/KILL_SWITCH',
        EVALUATION_OUTPUT_DIR='/root/trader-evidence', EVALUATION_LEDGER_FILE='/root/trader-evidence/ledger.json',
        MARKET_DATA_COMPARISON_FILE='/root/trader-evidence/market_data_comparisons.jsonl')
    return values


def write_env(path, values):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(''.join(f'{k}={shlex.quote(v)}\n' for k, v in sorted(values.items())
                                if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k)))
    temporary.chmod(0o600)
    temporary.replace(path)


def notification_config(values, old_scripts):
    url = values.get('OPS_NTFY_URL') or values.get('NTFY_URL')
    if not url and values.get('NTFY_TOPIC'):
        url = (values.get('NTFY_SERVER') or 'https://ntfy.sh').rstrip('/') + '/' + values['NTFY_TOPIC']
    if not url:
        for script in old_scripts:
            match = re.search(r'https://ntfy[.]sh/[A-Za-z0-9_-]+', script)
            if match:
                url = match[0]
                break
    return url, values.get('OPS_NTFY_TOKEN') or values.get('NTFY_TOKEN') or ''


def database_path(value, old):
    prefix = 'sqlite+aiosqlite:///'
    if not value or value.startswith(prefix):
        raw = (value or prefix + './trading.db').removeprefix(prefix)
        p = Path(raw)
        return p if p.is_absolute() else (old / p).resolve()
    raise ValueError('Recovery supports the existing SQLite deployment only')


def restart_dashboard(root, env):
    """Replace only dashboards owned by these trader checkouts, on loopback."""
    found = command('pgrep', '-f', r'[m]ain[.]py[[:space:]]+dashboard', check=False)
    if found.returncode not in (0, 1):
        raise ValueError('Dashboard process detection failed')
    owned = []
    for text in found.stdout.splitlines():
        pid = int(text)
        try:
            cwd = Path(f'/proc/{pid}/cwd').resolve(strict=True)
        except FileNotFoundError:
            continue
        if cwd in (Path('/root/trader'), root):
            os.kill(pid, signal.SIGTERM)
            owned.append(pid)
    for _ in range(20):
        if not any(Path(f'/proc/{pid}').exists() for pid in owned):
            break
        time.sleep(.25)
    else:
        raise ValueError('Old dashboard did not stop after SIGTERM')
    dashboard_env = dict(env, API_HOST='127.0.0.1')
    with (root / 'logs/dashboard.log').open('a') as output:
        subprocess.Popen([str(root/'.venv/bin/python'), 'main.py', 'dashboard'], cwd=root,
            env=dashboard_env, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            start_new_session=True, close_fds=True)
    for _ in range(10):
        if command('curl','-fsS','--max-time','2','http://127.0.0.1:8000/health',check=False).returncode == 0:
            return True
        time.sleep(.5)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch('[0-9a-f]{40}', args.release):
        raise ValueError('Run as root with the exact reviewed release SHA')
    now = datetime.now(ET)
    if now.weekday() < 5 and 9 <= now.hour < 13:
        raise ValueError('Run outside 09:00-13:00 Eastern, when the scheduled runner is stopped')
    timezone = command('timedatectl', 'show', '-p', 'Timezone', '--value').stdout.strip()
    if timezone != 'America/New_York':
        raise ValueError('Server timezone must be America/New_York for the Debian cron schedule')
    old, root = Path('/root/trader'), Path('/root/trader-release')
    sys.path.insert(0, str(args.source))
    from dotenv import dotenv_values
    # Do not execute or display environment contents.
    config = dict(dotenv_values(old / '.env'))
    ops = Path('/root/trader-ops.env')
    if ops.exists(): config.update(dotenv_values(ops))
    lock = Path('/root/.trader-recovery.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    held = []
    for name in ('.eod_close.lock', '.session.lock', '.session-preflight.lock'):
        handle = (Path('/root') / name).open('a')
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(handle)
    pids = command('pgrep', '-af', r'[s]cripts/session_runner[.]py([[:space:]]|$)', check=False)
    if pids.returncode != 1:
        raise ValueError('A runner is active or process detection failed; no installation attempted')
    cron_result = command('crontab', '-l', check=False)
    if cron_result.returncode not in (0, 1): raise ValueError('Could not read crontab')
    cron = cron_result.stdout
    paused = replace_cron(cron, ['# Paused until recovery checks pass'])
    backup = Path('/root/trader-backups') / ('recovery-' + now.strftime('%Y%m%dT%H%M%S%z'))
    backup.mkdir(parents=True, mode=0o700)
    (backup / 'crontab.txt').write_text(cron)
    (backup / 'original-head.txt').write_text(command('git', 'rev-parse', 'HEAD', cwd=old).stdout)
    scripts = []
    for name in ('preflight_session.sh','start_session.sh','auto_close_session.sh','eod_close.sh','trader-ops.env'):
        p = Path('/root') / name
        if p.is_file():
            shutil.copy2(p, backup / name)
            if name.endswith('.sh'): scripts.append(p.read_text())
    if (old / '.env').exists(): shutil.copy2(old / '.env', backup / 'original.env')
    for name in ('logs', 'evaluation'):
        if (old / name).is_dir(): shutil.copytree(old / name, backup / name, symlinks=True)
    database = database_path(config.get('DATABASE_URL'), old)
    if database.exists():
        with sqlite3.connect(str(database)) as src, sqlite3.connect(str(backup / 'trading.db')) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Database backup failed integrity check')
    command('crontab', '-', input=paused)
    (old / 'KILL_SWITCH').touch()
    Path('/root/.session_armed').unlink(missing_ok=True)
    print(f'BACKUP_COMPLETE {backup}\nOLD_TRADING_CRON_PAUSED', flush=True)

    if root.exists():
        actual = command('git', 'rev-parse', 'HEAD', cwd=root).stdout.strip()
        if actual != args.release:
            raise ValueError('Existing recovery checkout has a different release; preserve it for review')
    else:
        command('git', 'clone', '--quiet', '--no-hardlinks', str(args.source), str(root))
        command('git', 'checkout', '-B', BRANCH, args.release, cwd=root)
    command('git', 'remote', 'set-url', 'origin', PUBLIC_REMOTE, cwd=root)
    command('git', 'config', 'branch.' + BRANCH + '.remote', 'origin', cwd=root)
    command('git', 'config', 'branch.' + BRANCH + '.merge', 'refs/heads/' + BRANCH, cwd=root)
    # Reuse the host's locked dependency environment. No new software plan/account.
    if not (root / '.venv').exists(): (root / '.venv').symlink_to(old / '.venv', target_is_directory=True)
    python = str(root / '.venv/bin/python')
    values = settings_env(config)
    values['DATABASE_URL'] = 'sqlite+aiosqlite:///' + str(database)
    import secrets
    if len(values.get('DASHBOARD_CONTROL_TOKEN', '')) < 16:
        values['DASHBOARD_CONTROL_TOKEN'] = secrets.token_urlsafe(32)
    url, token = notification_config(values, scripts)
    if url: values.update(OPS_NTFY_URL=url, OPS_NTFY_TOKEN=token)
    (root / 'KILL_SWITCH').touch()
    env = dict(os.environ, **values, PYTHONPATH=str(root), GIT_TERMINAL_PROMPT='0')
    (root / 'logs').mkdir(exist_ok=True)
    # Load the new release in fresh processes, never the bootstrap interpreter.
    # Tests run without host credentials or host configuration, including retries.
    test_env = {k:v for k,v in os.environ.items() if k not in values and not k.startswith(('ALPACA_', 'TRADIER_', 'PAPER_', 'TRADER_'))}
    test_env['PYTHONPATH'] = str(root)
    saved_env = backup / 'release.env'
    if (root / '.env').exists(): (root / '.env').replace(saved_env)
    try:
        checks = command(python, '-m', 'pytest', 'tests/', '-q', '--tb=short', '-p', 'no:warnings', cwd=root, env=test_env, check=False)
    finally:
        if saved_env.exists(): saved_env.replace(root / '.env')
    (backup / 'tests.log').write_text(checks.stdout + checks.stderr)
    if checks.returncode: raise ValueError('Release tests failed; see tests.log in the backup')
    print(checks.stdout.splitlines()[-1], flush=True)
    write_env(root / '.env', values)
    write_env(ops, {k: values[k] for k in ('TRADER_ROOT','TRADER_CODE_BRANCH','TRADER_BASELINE_PATH','TRADER_EVIDENCE_DIR','TRADER_ARTIFACT_CHECKOUT','OPS_NTFY_URL','OPS_NTFY_TOKEN') if k in values})
    command(python, 'scripts/session_ops.py', 'migrate', '--source-evaluation', str(backup / 'evaluation'), cwd=root, env=env)
    command(python, 'scripts/session_ops.py', 'verify-flat', cwd=root, env=env)
    print('PAPER_ACCOUNT_VERIFIED_FLAT', flush=True)
    canary = command(python, 'scripts/session_ops.py', 'check-options-data', cwd=root, env=env, check=False)
    (backup / 'options-canary.log').write_text(canary.stdout + canary.stderr)
    if canary.returncode:
        print('ACTION_REQUIRED: Alpaca OPRA data access failed. Check the account data subscription and API keys. No subscription was purchased. Fix access and rerun the same command.', flush=True)
        raise ValueError('Options provider unavailable; schedule remains paused')
    print(canary.stdout.strip(), flush=True)
    if not url:
        raise ValueError('Set OPS_NTFY_URL to your existing private HTTPS topic in /root/trader-ops.env, then rerun')
    command(python, 'scripts/session_ops.py', 'check-config', cwd=root, env=env)
    command(python, 'scripts/session_ops.py', 'test-alert', '--message', 'Trader recovery checks passed. Paper observations will resume on the next market session.', cwd=root, env=env)
    print('ALERT_ACCEPTED_BY_SERVER: confirm receipt on your phone', flush=True)
    for name, script in (('preflight_session.sh','preflight'),('start_session.sh','start'),('auto_close_session.sh','close')):
        p = Path('/root') / name
        p.write_text(f'#!/usr/bin/env bash\nexport TRADER_ROOT=/root/trader-release\nexec bash /root/trader-release/scripts/ops/{script}.sh "$@"\n')
        p.chmod(0o700)
    # Our locks must be released before check-only preflight acquires them.
    for handle in held: handle.close()
    command('bash','/root/preflight_session.sh','--check-only',cwd=root,env=env)
    if not (root/'KILL_SWITCH').exists() or Path('/root/.session_armed').exists():
        raise ValueError('Check-only preflight failed to retain disarmed state')
    dashboard_ready = restart_dashboard(root, env)
    print('DASHBOARD_LOCAL_READY' if dashboard_ready else 'DASHBOARD_UNAVAILABLE: inspect logs/dashboard.log', flush=True)
    tailscale = command('systemctl', 'is-active', 'tailscaled', check=False)
    print('TAILSCALE_SERVICE_ACTIVE' if tailscale.returncode == 0 else 'TAILSCALE_SERVICE_UNAVAILABLE: remote dashboard access needs attention', flush=True)
    ready = replace_cron(cron, ['CRON_TZ=America/New_York',
        '20 9 * * 1-5 /root/preflight_session.sh',
        '30 9 * * 1-5 /root/start_session.sh',
        '35 12 * * 1-5 /root/auto_close_session.sh',
        '* * * * * TRADER_ROOT=/root/trader-release /bin/bash /root/trader-release/scripts/ops/watchdog.sh',
        '40,50 12 * * 1-5 TRADER_ROOT=/root/trader-release /bin/bash /root/trader-release/scripts/ops/publish.sh'])
    command('crontab','-',input=ready)
    status = {'release':args.release,'installed_at':now.isoformat(),'paper_only':True,
        'broker_entry_strategies':[],'source_fetch_requires_pat':False,
        'preflight_passed':True,'options_authorization_verified':True,
        'market_open_freshness':'must pass during next market session',
        'dashboard_local_ready':dashboard_ready, 'tailscale_service_active':tailscale.returncode == 0,
        'backup':str(backup),'database_reconciled_to_broker_fills':False}
    Path('/root/trader-recovery-status.json').write_text(json.dumps(status,indent=2)+'\n')
    # Publication is independent of data collection. An expired write token may
    # delay uploads, but cannot destroy local evidence or stop healthy research.
    days = sorted(p.stem for p in (Path('/root/trader-evidence')/'reports').glob('*.json'))
    if days:
        published = command(python,'scripts/session_ops.py','publish','--date',days[-1],cwd=root,env=env,check=False)
        if published.returncode:
            print('UPLOAD_AUTH_REQUIRED: local archives are preserved. Configure GitHub write access for the session-artifacts branch. Source fetch and collection do not need a PAT.',flush=True)
    print('RECOVERY_INSTALLED: next scheduled preflight controls paper observations; broker entries remain suspended.',flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'RECOVERY_STOPPED {type(exc).__name__}: {exc}',file=sys.stderr)
        print('No success is claimed. Once maintenance begins, session cron remains paused on failure. The original checkout and backups are retained.',file=sys.stderr)
        raise SystemExit(1)
