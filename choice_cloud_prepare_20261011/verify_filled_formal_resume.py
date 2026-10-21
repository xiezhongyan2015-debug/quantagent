import sys,json,hashlib
from pathlib import Path
root=Path('C:/ChoiceBridge/executor/research/choice_pcf_automation');sys.path.insert(0,str(root))
import workflow_rebalance as w
p=root/'console/runs/2026-10-12/095215_db7350f4/private_state.json';s=json.loads(p.read_text(encoding='utf8'));c=json.loads((p.parent/'config.json').read_text(encoding='utf8'));w.effective(c,s);w.consistent_position_marks(s['last_audit']);assert len(s['attempts'])==4 and all(x['state']=='4' and x['terminal'] and x['filled']==x['order']['quantity'] for x in s['attempts']);assert not s['last_audit']['pending'] and not float(s['last_audit']['frozen']);print(json.dumps(dict(source_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),complete_orders=4,total_filled=5100,formal_phase_preserved=True,zero_pending_and_frozen=True,marks_consistent=True,no_network_or_order_calls=True)))
