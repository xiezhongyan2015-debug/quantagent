"""Independent heartbeat watchdog (task QuantDeadman, runs as SYSTEM every 10 min, no logon needed). Reads files only, pushes Feishu directly.
Catches what the in-session tasks cannot report about themselves:
  Administrator logged off (every interactive task stops) | the 1-minute alert dispatcher stalled | the intraday monitor stalled
  the 16:30 pipeline never started | no released + armed target for the next trading day by 20:30 | disk space low.
Repeats an unresolved problem at most hourly in working hours (every 6 h otherwise). State: live/deadman_state.json."""
import sys, os, json, shutil, subprocess, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import send
from execution_status import day_runs
LIVE = Path(os.environ.get('QD_LIVE', 'C:/ChoiceBridge/live')); RUNS = Path(os.environ.get('QD_RUNS', 'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs'))
QUANT = Path(os.environ.get('QD_QUANT', 'C:/ChoiceBridge/quant/current')); HEALTH = Path(os.environ.get('QD_HEALTH', 'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/corporate_actions/health_last_seen.json'))
def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
now = dt.datetime.fromisoformat(os.environ['QD_NOW']) if os.environ.get('QD_NOW') else dt.datetime.now()
T = now.strftime('%Y-%m-%d'); hm = now.strftime('%H:%M'); cal = load(QUANT / '实盘日更/config/calendar_2026.json'); trading = T in cal
nxt = next((d for d in cal if d > T), None)
state_p = LIVE / 'deadman_state.json'; state = load(state_p) if state_p.exists() else {}
problems = []
def age_min(iso):
    t = dt.datetime.fromisoformat(iso); t = t.replace(tzinfo=None) if t.tzinfo is None else t.astimezone(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)
    return (now - t).total_seconds() / 60
# 1) Administrator interactive session (all Quant* tasks except this one need it)
if os.environ.get('QD_SESSIONS') is not None: sessions = os.environ['QD_SESSIONS']
else:
    r = subprocess.run(['query', 'user'], capture_output=True); sessions = (r.stdout + r.stderr).decode('mbcs', errors='replace')
if 'administrator' not in sessions.lower():
    problems.append(('logon', 'Administrator 未登录：盘中执行、监控、日更流水线和告警都不会运行。请远程桌面登录一次（之后断开即可，不要注销）'))
# 2) the one-minute alert dispatcher writes health_last_seen.json every run
try: a = age_min(load(HEALTH)['at'])
except Exception: a = None
if a is None or a > 15: problems.append(('dispatch', f'每分钟告警任务已 {"未知" if a is None else f"{a:.0f} 分钟"}没有运行，其他告警可能发不出来'))
# 3) intraday monitor heartbeat
if trading and '09:35' <= hm < '15:00':
    try: m = load(LIVE / 'monitor_state.json'); ma = age_min(m['at']) if m.get('day') == T else None
    except Exception: ma = None
    if ma is None or ma > 15: problems.append(('monitor', '盘中监控任务（QuantMonitor）超过 15 分钟没有运行'))
# 4) pipeline started, next day released and armed
if trading and hm >= '16:50' and not (LIVE / f'logs/pipeline_{T}.log').exists():
    problems.append(('pipeline_start', '16:30 日更流水线没有启动（无当日日志），明天可能没有目标'))
if trading and hm >= '20:30' and nxt:
    if not (LIVE / f'release_receipt_{nxt}.json').exists(): problems.append(('release', f'{nxt} 的目标没有发布，明天执行器不会下单'))
    else:
        armed = [r for r in day_runs(RUNS, nxt) if r['status'] == 'scheduled_waiting']
        try:
            import psutil; armed = [r for r in armed if r.get('pid') and psutil.pid_exists(int(r['pid']))]
        except ImportError: pass
        if not armed: problems.append(('arm', f'{nxt} 的目标已发布，但执行器没有处于预约等待状态，明天不会自动交易'))
# 5) disk
free = shutil.disk_usage(os.environ.get('QD_DISK', 'C:/')).free / 1e9
if free < 3: problems.append(('disk', f'C 盘只剩 {free:.1f} GB'))
LABEL = dict(logon='Administrator 登录', dispatch='每分钟告警任务', monitor='盘中监控', pipeline_start='日更流水线启动', release='明日目标发布', arm='执行器预约', disk='磁盘空间')
work = trading and '08:30' <= hm <= '21:30'; gap = 60 if work else 360
active = {k for k, _ in problems}
for k, text in problems:
    last = state.get(k)
    if last and age_min(last) < gap: continue
    msg = f'🚨 {T} {hm} 云机看门狗：{text}'
    if send(msg): state[k] = now.isoformat(); print('sent', msg)
    else: print('send failed', msg)
for k in [k for k in state if k not in active]:
    if send(f'✅ {T} {hm} 云机看门狗：{LABEL.get(k, k)}已恢复'): state.pop(k)
LIVE.mkdir(parents=True, exist_ok=True); state_p.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
print(json.dumps(dict(at=now.isoformat(), problems=sorted(active), free_gb=round(free, 1)), ensure_ascii=False))
