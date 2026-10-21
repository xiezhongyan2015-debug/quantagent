"""Inspect public quotes for independently audited missing targets. Never orders."""
import json
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
import datetime as dt
import requests
from verify_readonly import ROOT
from run_full_basket import OLD,journal_read
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import read_json,write_json,now_cn
from batch_engine import stable_price_at_ask


def main():
    audit=read_json(ROOT/'research/choice_pcf_automation/queued_basket_20260921/independent_whole_account_audit.json')
    now=now_cn()
    if not 0 <= (now-dt.datetime.fromisoformat(audit['checked_at'])).total_seconds() <= 600:
        raise RuntimeError('Fresh read-only account audit required')
    if audit['issues'] or audit['pending_count']:
        raise RuntimeError('Resolve audit issues/open orders before classifying new submissions')
    plan,_,_=journal_read(OLD)
    original={o['code']:o for o in plan['orders']}
    rows=[]
    with requests.Session() as session:
        for missing in audit['remaining_targets']:
            code=missing['code'];reference=Decimal(str(original[code]['reference_price']))
            lower=(reference*Decimal('.97')).quantize(Decimal('.01'),rounding=ROUND_CEILING)
            upper=(reference*Decimal('1.03')).quantize(Decimal('.01'),rounding=ROUND_FLOOR)
            row=dict(missing,reference_close=float(reference),guard_min=float(lower),guard_max=float(upper))
            try:
                q=quote(code,session)
                age=(now_cn()-dt.datetime.fromisoformat(q['timestamp'])).total_seconds()
                ask=Decimal(str(q['ask']))
                row.update(quote=q,quote_age_seconds=round(age,2),source_timestamp_within_30s=0<=age<=30,
                           ask_deviation=float(ask/reference-1),within_price_guard=lower<=ask<=upper,
                           limit_precision_safe=stable_price_at_ask(q['ask']) is not None)
            except Exception as error:
                row['quote_error_type']=type(error).__name__
            rows.append(row)
    result=dict(checked_at=now_cn().isoformat(),account_checked_at=audit['checked_at'],
                held_securities=audit['held_securities'],pending_count=audit['pending_count'],
                frozen_cny=audit['balance']['frozenMoney'],actual_paid_cny=audit['actual_paid_cny'],
                remaining=rows,new_orders=0,cancellations=0,
                note='Read-only snapshot. A recent provider timestamp does not establish an executable exchange quote, especially during recess. Price-inside-range is not permission, verified instrument identity, tradability or fill confirmation.')
    stamp=now.strftime('%Y%m%d_%H%M%S')
    out=ROOT/'research/choice_pcf_automation'/('remaining_review_'+stamp+'.json')
    write_json(out,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('REPORT',out)


if __name__=='__main__':main()
