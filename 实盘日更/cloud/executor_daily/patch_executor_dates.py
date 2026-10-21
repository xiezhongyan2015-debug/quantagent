"""Make the cloud executor's pilot->targets workflow date-generic (idempotent, exact-string patch with backups).

Before: pilot_one_lot.py / workflow_rebalance.py carried the day as constants (2026-10-12 / 2026-10-09) and the PCF file name.
After : both read C:/ChoiceBridge/live/active_day.json {execution_date, signal_date, pcf_file, pcf_trading_day, pcf_valuation_day}
        (override with env CHOICE_ACTIVE_DAY). Index-constituent evidence may be up to 7 calendar days older than the signal
        date (CSI publishes the file after the close; the executor re-verifies the live file hash before every formal phase).
Run:   python patch_executor_dates.py [--check]   (on the cloud machine; BASE = C:/ChoiceBridge/executor/research/choice_pcf_automation)
"""
import sys, shutil, hashlib
from pathlib import Path
BASE = Path(sys.argv[sys.argv.index('--base') + 1]) if '--base' in sys.argv else Path('C:/ChoiceBridge/executor/research/choice_pcf_automation')
MARK = '# [daily-generic patch 2026-10-12]'
LOADER = ("import os as _os\n_ACTIVE=json.loads(Path(_os.environ.get('CHOICE_ACTIVE_DAY','C:/ChoiceBridge/live/active_day.json')).read_text(encoding='utf-8'))\n")
EDITS = {
    'pilot_one_lot.py': [
        ("EXECUTION_DATE='2026-10-12'\nSIGNAL_DATE='2026-10-09'\n",
         MARK + "\n" + LOADER + "EXECUTION_DATE=_ACTIVE['execution_date']\nSIGNAL_DATE=_ACTIVE['signal_date']\n_SNAPSHOT_FLOOR=(dt.date.fromisoformat(SIGNAL_DATE)-dt.timedelta(days=7)).isoformat()\n"),
        ("path=Path(base_dir)/'daily_public_pcf/563030_20261009_official.xml'", "path=Path(base_dir)/'daily_public_pcf'/_ACTIVE['pcf_file']"),
        ("root.findtext('TradingDay')!='20261009' or root.findtext('PreTradingDay')!='20261008'", "root.findtext('TradingDay')!=_ACTIVE['pcf_trading_day'] or root.findtext('PreTradingDay')!=_ACTIVE['pcf_valuation_day']"),
        ("not SIGNAL_DATE<=m.get('snapshot_date','')<=EXECUTION_DATE", "not _SNAPSHOT_FLOOR<=m.get('snapshot_date','')<=EXECUTION_DATE"),
    ],
    'console_runtime.py': [
        ("    if not 0<=(execution-today).days<=1:raise TradeError('仅可在试单当天或前一天预约固定日期的一手试单')",
         "    _days,_=days_for(c,day);_later=[d for d in _days if d>today.isoformat()]\n    if not (day==today.isoformat() or (_later and day==_later[0])):raise TradeError('只能预约当天或日历上的下一个交易日')   " + MARK),
    ],
    'workflow_rebalance.py': [
        ("DAY='2026-10-12'\nSIGNAL='2026-10-09'\n",
         MARK + "\n" + LOADER + "DAY=_ACTIVE['execution_date']\nSIGNAL=_ACTIVE['signal_date']\n_SNAPSHOT_FLOOR=(dt.date.fromisoformat(SIGNAL)-dt.timedelta(days=7)).isoformat()\n"),
        ("if x.get('snapshot_date')!=SIGNAL or x.get('source_url')!=urls[x['index']]", "if not _SNAPSHOT_FLOOR<=x.get('snapshot_date','')<=SIGNAL or x.get('source_url')!=urls[x['index']]"),
    ],
}
check = '--check' in sys.argv; report = {}
for name, edits in EDITS.items():
    p = BASE / name; s = p.read_text(encoding='utf-8')
    if MARK in s: report[name] = 'already patched'; continue
    for old, new in edits:
        if s.count(old) != 1: raise SystemExit(f'{name}: expected exactly one occurrence of: {old[:60]!r}')
    if check: report[name] = 'patchable'; continue
    shutil.copy2(p, p.with_suffix(p.suffix + '.before_daily_patch'))
    for old, new in edits: s = s.replace(old, new)
    p.write_text(s, encoding='utf-8'); report[name] = 'patched sha256=' + hashlib.sha256(p.read_bytes()).hexdigest()[:16]
print(report)
