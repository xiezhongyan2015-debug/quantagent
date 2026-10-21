"""Pre-open preflight (task QuantPreflight at 09:05 and 09:18 on trading days). 2026-10-16. Files only, plus one re-arm if needed.
Checks: today's release receipt + target + PCF present | an armed run (scheduled_waiting) whose process is alive, else re-arm
(clear_stale_lock + arm) | expected dividend cash today (announced events x record-date holdings) so the
09:30 reconciliation has a known expectation | disk free.  Pushes one ℹ️ line (09:18 run) or ⚠️ lines for problems.
State: live/preflight_<day>.json."""
import sys, os, json, shutil, subprocess, hashlib, datetime as dt
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import send_or_queue
from execution_status import day_runs
LIVE = Path(os.environ.get('QD_LIVE', 'C:/ChoiceBridge/live')); RUNS = Path(os.environ.get('QD_RUNS', 'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs'))
QUANT = Path(os.environ.get('QD_QUANT', 'C:/ChoiceBridge/quant/current')); EXE = os.environ.get('QD_EXE', 'C:/ChoiceBridge/runtime/choice-venv/Scripts/python.exe')
STAGING = Path(os.environ.get('QD_STAGING', 'C:/ChoiceBridge/staging')); EXECUTOR = Path(os.environ.get('QD_EXECUTOR', 'C:/ChoiceBridge/executor'))
CA = Path(os.environ.get('QD_CA', 'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/corporate_actions')); DRY = '--dry-run' in sys.argv
def load(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
now = dt.datetime.fromisoformat(os.environ['QD_NOW']) if os.environ.get('QD_NOW') else dt.datetime.now(); T = now.strftime('%Y-%m-%d'); hm = now.strftime('%H:%M')
if T not in load(QUANT / '实盘日更/config/calendar_2026.json'): sys.exit(0)
problems = []; info = []
# 1) release, target, PCF
active = load(LIVE / 'active_day.json') if (LIVE / 'active_day.json').exists() else {}
if active.get('execution_date') != T: problems.append(f'active_day.json 不是今天（{active.get("execution_date")}）：没有今日发布')
else:
    for p in [LIVE / f'release_receipt_{T}.json', QUANT / f'实盘日更/outputs/targets/{T}/target_{T}.json', QUANT / f'实盘日更/inputs/pcf/563030_{active["pcf_trading_day"]}.xml']:
        if not p.exists(): problems.append(f'缺文件 {p.name}')
# 2) armed run alive
runs = day_runs(RUNS, T)
try:
    import psutil
    for r in runs: r['alive'] = bool(r.get('pid')) and psutil.pid_exists(int(r['pid']))
except ImportError:
    for r in runs: r['alive'] = None
armed = [r for r in runs if r['status'] in ('scheduled_waiting', 'waiting_open', 'starting') and r['alive']]
rearm = None
if not armed and not problems and hm < '09:28':
    if DRY: rearm = 'DRY: would re-arm'
    else:
        try:
            l = subprocess.run([EXE, '-B', str(STAGING / 'clear_stale_lock.py')], cwd=str(EXECUTOR), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
            a = subprocess.run([EXE, '-B', str(STAGING / 'arm.py')], cwd=str(EXECUTOR), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
            rearm = f'lock rc={l.returncode}, arm rc={a.returncode}: ' + (a.stdout + a.stderr)[-200:].replace('\n', ' ')
        except Exception as e: rearm = f'{type(e).__name__}: {str(e)[:120]}'
    problems.append('09:30 前没有存活的预约进程，已尝试重新预约：' + str(rearm))
# 3) (Choice.exe is not required: the executor trades through the authorised HTTP session; verified 2026-10-16 while trading)
# 4) expected dividend cash today
try:
    cache = load(CA / f'cache/{T}.json'); total = D(0); items = []
    for e in cache.get('events', []):
        if e.get('pay_date') != T or not D(str(e.get('cash_per_share', 0))): continue
        rec = e.get('record_date'); closes = CA / f'closes/{rec}.json'
        if not closes.exists(): items.append(f'{e["code"]} 登记日 {rec} 无持仓快照'); continue
        held = int(load(closes)['positions'].get(e['code'], 0))
        if held: amt = (D(str(e['cash_per_share'])) * held).quantize(D('.01')); total += amt; items.append(f'{e["code"]} {held}股×{e["cash_per_share"]}={amt}')
    info.append(f'今日预期分红到账 {total} 元' + ('（' + '；'.join(items) + '）' if items else '')) if items else info.append('今日无预期分红到账')
except Exception as e: info.append(f'分红预期不可用：{type(e).__name__}')
# 5) disk
free = shutil.disk_usage(os.environ.get('QD_DISK', 'C:/')).free / 1e9
if free < 5: problems.append(f'C 盘只剩 {free:.1f} GB')
state = dict(at=now.isoformat(), problems=problems, info=info, armed=[r['name'] for r in armed], rearm=rearm)
if not DRY: (LIVE / f'preflight_{T}.json').write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
if problems: msg = f'⚠️ {T} {hm} 盘前检查：' + '；'.join(problems)
elif hm >= '09:15': msg = f'ℹ️ {T} {hm} 盘前检查通过：已预约 {armed[0]["name"] if armed else "-"}；' + '；'.join(info) + f'；磁盘 {free:.0f} GB'
else: msg = None
print(json.dumps(state, ensure_ascii=False))
if msg and not DRY:   # 2026-10-19: was send_or_queue(msg, channel='alert') -> TypeError (notify contract is text, kind, key)
    try: print('push', send_or_queue(msg, 'preflight', f'preflight:{T}:{hm}:' + hashlib.sha256(msg.encode()).hexdigest()[:16]))
    except Exception as e: print('push failed', type(e).__name__, e)
