# Read-only: print key structure of executor private_state.json files (no values of sensitive fields)
import json, sys, pathlib
root = pathlib.Path(r'C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs')
SKIP = '094015_438e7161'
for p in sorted(root.glob('*/*/private_state.json')):
    if SKIP in str(p):
        continue
    s = json.loads(p.read_text(encoding='utf-8'))
    att = s.get('attempts', [])
    la = s.get('last_audit', {})
    print(p.parent.parent.name, p.parent.name, 'keys=', sorted(s.keys())[:60])
    print('   n_targets', len(s.get('targets', {})), 'n_attempts', len(att), 'n_deferred', len(s.get('deferred', {})),
          'formal', sum(1 for a in att if a.get('phase') == 'formal'), 'audit_keys', sorted(la.keys())[:40])
    if att:
        print('   attempt keys', sorted(att[-1].keys()), 'order keys', sorted(att[-1]['order'].keys()))
    q = s.get('quotes', {})
    if q:
        k = next(iter(q)); print('   quote keys', sorted(q[k].keys()))
    t = s.get('targets', {})
    if t:
        k = next(iter(t)); print('   target keys', sorted(t[k].keys()))
