"""Recovery keeps cron paused on failed gates and leaves the old code intact."""
import importlib.util
import json
from datetime import datetime
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location('recovery_installer',Path(__file__).resolve().parents[1]/'scripts/ops/recover.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def test_cron_migration_handles_legacy_jobs_and_preserves_unrelated_jobs():
    cron = 'MAILTO=root\n0 2 * * * /root/backup.sh\n20 9 * * 1-5 /root/preflight_session.sh\n'
    changed = r.replace_cron(cron,['new job'])
    assert '/root/backup.sh' in changed and 'preflight_session.sh' not in changed
    assert changed.count(r.BEGIN) == 1 and changed.count(r.END) == 1
    with pytest.raises(ValueError): r.replace_cron(r.BEGIN+'\n',[])


def test_recovery_policy_preserves_thresholds_and_blocks_all_broker_entries():
    values=r.settings_env({'ALPACA_API_KEY':'PKprivate','PAPER_SCALED_PREMIUM_BUDGET_DOLLARS':'250',
                          'LIVE_TRADING_ENABLED':'true','OPTIONS_DATA_PROVIDER':'tradier'})
    assert values['LIVE_TRADING_ENABLED']=='false' and values['OPTIONS_DATA_PROVIDER']=='alpaca'
    assert values['PAPER_SCALED_SHADOW_ONLY_STRATEGIES']=='vwap_reclaim,orb'
    assert values['PAPER_SCALED_PREMIUM_BUDGET_DOLLARS']=='250'
    assert values['ALPACA_API_KEY']=='PKprivate'


@pytest.mark.parametrize('fail_at',[None,'verify-flat','check-options-data','test-alert','preflight'])
def test_recovery_installs_schedule_only_after_checks(tmp_path,monkeypatch,fail_at):
    base=tmp_path/'root';old=base/'trader';source=tmp_path/'source'
    (old/'.git').mkdir(parents=True);(source/'.git').mkdir(parents=True)
    (old/'.venv/bin').mkdir(parents=True);(old/'logs').mkdir();(old/'evaluation/reports').mkdir(parents=True)
    (old/'.env').write_text('ALPACA_API_KEY=PKprivate\nALPACA_SECRET_KEY=private\nOPS_NTFY_URL=https://example.test/private\n')
    (old/'evaluation/reports/2026-10-23.json').write_text('{}')
    (old/'logs/example.log').write_text('historical evidence')
    with sqlite3.connect(old/'trading.db') as db:
        db.execute('create table audit (x integer)');db.execute('insert into audit values (42)')
    original='0 2 * * * /root/backup.sh\n20 9 * * 1-5 /root/preflight_session.sh\n'
    state={'cron':original};calls=[]
    def mapped(p):
        value=str(p)
        if value=='/root' or value.startswith('/root/'):
            return base/value.removeprefix('/root').lstrip('/')
        return Path(p)
    monkeypatch.setattr(r,'Path',mapped)
    monkeypatch.setattr(r.os,'geteuid',lambda:0)
    monkeypatch.setattr(r,'datetime',SimpleNamespace(now=lambda tz:datetime(2026,10,24,14,0,tzinfo=tz)))
    monkeypatch.setattr(sys,'argv',['recover.py','--release','b'*40,'--source',str(source)])
    def fake(*args,check=True,cwd=None,env=None,input=None):
        calls.append(args);rc=0;out=''
        if args[0]=='timedatectl':out='America/New_York\n'
        elif args[0]=='pgrep':rc=1
        elif args[0]=='crontab':
            if args[1]=='-l':out=state['cron']
            else:state['cron']=input
        elif args[0]=='git':
            if args[1]=='rev-parse':out='a'*40
            if args[1]=='clone':shutil.copytree(source,Path(args[-1]))
        elif '-m' in args and 'pytest' in args:
            assert not (base/'trader-release/.env').exists()
            assert (base/'trader-release/KILL_SWITCH').is_file()
            assert 'ALPACA_API_KEY' not in env
            out='1060 passed, 2 skipped\n'
        elif 'scripts/session_ops.py' in args:
            operation=args[2]
            if operation==fail_at:rc=1
            if operation=='migrate':
                shutil.copytree(Path(args[-1]),base/'trader-evidence')
        elif args[0]=='bash' and fail_at=='preflight':rc=1
        result=subprocess.CompletedProcess(args,rc,stdout=out,stderr='')
        if rc and check:raise RuntimeError('simulated gate failure')
        return result
    monkeypatch.setattr(r,'command',fake)
    monkeypatch.setattr(r,'restart_dashboard',lambda root,env:True)
    if fail_at:
        with pytest.raises((ValueError,RuntimeError)):r.main()
        assert 'Paused until recovery checks pass' in state['cron']
        assert 'watchdog.sh' not in state['cron']
    else:
        r.main()
        assert 'watchdog.sh' in state['cron'] and '/root/backup.sh' in state['cron']
        assert json.loads((base/'trader-recovery-status.json').read_text())['broker_entry_strategies']==[]
    assert (old/'.env').read_text().startswith('ALPACA_API_KEY=PKprivate')
    assert (old/'logs/example.log').read_text()=='historical evidence'
    backup=next((base/'trader-backups').iterdir())
    with sqlite3.connect(backup/'trading.db') as db:
        assert db.execute('select x from audit').fetchone()==(42,)
    assert (old/'KILL_SWITCH').exists()


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def make_release(tmp_path):
    source = tmp_path/'source'; source.mkdir()
    git(source,'init','-b',r.BRANCH)
    git(source,'config','user.name','Test');git(source,'config','user.email','test@example.test')
    (source/'.gitignore').write_text('KILL_SWITCH\n.env\nlogs/\n.venv/\n')
    (source/'code.txt').write_text('original')
    git(source,'add','.');git(source,'commit','-m','original')
    root = tmp_path/'installed'
    subprocess.run(['git','clone','--quiet',str(source),str(root)],check=True)
    old = git(root,'rev-parse','HEAD')
    (root/'KILL_SWITCH').touch()
    (root/'.env').write_text('private host configuration')
    (source/'code.txt').write_text('repaired')
    git(source,'add','code.txt');git(source,'commit','-m','repair')
    new = git(source,'rev-parse','HEAD')
    backup = tmp_path/'backup';backup.mkdir()
    return source,root,old,new,backup


def test_retry_advances_failed_release_without_disarming_or_changing_host_env(tmp_path,monkeypatch):
    source,root,old,new,backup = make_release(tmp_path)
    monkeypatch.setattr(r,'COMMAND_LOG_DIR',None)
    r.install_release(root,source,new,backup)
    assert git(root,'rev-parse','HEAD')==new
    assert (backup/'prior-release-head.txt').read_text().strip()==old
    assert (root/'KILL_SWITCH').exists()
    assert (root/'.env').read_text()=='private host configuration'
    r.install_release(root,source,new,backup)
    assert (root/'KILL_SWITCH').exists()


@pytest.mark.parametrize('condition',['dirty','untracked','wrong_branch','unrelated'])
def test_retry_refuses_to_overwrite_operator_changes_or_unrelated_code(tmp_path,monkeypatch,condition):
    source,root,old,new,backup = make_release(tmp_path)
    monkeypatch.setattr(r,'COMMAND_LOG_DIR',None)
    if condition=='dirty': (root/'code.txt').write_text('operator edit')
    elif condition=='untracked': (root/'operator.py').write_text('preserve me')
    elif condition=='wrong_branch': git(root,'switch','-c','operator-work')
    else:
        git(source,'checkout','--orphan','unrelated')
        git(source,'commit','-m','unrelated history')
        new=git(source,'rev-parse','HEAD')
    with pytest.raises(ValueError):r.install_release(root,source,new,backup)
    assert git(root,'rev-parse','HEAD')==old
    assert (root/'KILL_SWITCH').exists()
    assert (root/'.env').read_text()=='private host configuration'
    if condition=='dirty':assert (root/'code.txt').read_text()=='operator edit'
    if condition=='untracked':assert (root/'operator.py').read_text()=='preserve me'


def test_retry_accepts_its_expected_venv_symlink_before_ignore_rule_upgrade(tmp_path,monkeypatch):
    source,root,old,new,backup=make_release(tmp_path)
    expected=tmp_path/'original-venv';expected.mkdir()
    (root/'.venv').symlink_to(expected,target_is_directory=True)
    assert git(root,'status','--porcelain')=='?? .venv'
    with (source/'.gitignore').open('a') as f:f.write('.venv\n')
    git(source,'add','.gitignore');git(source,'commit','-m','ignore environment symlink')
    new=git(source,'rev-parse','HEAD')
    monkeypatch.setattr(r,'COMMAND_LOG_DIR',None)
    r.install_release(root,source,new,backup,venv_target=expected)
    assert git(root,'status','--porcelain')==''
    assert (root/'.venv').is_symlink() and (root/'.venv').resolve()==expected
    assert (root/'KILL_SWITCH').exists()
    assert (root/'.env').read_text()=='private host configuration'


@pytest.mark.parametrize('kind',['wrong_target','broken_link','regular_file','other_source'])
def test_retry_does_not_hide_other_files_as_its_environment_link(tmp_path,monkeypatch,kind):
    source,root,old,new,backup=make_release(tmp_path)
    expected=tmp_path/'original-venv';expected.mkdir()
    if kind=='regular_file':(root/'.venv').write_text('preserve me')
    elif kind=='wrong_target':
        other=tmp_path/'different-venv';other.mkdir()
        (root/'.venv').symlink_to(other,target_is_directory=True)
    elif kind=='broken_link':(root/'.venv').symlink_to(tmp_path/'missing')
    else:
        (root/'.venv').symlink_to(expected,target_is_directory=True)
        (root/'operator.py').write_text('preserve me')
    monkeypatch.setattr(r,'COMMAND_LOG_DIR',None)
    with pytest.raises(ValueError):r.install_release(root,source,new,backup,venv_target=expected)
    assert git(root,'rev-parse','HEAD')==old
    assert (root/'KILL_SWITCH').exists()
    if kind=='regular_file':assert (root/'.venv').read_text()=='preserve me'
    if kind=='other_source':assert (root/'operator.py').read_text()=='preserve me'
