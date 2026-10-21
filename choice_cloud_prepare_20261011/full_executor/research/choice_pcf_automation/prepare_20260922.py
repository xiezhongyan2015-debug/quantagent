"""Archive tonight's PCF and after-close quotes, no broker credentials/orders."""
import concurrent.futures as cf
import datetime as dt
import json
from pathlib import Path
from verify_readonly import ROOT
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json,write_json,close_job

DEST=ROOT/'research/choice_pcf_automation/session_20260922'
SIGNAL='2026-09-21';DAY='2026-09-22'

def main():
    if now_cn().date().isoformat()!=SIGNAL or now_cn().time()<dt.time(15,5):raise RuntimeError('Preparation must occur on signal date after close')
    DEST.mkdir(exist_ok=True)
    days=['2026-09-17','2026-09-18','2026-09-21','2026-09-22','2026-09-23','2026-09-24','2026-09-28','2026-09-29','2026-09-30']
    write_json(DEST/'calendar.json',days)
    cfg=dict(state_dir=str(DEST/'state'),calendar=str(DEST/'calendar.json'),close_command=[],enable_choice_orders=False)
    manifest=close_job(cfg)
    record=read_json(DEST/'state/archive'/SIGNAL/'pcf.json')
    prior=read_json(ROOT/'research/choice_pcf_automation/until_close_20260921/private_state.json')
    codes=sorted({normalize_code(str(x['C_STOCKCODE'])) for x in record['stocklist']}|set(prior['last_audit']['positions']))
    output=DEST/'close_quotes.json';quotes=read_json(output) if output.exists() else {};errors={}
    def fetch(code):
        q=quote(code);stamp=dt.datetime.fromisoformat(q['timestamp'])
        if stamp.date().isoformat()!=SIGNAL or stamp.time()<dt.time(15) or stamp>now_cn():raise RuntimeError('Quote lacks same-date after-close source timestamp')
        return q
    remaining=[c for c in codes if c not in quotes]
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(fetch,c):c for c in remaining}
        for i,f in enumerate(cf.as_completed(jobs),1):
            c=jobs[f]
            try:quotes[c]=f.result()
            except Exception as e:errors[c]=type(e).__name__
            write_json(output,quotes)
            if i%40==0:print('after-close quotes',i,'/',len(remaining),flush=True)
    write_json(DEST/'preparation.json',dict(prepared_at=now_cn().isoformat(),pcf_date=SIGNAL,execution_date=DAY,
               pcf_manifest=manifest,pcf_rows=len(record['stocklist']),positive_pcf_names=sum(float(r['L_NUMBER'])>0 for r in record['stocklist']),
               required_quote_names=len(codes),available_quote_names=len(quotes),errors=errors,
               account_accessed=False,orders_submitted=0,enable_choice_orders=False,
               note='Archive and quotes only. Capital approval and cross-day inventory checks required before enabling execution.'))
    print(json.dumps(read_json(DEST/'preparation.json'),ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
