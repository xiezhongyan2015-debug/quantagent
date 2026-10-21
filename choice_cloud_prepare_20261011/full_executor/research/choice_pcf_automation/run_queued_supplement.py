"""Complete missing PCF targets after exact reconciliation, never duplicate fills."""
import json
import os
import sys
from pathlib import Path
import requests
from run_queued_basket import DEST as QUEUED,OLD,RETEST,PREVIOUS,ACCOUNT,DAY
from run_full_basket import ROOT,BasketSession,journal_read,verify_prior,final_audit
from verify_readonly import session_tokens
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,TradeError
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import read_json,write_json,now_cn,calendar,validate_session,digest,Journal,lock,order_cash_reserve
from batch_engine import execute_batch

DEST=ROOT/'research/choice_pcf_automation/queued_supplement_20260921'


def main():
    if sys.argv[1:]!=['--execute-approved-missing-targets']:raise TradeError('Explicit authorized completion flag required')
    cfg=read_json(OLD/'pcf_automation.local.json')
    if now_cn().date().isoformat()!=DAY:raise TradeError('Wrong date')
    validate_session(now_cn(),calendar(cfg))
    original,_,_=journal_read(OLD)
    reviewed=read_json(QUEUED/'independent_whole_account_audit.json')
    if reviewed['issues'] or reviewed['pending_count'] or reviewed['held_securities']!=94:
        raise TradeError('Whole account must be independently reconciled before missing-target completion')
    by_source={(x['run'],x['code']):x for x in reviewed['verified_orders']}
    prior=[]
    for directory in (OLD,RETEST,PREVIOUS,QUEUED):
        plan,rows,res=journal_read(directory)
        for code,old_state,oid,old_filled in rows:
            if not oid:
                if old_state not in ('DEFERRED_PRICE','DEFERRED_PRECISION'):raise TradeError('Unknown previous submission')
                continue
            evidence=by_source[(directory.name,code)]
            if evidence['status'] not in ('4','8') or (evidence['status']=='8' and evidence['filled']):
                raise TradeError('Prior order is not full or zero-fill cancelled')
            prior.append((code,'FILLED' if evidence['status']=='4' else 'CANCELLED',str(oid),evidence['filled']))
    carried=sum(v for c,v in journal_read(QUEUED)[2])/100
    positions=reviewed['inventory']
    targets={o['code']:o for o in original['orders']}
    if any(targets[c]['quantity']!=q for c,q in positions.items()):raise TradeError('Prior position exceeds/differs from PCF target')
    orders=[dict(o) for o in original['orders'] if o['code'] not in positions]
    if len(orders)!=14:raise TradeError('Unexpected missing target set')
    if carried+sum(order_cash_reserve(cfg,o['quantity']*o['reference_price']*1.03) for o in orders)>2000000:
        raise TradeError('Worst allowed remaining orders exceed cumulative budget')
    manifest=read_json(OLD/'state/archive/2026-09-18/manifest.json');pcf=read_json(OLD/'state/archive/2026-09-18/pcf.json')
    if manifest['pcf_sha256']!=original['pcf_sha256'] or digest({k:pcf[k] for k in ('date','baseinfo','stocklist')})!=manifest['pcf_sha256']:
        raise TradeError('Archived PCF changed')
    DEST.mkdir(exist_ok=True)
    with (DEST/'supplement_attempt.json').open('x',encoding='utf-8') as f:
        json.dump({'at':now_cn().isoformat(),'account_tail':ACCOUNT[-4:],
                   'authorization':'User requested complete PCF portfolio then batch submission; explicitly approved at most 0.01 above ask only for SZ000415 and SZ002152',
                   'existing_positions':94,'missing_targets':14,'prior_reserved_cny':carried},f)
        f.flush();os.fsync(f.fileno())
    cfg.update(state_dir=str(DEST/'state'),authorized_padding_ticks_by_code={'SZ000415':1,'SZ002152':1})
    Path(cfg['state_dir']).mkdir(exist_ok=True)
    session=BasketSession(DEST/'http_trace_redacted.jsonl',carried);public=requests.Session();journal=None;broker=None
    keys={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    oldenv={k:os.environ.get(k) for k in [*keys.values(),'CHOICE_ACCOUNT_ID']}
    report={'started_at':now_cn().isoformat(),'status':'preflight','account_tail':ACCOUNT[-4:],'capital_limit':2000000,
            'already_held_count':94,'missing_targets':14,'carried_reserved_cny':carried,'padding_ticks_by_code':cfg['authorized_padding_ticks_by_code']}
    try:
        tokens=session_tokens(tuple(keys))
        for k,v in keys.items():os.environ[v]=tokens[k]
        tokens.clear();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        with lock(OLD/'state/choice.lock'),lock(RETEST/'state/choice.lock'),lock(PREVIOUS/'state/choice.lock'),lock(QUEUED/'state/choice.lock'),lock(Path(cfg['state_dir'])/'choice.lock'):
            broker=ChoiceBroker(cfg['choice_contract'],True,session)
            cancelled,state=verify_prior(broker,original,prior,positions)
            broker.contract['verified_zero_fill_cancellations']=cancelled
            session.expected={o['code']:o for o in orders}
            journal=Journal(Path(cfg['state_dir'])/'choice.sqlite',ACCOUNT)
            journal.reserve_capital(DAY,'PRIOR_APPROVED_ATTEMPTS',carried,2000000)
            plan=dict(original,orders=orders,initial_positions=positions)
            write_json(DEST/'plan.json',plan)
            states={oid:('4' if status=='FILLED' else '8',filled) for code,status,oid,filled in prior}
            report['result']=execute_batch(cfg,plan,broker,journal,lambda code:quote(code,public),states)
            report['status']=report['result']['status']
    except Exception as error:
        report.update(status='stopped_review_required',error_type=type(error).__name__)
        if isinstance(error,TradeError):report['reason']=str(error)
    finally:
        if journal is not None:
            try:report['audit']=final_audit(broker,journal)
            except Exception as error:report['audit_error_type']=type(error).__name__
            journal.db.close()
        report.update(finished_at=now_cn().isoformat(),submit_requests=len(session.sent),cancel_requests=len(session.cancelled),transport_reserved_cny=float(session.reserved))
        for k,v in oldenv.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close();public.close();write_json(DEST/'supplement_result.json',report)
        print(json.dumps({k:v for k,v in report.items() if k!='audit'},ensure_ascii=False,indent=2))
        if 'audit' in report:
            a=report['audit'];print(json.dumps({'balance':a['balance'],'holdings_count':len(a['holdings']),
                                                'pending_count':len(a['account_pending_orders']),'reserved_cny':a['cumulative_reserved_cny']}))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,TradeError) else type(error).__name__);raise SystemExit(1)
