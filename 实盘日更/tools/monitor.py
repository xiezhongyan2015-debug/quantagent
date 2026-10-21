"""Intraday watchdog (QuantMonitor, every 2 min 09:20-15:20 on trading days). Reads only files (run folders, release receipts, outbox).
Pushes at once (⚠️): stop needing review / login needed / executor process gone / no armed run by 09:25 / reconciliation or recovery stuck >= 6 min.
Pushes once a day (ℹ️): first order, all targets done, 11:30 morning summary, 15:05 close summary. Lunch pause and short reconciling are not pushed.
Messages go through notify.send_or_queue, so a failed push is retried by QuantAlertDispatch. State: live/monitor_state.json."""
import sys, os, json, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import send_or_queue, OUTBOX
from execution_status import day_runs, main_run, summarize_day, lines as exec_lines, cn
LIVE = Path(os.environ.get('QD_LIVE', 'C:/ChoiceBridge/live')); RUNS = Path(os.environ.get('QD_RUNS', 'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs')); QUANT = Path(os.environ.get('QD_QUANT', 'C:/ChoiceBridge/quant/current'))
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
now = dt.datetime.fromisoformat(os.environ['QD_NOW']) if os.environ.get('QD_NOW') else dt.datetime.now()
T = now.strftime('%Y-%m-%d'); hhmm = now.strftime('%H:%M')
if T not in load(QUANT / '实盘日更/config/calendar_2026.json'): sys.exit(0)
state_p = LIVE / 'monitor_state.json'; prev = load(state_p) if state_p.exists() else {}
if prev.get('day') != T: prev = dict(day=T)
BAD = {'stopped_review_required', 'login_required', 'waiting_login', 'position_floor_unmet', 'stopped'}
FINAL = {'completed_targets', 'finished', 'stopped', 'closed', 'completed_pilot', 'stopped_review_required', 'handed_off'}
PREOPEN = {'scheduled_waiting', 'waiting_open', 'starting', 'preparing'}
STUCK = {'reconciling', 'reconciling_corporate_action', 'recovering_readonly', 'recovering_submission'}
def alive(r):
    try:
        import psutil; return psutil.pid_exists(int(r['pid'])) if r.get('pid') else False
    except Exception: return None
runs = day_runs(RUNS, T)
for r in runs: r['alive'] = alive(r)
run = main_run(runs) or next((r for r in sorted(runs, key=lambda r: r['at'] or '', reverse=True) if r['alive']), None) or (max(runs, key=lambda r: r['at'] or '') if runs else None)
status = run['status'] if run else None; reason = ((run or {}).get('reason') or '')[:120]
attempts = ((run or {}).get('state') or {}).get('attempts', []) if run else []
orders = len(attempts); fills = sum(1 for a in attempts if a.get('filled')); pid_alive = run['alive'] if run else None
release = (LIVE / f'release_receipt_{T}.json').exists()
msgs = []
def say(kind, text): msgs.append((kind, text))
def executor_already_reported():
    """The executor queues its own stop notice; avoid a second message for the same stop."""
    try: return any(load(p).get('kind') == 'executor_stopped' and T in load(p).get('text', '') for p in OUTBOX.glob('*.json'))
    except Exception: return False
if release and not runs and hhmm >= '09:25' and not prev.get('no_run_sent'):
    say('no_run', f'⚠️ {T} {hhmm} 今日有发布目标但没有执行器运行记录，不会下单，请人工检查'); prev['no_run_sent'] = True
if run and status in PREOPEN and pid_alive is False and hhmm >= '09:20' and not prev.get('armed_dead_sent'):
    say('armed_dead', f'⚠️ {T} {hhmm} 预约的执行器进程已不在（状态{cn(status)}），今天不会自动交易，请人工检查'); prev['armed_dead_sent'] = True
if status in BAD and prev.get('status') != status:
    if not (status == 'stopped_review_required' and executor_already_reported()):
        say('bad', f'⚠️ {T} {hhmm} 执行器{cn(status)}' + (f'：{reason}' if reason else '') + f'（委托 {orders}，成交 {fills}）')
if run and pid_alive is False and status not in FINAL | PREOPEN and prev.get('pid_alive') is not False and hhmm < '15:05':
    say('exited', f'⚠️ {T} {hhmm} 执行器进程已退出，最后状态{cn(status)}' + (f'：{reason}' if reason else '') + f'（委托 {orders}，成交 {fills}），今天不会再自动下单，请人工检查')
if status in STUCK and pid_alive is not False:
    since = prev.get('stuck_since') or now.isoformat(); prev['stuck_since'] = since
    minutes = (now - dt.datetime.fromisoformat(since)).total_seconds() / 60
    if minutes >= 6 and not prev.get('stuck_sent'):
        say('stuck', f'⚠️ {T} {hhmm} 执行器已连续约 {minutes:.0f} 分钟处于“{cn(status)}”' + (f'：{reason}' if reason else '') + f'；期间不新增委托，若无法自动恢复会停机'); prev['stuck_sent'] = True
else: prev['stuck_since'] = None; prev['stuck_sent'] = False
if orders and not prev.get('first_order_sent'):
    say('first_order', f'ℹ️ {T} {hhmm} 已开始下单（委托 {orders}，成交 {fills}），状态{cn(status)}'); prev['first_order_sent'] = True
if status == 'completed_targets' and not prev.get('completed_sent'):
    say('completed', f'ℹ️ {T} {hhmm} 今日目标已全部完成（委托 {orders}，成交 {fills}）'); prev['completed_sent'] = True
def detail():
    try: return '\n'.join(exec_lines(summarize_day(RUNS, T)))
    except Exception as e: return f'执行汇总读取失败：{type(e).__name__}'
if '11:31' <= hhmm < '13:00' and runs and not prev.get('midday_sent'):
    say('midday', f'ℹ️ {T} 午间小结：执行器{cn(status)}，委托 {orders}，成交 {fills}\n{detail()}'); prev['midday_sent'] = True
if hhmm >= '15:05' and not prev.get('closed'):
    say('close', f'🔔 {T} 收盘：执行器{cn(status)}，委托 {orders}，成交 {fills}' + (f'，原因 {reason}' if status in BAD else '') + f'\n{detail()}'); prev['closed'] = True
for kind, text in msgs:
    print(text, send_or_queue(text, 'monitor_' + kind, f'monitor:{T}:{kind}:{status}:{hhmm}'))
prev.update(status=status, orders=orders, fills=fills, pid_alive=pid_alive, at=now.isoformat(), day=T)
LIVE.mkdir(parents=True, exist_ok=True); state_p.write_text(json.dumps(prev, ensure_ascii=False), encoding='utf-8')
