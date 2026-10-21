import sys,json
from pathlib import Path
p=Path('C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs/2026-10-12/102700_ebc802f8');s=json.loads((p/'private_state.json').read_text(encoding='utf8'));status=json.loads((p/'status_redacted.json').read_text(encoding='utf8'));out=[]
for a in s['attempts']:
 if a.get('state')!='4':out.append({k:a.get(k) for k in ('order','state','oid','filled','terminal','submitted_at','terminal_at','cancel_requested_at','cancel_not_sent_auth_at','submit_failed_at','submit_error_type')})
print(json.dumps(dict(incomplete=out,remaining=status['remaining'],deferred=status['deferred'],reference_quotes={c:s['quotes'].get(c) for c in status['remaining']},reference_prices={c:s['targets'][c]['reference_price'] for c in status['remaining']},nav=s['last_audit']['total_assets'],market=s['last_audit']['market_value'],cash=s['last_audit']['cash']),ensure_ascii=False))
