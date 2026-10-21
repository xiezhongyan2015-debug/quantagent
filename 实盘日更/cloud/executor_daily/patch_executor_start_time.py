"""Make the cloud executor's session start time configurable (idempotent, exact-string patch with backups). 2026-10-15.

Before: console_runtime.py opens the pilot/rebalance window at a hard-coded 09:30 (three places).
After : the window opens at active_day.json['start_time'] (HH:MM, default "09:30"), read next to the existing
        daily-generic loader.  Nothing else changes: the 14:50 new-order cutoff and the 14:35 floor logic stay, so an
        afternoon session must start no later than ~13:00 to leave room for the pilot lot and two reconciliations.
Run (on the cloud machine, executor interpreter):  python patch_executor_start_time.py [--check] [--base DIR]
Then re-pin the deployment manifest (refresh_manifest.py) or arm.py will refuse to start.
NOT deployed by default: the live session stays at 09:30 until the operator switches start_time in active_day.json.
"""
import sys, shutil
from pathlib import Path
BASE = Path(sys.argv[sys.argv.index('--base') + 1]) if '--base' in sys.argv else Path('C:/ChoiceBridge/executor/research/choice_pcf_automation')
MARK = '# [start-time patch 2026-10-15]'
LOADER = (MARK + "\nimport os as _os, json as _json\nfrom pathlib import Path as _Path\ndef _start_time():\n"
          "    try: v = _json.loads(_Path(_os.environ.get('CHOICE_ACTIVE_DAY', 'C:/ChoiceBridge/live/active_day.json')).read_text(encoding='utf-8')).get('start_time', '09:30')\n"
          "    except Exception: v = '09:30'\n    h, m = v.split(':'); t = dt.time(int(h), int(m))\n"
          "    if not dt.time(9, 30) <= t <= dt.time(13, 5): raise TradeError('start_time must be between 09:30 and 13:05')\n    return t\n")
OLD = 'dt.time(9,30)'; NEW = '_start_time()'
check = '--check' in sys.argv; p = BASE / 'console_runtime.py'; s = p.read_text(encoding='utf-8')
if MARK in s: print('already patched'); sys.exit(0)
n = s.count(OLD)
if n != 3: raise SystemExit(f'expected exactly three occurrences of {OLD!r}, found {n}')
anchor = 'def pilot_start_day(c,now):'
if s.count(anchor) != 1: raise SystemExit('anchor for loader not found')
if check: print('patchable: 3 occurrences, anchor found'); sys.exit(0)
shutil.copy2(p, p.with_suffix('.py.before_start_time_patch'))
s = s.replace(OLD, NEW).replace(anchor, LOADER + '\n\n' + anchor)
p.write_text(s, encoding='utf-8'); print('patched console_runtime.py; backup .before_start_time_patch; now refresh the deployment manifest')
