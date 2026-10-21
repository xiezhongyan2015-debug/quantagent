"""Read-only PCF and closing-quote preparation for the approved simulation day."""
import concurrent.futures as cf
import datetime as dt
import json
import shutil
from verify_readonly import ROOT
from quanta_agents.factor_lab_a.pcf import normalize_code, validate_pcf
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import read_json, write_json, now_cn, digest
from plan_20260922 import allocate
import pandas as pd

DEST=ROOT/'research/choice_pcf_automation/session_20260923'
SOURCE=ROOT/'research/choice_pcf_automation/afternoon_resume_20260922'
SIGNAL='2026-09-22';DAY='2026-09-23'


def main():
    now=now_cn()
    if now.date().isoformat()!=SIGNAL or now.time()<dt.time(15,5):raise RuntimeError('Signal-evening preparation only')
    DEST.mkdir(exist_ok=True)
    public=ROOT/'research/choice_pcf_automation/daily_public_pcf'
    arc=public/'state/archive'/SIGNAL
    record=read_json(arc/'pcf.json');validate_pcf(record)
    manifest=read_json(arc/'manifest.json')
    if record['date']!=SIGNAL or manifest['execution_date']!=DAY or (arc/'REVISION_BLOCK.json').exists():raise RuntimeError('Wrong/revised PCF')
    frozen=DEST/'state/archive'/SIGNAL;frozen.mkdir(parents=True,exist_ok=True)
    for name in ['pcf.json','manifest.json']:
        target=frozen/name
        if target.exists() and digest(read_json(target))!=digest(read_json(arc/name)):raise RuntimeError('Frozen PCF changed')
        if not target.exists():shutil.copy2(arc/name,target)
    write_json(DEST/'calendar.json',read_json(public/'calendar.json'))
    state=read_json(SOURCE/'private_state.json');proof=read_json(SOURCE/'independent_readonly_audit.json')
    result=read_json(SOURCE/'result_redacted.json')
    if proof['issues'] or proof['pending'] or float(proof['frozen_cny']) or result['status']!='closed':raise RuntimeError('Clean account audit required')
    if proof['submitted']!=len(state['attempts']):raise RuntimeError('Journal count mismatch')
    holdings=state['last_audit']['positions']
    codes=sorted({normalize_code(str(x['C_STOCKCODE'])) for x in record['stocklist'] if float(x['L_NUMBER'])>0}|set(holdings))
    path=DEST/'close_quotes.json';quotes=read_json(path) if path.exists() else {};errors={}
    def fetch(code):
        q=quote(code);stamp=dt.datetime.fromisoformat(q['timestamp'])
        if stamp.date().isoformat()!=SIGNAL or stamp.time()<dt.time(15) or stamp>now_cn() or q['last']<=0:raise RuntimeError('Invalid closing quote')
        return q
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(fetch,c):c for c in codes if c not in quotes}
        for i,job in enumerate(cf.as_completed(jobs),1):
            c=jobs[job]
            try:quotes[c]=job.result()
            except Exception as error:errors[c]=type(error).__name__
            write_json(path,quotes)
            if i%30==0:print(f'Closing quotes {i}/{len(jobs)}',flush=True)
    write_json(DEST/'preparation.json',dict(at=now_cn().isoformat(),required=len(codes),quotes=len(quotes),errors=errors,account_writes=0))
    if errors or set(codes)-set(quotes):raise RuntimeError('Closing quote coverage incomplete')
    cash=float(state['last_audit']['cash']);nav=cash+395.66+sum(n*quotes[c]['last'] for c,n in holdings.items())
    m=pd.read_parquet(ROOT/'data_expansion/normalized/current_index_membership.parquet')
    union=set(m.symbol.map(normalize_code));c500=set(m.loc[m['index'].eq('csi500'),'symbol'].map(normalize_code))
    weights={};excluded=[]
    for row in record['stocklist']:
        c=normalize_code(str(row['C_STOCKCODE']));n=float(row['L_NUMBER'])
        if n<=0:continue
        if c not in union or quotes[c]['is_st']:
            excluded.append(dict(code=c,reason='outside_existing_index_union' if c not in union else 'ST'));continue
        weights[c]=n*quotes[c]['previous_close']
    targets,ideal,value=allocate(weights,{c:quotes[c]['last'] for c in weights},nav*.5)
    for c in holdings:targets.setdefault(c,0)
    lot_adjustments=[]
    for c,n in list(targets.items()):
        held=holdings.get(c,0);delta=n-held
        if c.startswith('SH688') and 0<abs(delta)<200 and not (delta<0 and n==0):
            lot_adjustments.append(dict(code=c,ideal_lot_target=n,executable_target=held,reason='STAR minimum 200-share adjustment; retain existing holding'))
            targets[c]=held
    value=sum(n*quotes[c]['last'] for c,n in targets.items())
    reductions={c:holdings[c]-targets[c] for c in holdings if holdings[c]>targets[c]}
    buys={c:targets[c]-holdings.get(c,0) for c in targets if targets[c]>holdings.get(c,0)}
    plan=dict(prepared_at=now_cn().isoformat(),execution_date=DAY,pcf_date=SIGNAL,
        pcf_sha256=digest({k:record[k] for k in ('date','baseinfo','stocklist')}),
        target_fraction=.5,capital_limit=5500000,max_security_value=500000,max_order_value=500000,
        buy_upper_guard=.03,buy_drop_review=.03,sell_deviation_guard=None,spacing_seconds=5,
        signal_close_nav=nav,baseline_cash=cash,baseline_positions=holdings,baseline_actual_paid=state['last_audit']['paid'],
        excluded_cash_cny='395.66',targets={c:dict(code=c,quantity=n,reference_price=quotes[c]['last'],member_csi500=c in c500) for c,n in targets.items()},
        estimated_target_market_value=value,estimated_target_fraction=value/nav,buys=buys,sells=reductions,
        requires_sell_orders=bool(reductions),estimated_buy_principal=sum(n*quotes[c]['last'] for c,n in buys.items()),
        estimated_sell_principal=sum(n*quotes[c]['last'] for c,n in reductions.items()),excluded=excluded,
        new_pcf_only=True,simulation_only=True,lot_adjustments=lot_adjustments,source_state_sha256=digest(state),source_audit_sha256=digest(proof))
    write_json(DEST/'plan.json',plan)
    print(json.dumps({k:v for k,v in plan.items() if k not in ('targets','baseline_positions','excluded')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
