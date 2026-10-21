"""Widen the executor's two timing windows (idempotent exact-string patch with backup + manifest re-pin). 2026-10-16.

  two_audits: two consistent account snapshots had to arrive within 90 s, else "连续90秒账户核对未通过" and a full stop
              (this is what turned the 10-16 dividend fraction and the 10-13 booking into stops).  -> 300 s.
  receipt recovery: an order whose server receipt is not confirmed within 90 s stops the whole executor
              (isolate_unknown_orders is false).  -> 240 s.
Nothing else changes.  Refuses while an executor session is active.  Run with the executor interpreter from C:\\ChoiceBridge\\executor:
  python patch_executor_windows.py [--check]
"""
import hashlib, json, shutil, sys, time, os
from pathlib import Path
ROOT = Path('C:/ChoiceBridge/executor'); BASE = ROOT / 'research/choice_pcf_automation'; P = BASE / 'console_runtime.py'
MARK = '# [timing-window patch 2026-10-16]'
EDITS = [
    ("        consistent=0;started=time.monotonic();until=started+90;last_reason=None;proofs=[]",
     "        consistent=0;started=time.monotonic();until=started+300;last_reason=None;proofs=[]   " + MARK),
    ("            if time.monotonic()>until:raise TradeError('连续90秒账户核对未通过'+('：'+last_reason if last_reason else ''))",
     "            if time.monotonic()>until:raise TradeError('连续300秒账户核对未通过'+('：'+last_reason if last_reason else ''))"),
    ("        started=time.monotonic();until=started+90;previous=None;absent=None;last_reason='等待服务器委托查询'",
     "        started=time.monotonic();until=started+240;previous=None;absent=None;last_reason='等待服务器委托查询'"),
    ("                reason=f'回执自动核查90秒仍未确认：{last_reason}；原委托保留，不自动重发，需确认服务器是否受理'",
     "                reason=f'回执自动核查240秒仍未确认：{last_reason}；原委托保留，不自动重发，需确认服务器是否受理'"),
]
sys.path.insert(0, str(BASE)); sys.path.insert(0, 'C:/ChoiceBridge/quant/current/实盘日更/tools'); os.chdir(str(ROOT))
check = '--check' in sys.argv
try:
    from notify import send
except Exception:
    def send(m): return False
try:
    s = P.read_text(encoding='utf-8')
    if MARK in s: print('already patched'); sys.exit(0)
    for old, new in EDITS:
        if s.count(old) != 1: raise SystemExit(f'expected exactly one occurrence of: {old[:70]!r}')
    if check: print('patchable: 4 edits'); sys.exit(0)
    from cloud_account import verify_deployment; verify_deployment()
    from console_runtime import active_session
    if active_session(): raise RuntimeError('executor session still active')
    bk = Path('C:/ChoiceBridge/live/backups/timing_windows_' + time.strftime('%Y%m%d_%H%M%S')); bk.mkdir(parents=True)
    mp = ROOT / 'deployment_manifest.json'; shutil.copy2(mp, bk / 'deployment_manifest.json'); shutil.copy2(P, bk / 'console_runtime.py')
    m = json.loads(mp.read_text(encoding='utf-8')); key = 'research/choice_pcf_automation/console_runtime.py'
    assert hashlib.sha256(P.read_bytes()).hexdigest() == m['files'][key], 'executor file does not match manifest before patch'
    for old, new in EDITS: s = s.replace(old, new)
    tmp = P.with_suffix('.py.deploytmp'); tmp.write_text(s, encoding='utf-8'); os.replace(tmp, P)
    m['files'][key] = hashlib.sha256(P.read_bytes()).hexdigest()
    m['timing_windows'] = dict(at=time.strftime('%Y-%m-%dT%H:%M:%S'), two_audits_seconds=300, receipt_seconds=240, backup=str(bk))
    t = mp.with_suffix('.json.deploytmp'); t.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8'); os.replace(t, mp)
    import importlib, cloud_account; importlib.reload(cloud_account); cloud_account.verify_deployment()
    msg = f'✅ 执行器核对窗口已放宽（{time.strftime("%H:%M")}）：两轮账户核对 90→300 秒，委托回执核查 90→240 秒。部署清单校验通过。'
except SystemExit: raise
except Exception as e:
    msg = f'⚠️ 执行器核对窗口补丁未部署：{type(e).__name__}: {str(e)[:120]}。原文件未改。'
print(msg); send(msg)
