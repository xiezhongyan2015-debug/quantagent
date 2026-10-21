import sys,json,time
from pathlib import Path
base=Path('C:/ChoiceBridge/executor/research/choice_pcf_automation');sys.path.insert(0,str(base));import console_core
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import now_cn
from batch_engine import stable_price_at_ask,validate_quote
import requests
r=base/'console/runs/2026-10-12/102700_ebc802f8';s=json.loads((r/'private_state.json').read_text(encoding='utf8'));v=json.loads((r/'status_redacted.json').read_text(encoding='utf8'));session=requests.Session();session.trust_env=False;out={}
for c,reason in v['deferred'].items():
 if reason!='价格保护或价格精度' or c not in v['remaining']:continue
 try:
  q=quote(c,session);validate_quote(q,{'quote_max_age_seconds':30},now_cn());side=1 if v['remaining'][c]>0 else 2;price=stable_price_at_ask(q['ask'] if side==1 else q['bid'],0);ref=s['targets'][c]['reference_price'];out[c]=dict(name=q['name'],timestamp=q['timestamp'],side=side,remaining=abs(v['remaining'][c]),reference_price=ref,order_price=price,deviation_pct=None if price is None else (price/ref-1)*100,precision_rejected=price is None,over_3pct=None if price is None else abs(price/ref-1)>.03)
 except Exception as e:out[c]=dict(error_type=type(e).__name__,reason=str(e))
 time.sleep(1.05)
print(json.dumps(out,ensure_ascii=False))
