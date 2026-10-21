"""Cloud resource watch (task QuantResourceWatch, SYSTEM, every 2 min). 2026-10-17. No third-party packages.
Samples physical memory (GlobalMemoryStatusEx), commit charge and C: free space; appends one row to live/resource_log.csv (14 days kept).
Alerts on the private channel (notify.send -> alert_feishu_webhook):
  memory warning  : available < 1.2 GB on two consecutive samples (about 4 min)
  memory critical : available < 0.6 GB or commit > 92% of the commit limit (immediately)
  disk warning    : C: free < 8 GB ; disk critical : < 4 GB
Each alert names the five largest processes (python ones with their script). Repeats at most every 30 min per level; sends a recovery notice.
State: live/resource_watch_state.json"""
import sys, os, json, csv, ctypes, shutil, subprocess, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import send
LIVE = Path(os.environ.get('QD_LIVE', 'C:/ChoiceBridge/live')); LOG = LIVE / 'resource_log.csv'; STATE = LIVE / 'resource_watch_state.json'
MEM_WARN, MEM_CRIT, COMMIT_CRIT, DISK_WARN, DISK_CRIT = 1.2, 0.6, 0.92, 8.0, 4.0


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong), ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong), ('ullTotalVirtual', ctypes.c_ulonglong),
                ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def memory():
    m = MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(MEMORYSTATUSEX); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    g = 1024 ** 3
    return dict(total_gb=m.ullTotalPhys / g, avail_gb=m.ullAvailPhys / g, load_pct=m.dwMemoryLoad,
                commit_used_gb=(m.ullTotalPageFile - m.ullAvailPageFile) / g, commit_limit_gb=m.ullTotalPageFile / g)


def top_processes(n=5):
    try:
        ps = ("Get-CimInstance Win32_Process | Sort-Object WorkingSetSize -Descending | Select-Object -First %d | "
              "ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.Name, $_.ProcessId, [int]($_.WorkingSetSize/1MB), $_.CommandLine }" % n)
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, timeout=60)
        out = []
        for line in r.stdout.decode('utf-8', errors='replace').splitlines():
            parts = line.split('|', 3)
            if len(parts) < 3: continue
            name, pid, mb = parts[:3]; cmd = parts[3] if len(parts) > 3 else ''
            script = next((w.split('\\')[-1].split('/')[-1] for w in cmd.split() if w.endswith('.py')), '') if 'python' in name.lower() else ''
            out.append(f'{name}{"(" + script + ")" if script else ""} pid {pid} {mb}MB')
        return out
    except Exception as e:
        return [f'进程列表读取失败：{type(e).__name__}']


def main():
    now = dt.datetime.now(); m = memory(); free_disk = shutil.disk_usage('C:/').free / 1024 ** 3
    LIVE.mkdir(parents=True, exist_ok=True)
    row = dict(at=now.isoformat(timespec='seconds'), avail_gb=round(m['avail_gb'], 2), load_pct=m['load_pct'], commit_used_gb=round(m['commit_used_gb'], 2),
               commit_limit_gb=round(m['commit_limit_gb'], 2), disk_free_gb=round(free_disk, 2))
    new = not LOG.exists()
    with open(LOG, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(row)); new and w.writeheader(); w.writerow(row)
    # keep 14 days (rewrite once a day at most)
    st = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    if st.get('pruned') != now.strftime('%Y-%m-%d'):
        cut = (now - dt.timedelta(days=14)).isoformat()
        with open(LOG, encoding='utf-8') as f: rows = [r for r in csv.DictReader(f) if r['at'] >= cut]
        with open(LOG, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(row)); w.writeheader(); w.writerows(rows)
        st['pruned'] = now.strftime('%Y-%m-%d')
    low = m['avail_gb'] < MEM_WARN; st['low_streak'] = st.get('low_streak', 0) + 1 if low else 0
    levels = {}
    if m['avail_gb'] < MEM_CRIT or m['commit_used_gb'] > COMMIT_CRIT * m['commit_limit_gb']: levels['mem'] = 'critical'
    elif st['low_streak'] >= 2: levels['mem'] = 'warning'
    if free_disk < DISK_CRIT: levels['disk'] = 'critical'
    elif free_disk < DISK_WARN: levels['disk'] = 'warning'
    sent = st.setdefault('sent', {}); hm = now.strftime('%m-%d %H:%M')
    for k, lv in levels.items():
        last = sent.get(k)
        if last and last['level'] == lv and (now - dt.datetime.fromisoformat(last['at'])).total_seconds() < 1800: continue
        icon = '🚨' if lv == 'critical' else '⚠️'
        if k == 'mem':
            msg = (f"{icon} {hm} 云机内存{'告急' if lv == 'critical' else '偏紧'}：可用 {m['avail_gb']:.2f} GB / {m['total_gb']:.1f} GB（占用 {m['load_pct']}%），"
                   f"提交 {m['commit_used_gb']:.1f}/{m['commit_limit_gb']:.1f} GB。\n最大进程：" + '；'.join(top_processes()))
        else:
            msg = f"{icon} {hm} 云机 C 盘只剩 {free_disk:.1f} GB（{'低于 4 GB，流水线可能写不进文件' if lv == 'critical' else '低于 8 GB'}）。清理任务 QuantHousekeeping 每天 22:40 运行；大文件请先确认再删。"
        if send(msg): sent[k] = dict(level=lv, at=now.isoformat())
    for k in [k for k in list(sent) if k not in levels]:
        if send(f"✅ {hm} 云机{'内存' if k == 'mem' else '磁盘'}已恢复：可用内存 {m['avail_gb']:.2f} GB，C 盘剩 {free_disk:.1f} GB"): sent.pop(k)
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(dict(row, levels=levels), ensure_ascii=False))


def day_summary(day, log=LOG):
    """for the daily report: memory peak (lowest available) and disk free on `day`"""
    if not Path(log).exists(): return None
    with open(log, encoding='utf-8') as f: rows = [r for r in csv.DictReader(f) if r['at'].startswith(day)]
    if not rows: return None
    lo = min(rows, key=lambda r: float(r['avail_gb']))
    return dict(samples=len(rows), min_avail_gb=float(lo['avail_gb']), at=lo['at'][11:16], max_load_pct=max(int(r['load_pct']) for r in rows), disk_free_gb=float(rows[-1]['disk_free_gb']))


if __name__ == '__main__':
    main()
