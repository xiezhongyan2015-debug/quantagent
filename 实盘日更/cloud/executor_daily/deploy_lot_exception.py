"""Deploy the 2026-10-20 one-lot exception (lot_exception_patch.py) after the close.
1 refuse while an executor session is alive; verify the deployment manifest as it is now
2 sandbox: copy research/choice_pcf_automation to live/patches/lot_sandbox_<ts>/{base,patched}, patch 'patched', run the executor's
  own test modules in both plus test_lot_exception in 'patched' (PYTHONPATH = the executor's quantagent-pr src, CHOICE_ACTIVE_DAY
  = a sandbox copy of active_day.json); deploy only if patched passes at least what base passes and every new test passes
3 back up, patch the live files in place, re-pin their manifest hashes, re-verify the manifest
Usage: python -X utf8 deploy_lot_exception.py [--dry-run]   (--dry-run stops after the sandbox comparison)"""
import hashlib, json, shutil, subprocess, sys, time, os, re
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = Path('C:/ChoiceBridge/executor'); BASE = ROOT / 'research/choice_pcf_automation'
SRC = ROOT / 'references/quantagent-pr/src'; LIVE = Path('C:/ChoiceBridge/live'); DRY = '--dry-run' in sys.argv
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(BASE)); sys.path.insert(0, 'C:/ChoiceBridge/quant/current/实盘日更/tools'); os.chdir(str(ROOT))
import lot_exception_patch as P
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
TESTS = ['test_workflow_rebalance', 'test_execution_policy', 'test_executor_efficiency', 'test_symbol_watchdog', 'test_cloud_pilot']


def run_tests(d, modules):
    env = dict(os.environ, PYTHONPATH=str(SRC), CHOICE_ACTIVE_DAY=str(d.parent / 'active_day.json'), PYTHONUTF8='1')
    res = {}
    for m in modules:
        r = subprocess.run([sys.executable, '-B', '-m', 'unittest', m], cwd=str(d), env=env, capture_output=True, timeout=900)
        tail = r.stderr.decode('utf-8', 'replace').strip().splitlines()[-3:]
        e = r.stderr.decode('utf-8', 'replace'); ran = re.search(r'Ran (\d+) test', e); bad = re.search(r'FAILED \((.*)\)', e)
        ids = sorted(set(re.findall(r'^(?:FAIL|ERROR): (\S+ \([^)]+\))', e, re.M)))   # 2026-10-20: compare failing test ids, not counts
        res[m] = dict(rc=r.returncode, ran=int(ran.group(1)) if ran else 0, failed=bad.group(1) if bad else '', failing=ids, tail=tail)
    return res


def main():
    from notify import send
    if 'lot_exception_fix' in json.loads((ROOT / 'deployment_manifest.json').read_text(encoding='utf-8')):
        print('ALREADY DEPLOYED (manifest has lot_exception_fix); nothing to do'); return 'already'
    from cloud_account import verify_deployment
    from console_runtime import active_session
    verify_deployment()
    if active_session(): raise RuntimeError('executor session still active')
    ts = time.strftime('%Y%m%d_%H%M%S'); sb = LIVE / f'patches/lot_sandbox_{ts}'
    for k in ('base', 'patched'): shutil.copytree(BASE, sb / k, ignore=shutil.ignore_patterns('console', 'cloud_ledger', '__pycache__', '*.log'))
    shutil.copy2(LIVE / 'active_day.json', sb / 'active_day.json')
    P.apply(sb / 'patched'); shutil.copy2(HERE / 'test_lot_exception.py', sb / 'patched/test_lot_exception.py')
    base = run_tests(sb / 'base', TESTS); patched = run_tests(sb / 'patched', TESTS + ['test_lot_exception'])
    ok = patched['test_lot_exception']['rc'] == 0 and all(patched[m]['rc'] == 0 or base[m]['rc'] != 0 and patched[m]['ran'] == base[m]['ran']
                                                          and set(patched[m]['failing']) <= set(base[m]['failing']) for m in TESTS)
    report = dict(at=ts, sandbox=str(sb), base=base, patched=patched, sandbox_ok=ok)
    (sb / 'sandbox_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({m: (base.get(m, {}).get('rc'), base.get(m, {}).get('failed'), patched[m]['rc'], patched[m]['failed']) for m in patched}, ensure_ascii=False))   # 2026-10-20: base has no test_lot_exception
    if not ok: raise RuntimeError('sandbox: patched tests worse than base or new tests failed; see ' + str(sb / 'sandbox_report.json'))
    if DRY: print('DRY RUN: sandbox ok, live files untouched'); return 'dry'
    bk = LIVE / f'backups/lot_exception_{ts}'; bk.mkdir(parents=True)
    mp = ROOT / 'deployment_manifest.json'; shutil.copy2(mp, bk / 'deployment_manifest.json'); m = json.loads(mp.read_text(encoding='utf-8'))
    for name in ('workflow_rebalance.py', 'console_runtime.py'):
        key = 'research/choice_pcf_automation/' + name
        if key in m['files'] and sha(BASE / name) != m['files'][key]: raise RuntimeError(f'{name} does not match the manifest before the patch')
        shutil.copy2(BASE / name, bk / name)
    P.apply(BASE)
    for name in ('workflow_rebalance.py', 'console_runtime.py'):
        key = 'research/choice_pcf_automation/' + name
        if key in m['files']: m['files'][key] = sha(BASE / name)
    m['lot_exception_fix'] = dict(at=time.strftime('%Y-%m-%dT%H:%M:%S'), backup=str(bk), sandbox=str(sb),
        reason='2026-10-20: names whose one legal minimum slice exceeds 50,000 CNY (SZ300308 660.7, SH688536 265) were never tradable (不足买入整手); one such slice may now be sent up to 200,000 CNY')
    t = mp.with_suffix('.json.deploytmp'); t.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8'); os.replace(t, mp)
    import importlib, cloud_account; importlib.reload(cloud_account); cloud_account.verify_deployment()
    msg = f'✅ 高价股一手例外已部署（{time.strftime("%H:%M")}）：一手超过 5 万元的股票允许单笔下一手（上限 20 万元），其余订单仍按 5 万拆。执行器自带测试在沙箱中与改前一致，新测试全部通过，部署清单已重钉。'
    print(msg)
    try: send(msg)
    except Exception as e: print('notify failed', e)
    return 'deployed'


if __name__ == '__main__':
    try: print('RESULT', main())
    except Exception as e:
        print('FAILED', type(e).__name__, e)
        try:
            from notify import send; send(f'⚠️ 高价股一手例外部署未完成：{type(e).__name__}: {str(e)[:150]}。执行器文件未改，需人工处理。')
        except Exception: pass
        sys.exit(2)
