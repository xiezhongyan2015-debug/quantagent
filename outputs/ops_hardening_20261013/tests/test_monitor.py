"""Scenario replay for tools/monitor.py with synthetic run folders (no network, fake psutil)."""
import json, os, subprocess, sys, tempfile, shutil
from pathlib import Path
TOOLS = Path(__file__).resolve().parents[3] / '实盘日更/tools'; HERE = Path(__file__).resolve().parent
PY = sys.executable; D = '2026-10-13'
root = Path(tempfile.mkdtemp()); live = root / 'live'; runs = root / 'runs' / D; quant = root / 'quant/实盘日更/config'; outbox = root / 'outbox'
for p in (live, runs, quant): p.mkdir(parents=True)
shutil.copy(TOOLS.parent / 'config/calendar_2026.json', quant)
(live / f'release_receipt_{D}.json').write_text('{}')
def w(run, name, obj): (runs / run).mkdir(exist_ok=True); (runs / run / name).write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
def status(run, st, reason=None, at='x', **kw): w(run, 'status_redacted.json', dict(at=f'{D}T{at}:00+08:00', status=st, reason=reason, **kw))
def ledger(run, n, filled=True): w(run, 'private_state.json', dict(day=D, attempts=[dict(state='4', filled=100 if filled else 0) for _ in range(n)], baseline=dict(positions={})))
def poll(t, alive=''):
    env = dict(os.environ, QD_NOW=f'{D}T{t}:00', QD_LIVE=str(live), QD_RUNS=str(root / 'runs'), QD_QUANT=str(root / 'quant'), QD_NOTIFY='/nonexistent', QD_OUTBOX=str(outbox), ALIVE_PIDS=alive, PYTHONPATH=str(HERE / 'fakepsutil'))
    out = subprocess.run([PY, str(TOOLS / 'monitor.py')], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return [l for l in out.stdout.splitlines() if l.startswith(('⚠️', 'ℹ️', '🔔'))]
fails = []
def check(name, got, want_sub=None, none=False):
    ok = (not got) if none else any(want_sub in g for g in got)
    print(('PASS' if ok else 'FAIL'), name, got); fails.append(name) if not ok else None
# armed run waiting, alive
w('190004_a', 'process.json', dict(pid=11)); status('190004_a', 'scheduled_waiting', at='19:00')
check('09:20 armed alive -> silent', poll('09:20', '11'), none=True)
check('09:22 armed process gone -> alert', poll('09:22', ''), '预约的执行器进程已不在')
check('09:24 not repeated', poll('09:24', ''), none=True)
# stop at 09:31 with the executor's own outbox notice -> no duplicate
ledger('190004_a', 0); status('190004_a', 'stopped_review_required', '连续90秒账户核对未通过', at='09:31')
outbox.mkdir(exist_ok=True); (outbox / 'x.json').write_text(json.dumps(dict(kind='executor_stopped', text=f'⚠️ {D} 执行器停止需审核')), encoding='utf-8')
check('09:32 stop already reported by executor -> silent', poll('09:32', ''), none=True)
(outbox / 'x.json').unlink(); live.joinpath('monitor_state.json').unlink()
w('190004_a', 'process.json', dict(pid=11))
check('09:32 stop without executor notice -> alert', [m for m in poll('09:32', '') if '停止' in m], '停止，需人工审核')
# resumed run trades
w('102604_b', 'process.json', dict(pid=22)); ledger('102604_b', 3); status('102604_b', 'trading', at='10:28')
check('10:28 first order', poll('10:28', '22'), '已开始下单')
status('102604_b', 'reconciling', '持仓数量与精确关联成交暂不一致', at='10:30')
check('10:30 reconciling short -> silent', poll('10:30', '22'), none=True)
check('10:34 reconciling 4 min -> silent', poll('10:34', '22'), none=True)
check('10:36 reconciling 6 min -> alert', poll('10:36', '22'), '连续约 6 分钟')
check('10:38 not repeated', poll('10:38', '22'), none=True)
status('102604_b', 'trading', at='10:40'); poll('10:40', '22')
status('102604_b', 'session_paused', at='11:30')
check('11:32 midday summary only', poll('11:32', '22'), '午间小结')
check('12:00 lunch pause silent', poll('12:00', '22'), none=True)
status('102604_b', 'trading', at='13:05'); poll('13:06', '22')
check('13:30 process died mid-session -> alert', poll('13:30', ''), '进程已退出')
check('13:32 not repeated', poll('13:32', ''), none=True)
check('15:06 close summary', poll('15:06', ''), '收盘')
check('15:08 once', poll('15:08', ''), none=True)
queued = len(list(outbox.glob('*.json'))); print('queued messages', queued)
shutil.rmtree(root)
print('FAILED' if fails else 'ALL PASS', fails); sys.exit(1 if fails else 0)
