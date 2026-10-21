"""Automatic resume of benign pre-trade executor stops (task QuantAutoResume, every 10 min 09:36-13:36 on trading days). 2026-10-16.

When today's latest executor run is 'stopped_review_required', has placed NO orders, the stop reason matches the benign
list below, it is before 13:30, no other run is alive, and fewer than MAX attempts were made today, this does exactly what the
operator did on 10-15 and 10-16: re-release the SAME sealed target with fresh evidence (release_day.py), review the stale
lock, and arm (= Start).  Every decision is pushed to the alert channel.  Unknown reasons or stops after orders were placed
are left for a human (one message, then silence).  State: live/auto_resume_state.json.  --dry-run prints the decision only.
Benign = the stop came from evidence/reconciliation timing, not from a trading anomaly:
  账户核对未通过 | 公司行动 | 成分 | 证据 | 快照 | 不一致 | 行情查询 | 行情不是今日 | 登录恢复
"""
import sys, os, json, re, subprocess, hashlib, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import send_or_queue
from execution_status import day_runs
LIVE = Path(os.environ.get('QD_LIVE', 'C:/ChoiceBridge/live')); RUNS = Path(os.environ.get('QD_RUNS', 'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs'))
QUANT = Path(os.environ.get('QD_QUANT', 'C:/ChoiceBridge/quant/current')); EXE = os.environ.get('QD_EXE', 'C:/ChoiceBridge/runtime/choice-venv/Scripts/python.exe')
STAGING = Path(os.environ.get('QD_STAGING', 'C:/ChoiceBridge/staging')); EXECUTOR = Path(os.environ.get('QD_EXECUTOR', 'C:/ChoiceBridge/executor'))
BENIGN = re.compile('账户核对未通过|公司行动|成分|证据|快照|不一致|行情查询|行情不是今日|登录恢复')
MAX = 2; DRY = '--dry-run' in sys.argv
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
now = dt.datetime.fromisoformat(os.environ['QD_NOW']) if os.environ.get('QD_NOW') else dt.datetime.now()
T = now.strftime('%Y-%m-%d'); hm = now.strftime('%H:%M')
def out(msg, push=True):
    print(msg)
    if push and not DRY:   # 2026-10-19: was send_or_queue(msg, channel='alert') -> TypeError before release/arm; never let a push failure stop the resume
        try: print('push', send_or_queue(msg, 'auto_resume', f'auto_resume:{T}:{hm}:' + hashlib.sha256(msg.encode()).hexdigest()[:16]))
        except Exception as e: print('push failed', type(e).__name__, e)
if T not in load(QUANT / '实盘日更/config/calendar_2026.json'): sys.exit(0)
state_p = LIVE / 'auto_resume_state.json'; state = load(state_p) if state_p.exists() else {}
if state.get('day') != T: state = dict(day=T, attempts=[], reported=[])
runs = day_runs(RUNS, T)
if not runs: print('no runs today'); sys.exit(0)
try:
    import psutil
    for r in runs: r['alive'] = bool(r.get('pid')) and psutil.pid_exists(int(r['pid']))
except ImportError:
    for r in runs: r['alive'] = None
latest = max(runs, key=lambda r: r['started_at'] or r['at'] or '')
FINAL = {'completed_targets', 'finished', 'stopped', 'closed', 'completed_pilot', 'stopped_review_required', 'handed_off'}
if any(r['alive'] and r['status'] not in FINAL for r in runs): print('an executor run is alive:', [(r['name'], r['status']) for r in runs if r['alive']]); sys.exit(0)
if latest['status'] != 'stopped_review_required': print('latest run status', latest['status'], '- nothing to do'); sys.exit(0)
reason = latest['reason'] or ''; key = latest['name']
if key in state['reported'] or key in [a['run'] for a in state['attempts']]: print('already handled', key); sys.exit(0)
def report(text):
    state['reported'].append(key); out(text)
    if not DRY: state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
if latest['attempts'] > 0: report(f'⚠️ {T} {hm} 执行器停止（已下 {latest["attempts"]} 笔委托）：{reason[:100]}。下单后的停机不自动恢复，需人工核对账本。'); sys.exit(0)
if not BENIGN.search(reason): report(f'⚠️ {T} {hm} 执行器停止，原因不在自动恢复清单：{reason[:120]}。需人工处理。'); sys.exit(0)
if hm >= '13:30': report(f'⚠️ {T} {hm} 执行器停止（{reason[:80]}），已过 13:30 不再自动恢复。'); sys.exit(0)
if len(state['attempts']) >= MAX: report(f'⚠️ {T} {hm} 执行器再次停止（{reason[:80]}），今天已自动恢复 {MAX} 次，不再重试，需人工处理。'); sys.exit(0)
active = load(LIVE / 'active_day.json')
if active.get('execution_date') != T: report(f'⚠️ {T} {hm} active_day.json 不是今天（{active.get("execution_date")}），不自动恢复。'); sys.exit(0)
S = active['signal_date']; t8 = active['pcf_trading_day']
target = QUANT / f'实盘日更/outputs/targets/{T}/target_{T}.json'; account = LIVE / f'captain_snapshot_{S}.json'; pcf = QUANT / f'实盘日更/inputs/pcf/563030_{t8}.xml'
missing = [str(p) for p in (target, account, pcf) if not p.exists()]
if missing: report(f'⚠️ {T} {hm} 自动恢复缺少输入：{missing}'); sys.exit(0)
attempt = dict(at=now.isoformat(), run=key, reason=reason[:160], steps=[])
out(f'🔁 {T} {hm} 执行器停止（{reason[:90]}），未下单，原因属良性，自动恢复第 {len(state["attempts"]) + 1}/{MAX} 次：重新发布同一目标并启动。')
if DRY: print('DRY RUN: would run release_day / clear_stale_lock / arm'); sys.exit(0)
def run(name, cmd, cwd):
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
    attempt['steps'].append(dict(step=name, rc=r.returncode, out=(r.stdout + r.stderr)[-400:])); return r.returncode, (r.stdout + r.stderr)
rc, o = run('release', [EXE, str(STAGING / 'release_day.py'), '--signal', S, '--exec', T, '--target', str(target), '--account', str(account), '--pcf', str(pcf)], EXECUTOR)
ok = rc == 0
if ok: rc, o = run('lock', [EXE, '-B', str(STAGING / 'clear_stale_lock.py')], EXECUTOR); ok = rc == 0
if ok:
    rc, o = run('arm', [EXE, '-B', str(STAGING / 'arm.py')], EXECUTOR)
    ok = '"action": "started"' in o or '"action": "scheduled"' in o or rc == 0
state['attempts'].append(attempt); state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
out(('✅' if ok else '⚠️') + f' {T} {now.strftime("%H:%M")} 自动恢复{"已启动执行器" if ok else "失败"}：' + '; '.join(f'{s["step"]} rc={s["rc"]}' for s in attempt['steps']) + ('' if ok else '。需人工处理。'))
