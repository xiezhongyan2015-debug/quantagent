"""Allow isolate_unknown_orders=true in the pilot config check (idempotent exact-string patch with backup + manifest re-pin). 2026-10-16, user-approved.

pilot_one_lot.check_pilot_config rejected any config with isolate_unknown_orders=True, so an order whose server receipt could not be
confirmed stopped the whole executor for the day. With the flag on, the teammate's receipt_recovery.isolate path reserves that
name's budget, skips it, and the remaining targets continue (two consistent account snapshots required). Refuses while a session is active.
"""
import hashlib, json, shutil, sys, time, os
from pathlib import Path
ROOT = Path('C:/ChoiceBridge/executor'); BASE = ROOT / 'research/choice_pcf_automation'; P = BASE / 'pilot_one_lot.py'; MARK = '# [isolation patch 2026-10-16]'
OLD = "        or c['isolate_unknown_orders'] or c['max_attempts_per_code']!=1\n"
NEW = "        or type(c['isolate_unknown_orders']) is not bool or c['max_attempts_per_code']!=1   " + MARK + "\n"
sys.path.insert(0, str(BASE)); sys.path.insert(0, 'C:/ChoiceBridge/quant/current/实盘日更/tools'); os.chdir(str(ROOT))
try:
    from notify import send
except Exception:
    def send(m): return False
try:
    s = P.read_text(encoding='utf-8')
    if MARK in s: print('already patched'); sys.exit(0)
    if s.count(OLD) != 1: raise SystemExit('expected exactly one occurrence of the pilot isolation lock')
    if '--check' in sys.argv: print('patchable'); sys.exit(0)
    from cloud_account import verify_deployment; verify_deployment()
    from console_runtime import active_session
    if active_session(): raise RuntimeError('executor session still active')
    bk = Path('C:/ChoiceBridge/live/backups/isolation_' + time.strftime('%Y%m%d_%H%M%S')); bk.mkdir(parents=True)
    mp = ROOT / 'deployment_manifest.json'; shutil.copy2(mp, bk / 'deployment_manifest.json'); shutil.copy2(P, bk / 'pilot_one_lot.py')
    m = json.loads(mp.read_text(encoding='utf-8')); key = 'research/choice_pcf_automation/pilot_one_lot.py'
    assert hashlib.sha256(P.read_bytes()).hexdigest() == m['files'][key], 'executor file does not match manifest before patch'
    tmp = P.with_suffix('.py.deploytmp'); tmp.write_text(s.replace(OLD, NEW), encoding='utf-8'); os.replace(tmp, P)
    m['files'][key] = hashlib.sha256(P.read_bytes()).hexdigest(); m['isolate_unknown_orders_allowed'] = dict(at=time.strftime('%Y-%m-%dT%H:%M:%S'), backup=str(bk), approved_by='user 2026-10-16')
    t = mp.with_suffix('.json.deploytmp'); t.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8'); os.replace(t, mp)
    import importlib, cloud_account; importlib.reload(cloud_account); cloud_account.verify_deployment()
    msg = f'✅ 未知委托隔离已允许（{time.strftime("%H:%M")}）：试单检查不再拒绝 isolate_unknown_orders=true；明日发布的配置将开启隔离，一笔回执不明只跳过该股，其余继续。'
except SystemExit: raise
except Exception as e:
    msg = f'⚠️ 隔离开关补丁未部署：{type(e).__name__}: {str(e)[:120]}。原文件未改。'
print(msg); send(msg)
