"""User explicitly requested submit-all-then-reconcile PCF completion."""
import os
import sys
import json
from pathlib import Path
import requests

from run_full_basket import (
    ACCOUNT, DAY, OLD, RETEST, DEST as PREVIOUS, ROOT, USER_DEFERRED,
    BasketSession, journal_read, verify_prior, final_audit,
)
from verify_readonly import session_tokens
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,TradeError
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import (
    read_json,write_json,now_cn,calendar,validate_session,digest,Journal,lock,order_cash_reserve,
)
from batch_engine import execute_batch

DEST=ROOT/'research/choice_pcf_automation/queued_basket_20260921'


def main():
    if sys.argv[1:]!=['--execute-authorized-queued-basket']:raise TradeError('Explicit queued-basket authorization required')
    cfg=read_json(OLD/'pcf_automation.local.json')
    if now_cn().date().isoformat()!=DAY:raise TradeError('Wrong day')
    validate_session(now_cn(),calendar(cfg))
    original,r0,c0=journal_read(OLD);p1,r1,c1=journal_read(RETEST);p2,r2,c2=journal_read(PREVIOUS)
    prior_rows=r0+r1+r2
    if any(s not in ('FILLED','CANCELLED') or not oid or (s=='CANCELLED' and filled) for c,s,oid,filled in prior_rows):
        raise TradeError('Prior submissions must all be fully reconciled before queued completion')
    base=dict(c0)
    for code,cents in c1:
        if code in base and base[code]!=cents:raise TradeError('Prior reservation mismatch')
        base[code]=cents
    previous_capital=dict(c2)
    if previous_capital.get('PRIOR_APPROVED_ATTEMPTS')!=sum(base.values()):
        raise TradeError('Carried capital chain is inconsistent')
    carried=sum(previous_capital.values())/100
    positions={}
    for code,state,oid,filled in prior_rows:
        if filled:positions[code]=positions.get(code,0)+filled
    targets={o['code']:o for o in original['orders']}
    if any(targets[c]['quantity']!=q for c,q in positions.items()):raise TradeError('Existing shares do not match original PCF targets')
    orders=[dict(o) for o in original['orders'] if o['code'] not in positions and o['code'] not in USER_DEFERRED]
    if len(original['orders'])!=108 or len(positions)!=8 or len(orders)!=95:raise TradeError('Unexpected continuation quantities')
    if carried+sum(order_cash_reserve(cfg,o['quantity']*o['reference_price']*1.03) for o in orders)>2000000:
        raise TradeError('Worst allowed prices exceed total budget')
    manifest=read_json(OLD/'state/archive/2026-09-18/manifest.json');pcf=read_json(OLD/'state/archive/2026-09-18/pcf.json')
    if manifest['pcf_sha256']!=original['pcf_sha256'] or digest({k:pcf[k] for k in ('date','baseinfo','stocklist')})!=manifest['pcf_sha256']:
        raise TradeError('PCF changed')
    if cfg['quote_max_age_seconds']!=30 or cfg['capital_limit']!=2000000 or cfg['max_order_value']!=100000 or cfg['max_price_move']!=.03:
        raise TradeError('Approved quote/price/capital limits changed')
    DEST.mkdir(exist_ok=True)
    with (DEST/'queued_attempt.json').open('x',encoding='utf-8') as f:
        json.dump({'at':now_cn().isoformat(),'authorization':'User asked to submit all remaining PCF orders first, then check fills; cap CNY2m, original 3% guard and five deferrals retained',
                   'account_tail':ACCOUNT[-4:],'remaining_targets':95,'prior_reserved_cny':carried},f)
        f.flush();os.fsync(f.fileno())
    cfg.update(state_dir=str(DEST/'state'))
    Path(cfg['state_dir']).mkdir(exist_ok=True)
    session=BasketSession(DEST/'http_trace_redacted.jsonl',carried)
    quote_session=requests.Session()
    keys={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    oldenv={k:os.environ.get(k) for k in [*keys.values(),'CHOICE_ACCOUNT_ID']}
    journal=None;broker=None
    report={'started_at':now_cn().isoformat(),'status':'preflight','account_tail':ACCOUNT[-4:],'capital_limit':2000000,
            'original_target_count':108,'already_held':positions,'planned_new_orders':len(orders),'carried_reserved_cny':carried,
            'user_deferred':sorted(USER_DEFERRED),'dispatch_policy':'all submissions first, then shared readback and cancellation of unresolved known orders'}
    try:
        tokens=session_tokens(tuple(keys))
        for k,v in keys.items():os.environ[v]=tokens[k]
        tokens.clear();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        with lock(OLD/'state/choice.lock'),lock(RETEST/'state/choice.lock'),lock(PREVIOUS/'state/choice.lock'),lock(Path(cfg['state_dir'])/'choice.lock'):
            broker=ChoiceBroker(cfg['choice_contract'],True,session)
            cancelled,state=verify_prior(broker,original,prior_rows,positions)
            broker.contract['verified_zero_fill_cancellations']=cancelled
            prior_states={str(oid):('8' if s=='CANCELLED' else '4',filled) for c,s,oid,filled in prior_rows}
            session.expected={o['code']:o for o in orders}
            journal=Journal(Path(cfg['state_dir'])/'choice.sqlite',ACCOUNT)
            journal.reserve_capital(DAY,'PRIOR_APPROVED_ATTEMPTS',carried,2000000)
            plan=dict(original,orders=orders,initial_positions=positions)
            write_json(DEST/'plan.json',plan)
            report['result']=execute_batch(cfg,plan,broker,journal,lambda code:quote(code,quote_session),prior_states)
            report['status']=report['result']['status']
    except Exception as error:
        report.update(status='stopped_review_required',error_type=type(error).__name__)
        if isinstance(error,TradeError):report['reason']=str(error)
    finally:
        if journal is not None:
            try:report['audit']=final_audit(broker,journal)
            except Exception as error:report['audit_error_type']=type(error).__name__
            journal.db.close()
        report.update(finished_at=now_cn().isoformat(),submit_requests=len(session.sent),cancel_requests=len(session.cancelled),
                      transport_reserved_cny=float(session.reserved))
        for k,v in oldenv.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close();quote_session.close()
        write_json(DEST/'queued_result.json',report)
        print(json.dumps({k:v for k,v in report.items() if k!='audit'},ensure_ascii=False,indent=2))
        if 'audit' in report:
            a=report['audit']
            print(json.dumps({'balance':a['balance'],'holdings_count':len(a['holdings']),'pending_count':len(a['account_pending_orders']),
                              'reserved_cny':a['cumulative_reserved_cny']},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,TradeError) else type(error).__name__)
        raise SystemExit(1)
