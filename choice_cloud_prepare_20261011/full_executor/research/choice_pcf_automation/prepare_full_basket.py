"""Public quotes only; no account session, no orders."""
import concurrent.futures
import json
import sqlite3
from run_retest import OLD, ROOT, DAY
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import now_cn, write_json

DEST=ROOT/'research/choice_pcf_automation/full_basket_20260921'


def main():
    if now_cn().date().isoformat()!=DAY:raise RuntimeError('Wrong date')
    with sqlite3.connect((OLD/'state/choice.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        plan=json.loads(db.execute('SELECT plan FROM batches WHERE day=?',(DAY,)).fetchone()[0])
    quotes={};failures=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(quote,o['code']):o for o in plan['orders']}
        for f in concurrent.futures.as_completed(futures):
            order=futures[f]
            try:quotes[order['code']]=f.result()
            except Exception as e:failures.append({'code':order['code'],'error_type':type(e).__name__})
    blocked=[]
    for o in plan['orders']:
        q=quotes.get(o['code'])
        if q and abs(q['ask']/o['reference_price']-1)>.03:
            blocked.append({'code':o['code'],'ask':q['ask'],'reference_price':o['reference_price'],
                            'change_percent':round((q['ask']/o['reference_price']-1)*100,3)})
    result={'asof':now_cn().isoformat(),'orders':len(plan['orders']),'quotes':len(quotes),
            'quote_failures':failures,'outside_original_3pct_tolerance':blocked,
            'estimated_gross_at_ask':round(sum(o['quantity']*quotes[o['code']]['ask'] for o in plan['orders'] if o['code'] in quotes),2)}
    write_json(DEST/'preflight_quotes.json',quotes)
    write_json(DEST/'preflight_summary.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
