# Read-only: locate price-guard code paths in the deployed executor and config writers; print manifest structure (no secrets).
import json, pathlib, re, hashlib
EX = pathlib.Path(r'C:/ChoiceBridge/executor')
pat = re.compile(r"price_guard|directional_price_allowed|Decimal\('\.03'\)|outside_3pct|3%价格保护")
for root in [EX / 'research/choice_pcf_automation', pathlib.Path(r'C:/ChoiceBridge/staging'), pathlib.Path(r'C:/ChoiceBridge/live')]:
    for p in sorted(root.glob('*.py')):
        try: lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
        except Exception: continue
        for i, l in enumerate(lines, 1):
            if pat.search(l): print(f'{p}:{i}: {l.strip()[:220]}')
m = json.loads((EX / 'deployment_manifest.json').read_text(encoding='utf-8'))
print('MANIFEST top keys', list(m.keys()) if isinstance(m, dict) else type(m))
if isinstance(m, dict):
    for k, v in m.items():
        if isinstance(v, dict):
            ks = list(v.keys()); print(' ', k, 'dict', len(ks), [x for x in ks if any(s in x for s in ('workflow_rebalance', 'console_runtime', 'console_core', 'batch_engine', 'watch_engine', 'pilot_one_lot', 'console_app'))])
        elif isinstance(v, list): print(' ', k, 'list', len(v), str(v[:2])[:200])
        else: print(' ', k, str(v)[:120])
for f in ['research/choice_pcf_automation/workflow_rebalance.py', 'research/choice_pcf_automation/console_runtime.py', 'research/choice_pcf_automation/console_core.py']:
    print(f, hashlib.sha256((EX / f).read_bytes()).hexdigest()[:16])
