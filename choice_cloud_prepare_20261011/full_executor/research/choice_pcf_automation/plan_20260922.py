"""Deterministic whole-lot PCF allocation under the user's approved 50%/CNY5.5m bounds."""
import json
import math
from pathlib import Path
import pandas as pd
from verify_readonly import ROOT
from quanta_agents.factor_lab_a.pcf import normalize_code,validate_pcf
from quanta_agents.factor_lab_a.pcf_automation import read_json,write_json,now_cn,digest

DEST=ROOT/'research/choice_pcf_automation/session_20260922'


def allocate(weights,prices,budget,single_cap=500000):
    """Follow relative eligible PCF weights; use whole lots to reach nominal budget."""
    total=sum(weights.values())
    if not total>0:raise ValueError('Empty eligible PCF weights')
    ideal={c:budget*w/total for c,w in weights.items()}
    if max(ideal.values())>single_cap:raise ValueError('PCF proportional target exceeds single-stock cap')
    target={}
    for c,money in ideal.items():
        n=int(money/prices[c]//100)*100
        if c.startswith('SH688') and n<200:n=0
        target[c]=n
    value=sum(target[c]*prices[c] for c in target)
    while value<budget:
        candidates=[]
        for c in target:
            lot=200 if c.startswith('SH688') and target[c]==0 else 100
            extra=lot*prices[c]
            if (target[c]+lot)*prices[c]>single_cap:continue
            deficit=ideal[c]-target[c]*prices[c]
            candidates.append((deficit/extra, -extra, c,lot))
        if not candidates:raise ValueError('No legal lot can complete target')
        _,_,c,lot=max(candidates);target[c]+=lot;value+=lot*prices[c]
    return target,ideal,value


def main():
    record=read_json(DEST/'state/archive/2026-09-21/pcf.json');validate_pcf(record)
    q=read_json(DEST/'close_quotes.json')
    old=read_json(ROOT/'research/choice_pcf_automation/until_close_20260921/private_state.json')
    audit=read_json(ROOT/'research/choice_pcf_automation/until_close_20260921/independent_readonly_audit.json')
    if audit['issues'] or audit['pending_count'] or float(audit['frozen_cny']):raise ValueError('Clean prior audit required')
    holdings=old['last_audit']['positions'];cash=float(old['last_audit']['cash'])
    if holdings!=audit['inventory']:raise ValueError('Baseline holdings disagree')
    nav=cash+sum(n*q[c]['last'] for c,n in holdings.items())
    m=pd.read_parquet(ROOT/'data_expansion/normalized/current_index_membership.parquet')
    union=set(m.symbol.map(normalize_code));c500=set(m.loc[m['index'].eq('csi500'),'symbol'].map(normalize_code))
    weights={};excluded=[]
    for row in record['stocklist']:
        c=normalize_code(str(row['C_STOCKCODE']));n=float(row['L_NUMBER'])
        if n<=0:continue
        if c not in union or q[c]['is_st'] or not q[c]['tradable']:
            excluded.append(dict(code=c,reason='outside_existing_index_union' if c not in union else 'ST_or_untradeable'));continue
        weights[c]=n*q[c]['previous_close']
    targets,ideal,value=allocate(weights,{c:q[c]['last'] for c in weights},nav*.50)
    for c in holdings:targets.setdefault(c,0)
    reductions={c:holdings[c]-targets[c] for c in holdings if holdings[c]>targets[c]}
    trades=[dict(code=c,quantity=targets[c]-holdings.get(c,0),reference_price=q[c]['last']) for c in sorted(targets) if targets[c]>holdings.get(c,0)]
    reserve=sum(o['quantity']*o['reference_price']*1.03*1.001+10 for o in trades)
    carried=float(old['carried_reserve'])+sum(float(x['reserve']) for x in old['attempts'])
    total_worst=carried+reserve
    if total_worst>5500000:raise ValueError('Worst guarded prices and fees exceed total cap')
    if reductions:raise ValueError('Target requires reductions; buy-only continuation cannot claim full rebalance: '+str(reductions))
    if any(c.startswith('SH688') and 0<targets[c]-holdings.get(c,0)<200 for c in targets):raise ValueError('STAR addition below verified conservative minimum')
    plan=dict(prepared_at=now_cn().isoformat(),execution_date='2026-09-22',pcf_date='2026-09-21',
              pcf_sha256=digest({k:record[k] for k in ('date','baseinfo','stocklist')}),
              target_fraction=.50,capital_limit=5500000,max_security_value=500000,max_order_value=500000,
              price_guard=.03,spacing_seconds=5,signal_close_nav=nav,baseline_cash=cash,baseline_positions=holdings,
              baseline_actual_paid=float(audit['actual_paid_cny']),carried_reserve=carried,
              targets={c:dict(code=c,quantity=targets[c],reference_price=q[c]['last'],member_csi500=c in c500) for c in targets},
              estimated_target_market_value=value,estimated_target_fraction=value/nav,estimated_new_orders=len(trades),
              estimated_new_principal=sum(o['quantity']*o['reference_price'] for o in trades),
              guarded_worst_total_reserved=total_worst,excluded=excluded,
              eligible_names=len(weights),membership_snapshot=sorted(m.snapshot_date.astype(str).unique().tolist()),
              weight_policy='Relative PCF quantities times PCF prior-session close, normalized within the unchanged allowed index union; integer lots allocated to nominal 50% of signal-close NAV',
              requires_sell_orders=False,fee_per_side_research=.0002)
    write_json(DEST/'plan.json',plan)
    print(json.dumps({k:v for k,v in plan.items() if k not in ['targets','baseline_positions']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
