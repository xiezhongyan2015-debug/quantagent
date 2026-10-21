"""Cloud housekeeping (task QuantHousekeeping, SYSTEM, daily 22:40). 2026-10-17. Dry run unless --apply.
Deletes ONLY whitelisted, regenerable items past their retention. Never touches executor ledgers/runs, releases, reports,
snapshots, targets, model files, the c5 payload or anything not listed below.
  quant/*.tgz deployment bundles            > 3 days
  live/patches/* uploaded one-off scripts   > 30 days
  live/timing/{smoke,beta_shadow_test,v3_raw} research/smoke scratch  > 7 days
  live/timing/auth_probe_*.json             > 1 day
  Windows TEMP entries (pip build dirs, copies made for diagnosis)    > 7 days
  gru_research/trading_data/future_minute_probe/sina_*  keep the 3 newest (raw minute folders, already merged into minute_store)
  live/logs/*.log                           > 60 days
Needs explicit approval (--approve NAME, recorded in live/housekeeping_approved.json): live/backups/fd_raw_20261013, staging/iso_test
Also appends folder sizes to live/storage_log.csv (one row per day) so growth of the c5 payload / quant / live is visible.
Log: live/logs/housekeeping_<date>.log"""
import sys, os, json, shutil, time, datetime as dt
from pathlib import Path

CB = Path('C:/ChoiceBridge'); LIVE = CB / 'live'; QUANT = CB / 'quant'; TEMP = Path(os.environ.get('TEMP_DIR', 'C:/Users/administrator/AppData/Local/Temp'))
APPROVAL = {'fd_raw_20261013': LIVE / 'backups/fd_raw_20261013', 'iso_test': CB / 'staging/iso_test'}
APPLY = '--apply' in sys.argv
now = time.time(); day = dt.date.today().isoformat(); log = []


def size(p):
    p = Path(p)
    if p.is_file(): return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob('*') if f.is_file()) if p.exists() else 0


def age_days(p): return (now - Path(p).stat().st_mtime) / 86400


def remove(p, why):
    s = size(p); log.append(f"{'DEL ' if APPLY else 'WOULD DEL '}{s / 1e6:9.1f} MB  {p}  ({why})")
    if APPLY:
        try: shutil.rmtree(p) if Path(p).is_dir() else Path(p).unlink()
        except Exception as e: log.append(f'  ! failed: {e}')
    return s


freed = 0
for f in QUANT.glob('*.tgz'):
    if age_days(f) > 3: freed += remove(f, 'deployment bundle > 3d')
for f in (LIVE / 'patches').glob('*'):
    if f.is_file() and age_days(f) > 30: freed += remove(f, 'patch script > 30d')
for name in ('smoke', 'beta_shadow_test', 'v3_raw'):
    p = LIVE / 'timing' / name
    if p.exists() and age_days(p) > 7: freed += remove(p, 'timing scratch > 7d')
for f in (LIVE / 'timing').glob('auth_probe_*.json'):
    if age_days(f) > 1: freed += remove(f, 'probe output > 1d')
if TEMP.exists():
    for f in TEMP.iterdir():
        try:
            if age_days(f) > 7: freed += remove(f, 'temp > 7d')
        except Exception: pass
mp = QUANT / 'current/gru_research/trading_data/future_minute_probe'
if mp.exists():
    for f in sorted([d for d in mp.iterdir() if d.is_dir() and d.name.startswith('sina_')], key=lambda d: d.name, reverse=True)[3:]:
        freed += remove(f, 'raw minute folder beyond 3 newest')
for f in (LIVE / 'logs').glob('*.log'):
    if age_days(f) > 60: freed += remove(f, 'log > 60d')
ap = LIVE / 'housekeeping_approved.json'; approved = json.loads(ap.read_text(encoding='utf-8')) if ap.exists() else {}
for a in [sys.argv[i + 1] for i, x in enumerate(sys.argv) if x == '--approve' and i + 1 < len(sys.argv)]:
    if a in APPROVAL: approved[a] = dt.datetime.now().isoformat(timespec='seconds')
if APPLY: ap.write_text(json.dumps(approved, ensure_ascii=False), encoding='utf-8')
for name, p in APPROVAL.items():
    if p.exists():
        if name in approved: freed += remove(p, f'approved {approved[name]}')
        else: log.append(f'NEEDS APPROVAL {size(p) / 1e6:9.1f} MB  {p}  (run with --approve {name})')
total, used, free = shutil.disk_usage('C:/')
sizes = {k: round(size(v) / 1e9, 2) for k, v in dict(c5_stage=CB / 'c5_stage_20261012', live=LIVE, runtime=CB / 'runtime', executor=CB / 'executor').items()}
sizes['quant'] = round(sum(size(d) for d in QUANT.iterdir() if d.name != 'current') / 1e9, 2)  # 'current' is a junction to gru_lite_*: do not count twice
row = dict(date=day, free_gb=round(free / 1e9, 2), freed_gb=round(freed / 1e9, 3), applied=APPLY, **sizes)
sl = LIVE / 'storage_log.csv'
if APPLY:
    hdr = not sl.exists()
    with open(sl, 'a', encoding='utf-8') as fh:
        if hdr: fh.write(','.join(row) + '\n')
        fh.write(','.join(str(v) for v in row.values()) + '\n')
text = '\n'.join(log + [json.dumps(row, ensure_ascii=False)])
(LIVE / 'logs').mkdir(parents=True, exist_ok=True)
(LIVE / f'logs/housekeeping_{day}.log').write_text(text, encoding='utf-8')
print(text)
