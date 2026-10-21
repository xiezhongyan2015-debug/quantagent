"""Exactly one approved 2026-09-21 initial-build simulation, hard cap CNY 2m."""
import datetime as dt
import json
import os
from pathlib import Path
import sys

from verify_readonly import ROOT,session_tokens
from quanta_agents.factor_lab_a.pcf_automation import CN,execute_job,write_json,read_json,now_cn
from quanta_agents.factor_lab_a.choice_pcf import TradeError

DEST=ROOT/'research/choice_pcf_automation/launch_20260921'
ACCOUNT=__import__('os').environ['CHOICE_SIMULATION_ACCOUNT']


def main():
    if sys.argv[1:]!=['--execute-approved-simulation']:
        raise TradeError('Explicit approved simulation execution flag required')
    now=now_cn()
    if now.date().isoformat()!='2026-09-21' or not dt.time(9,35)<=now.time()<=dt.time(9,50):
        raise TradeError('Only start on 2026-09-21 09:35-09:50 China time; no late catch-up')
    cfg=read_json(DEST/'pcf_automation.local.json')
    if (cfg.get('capital_limit')!=2000000 or cfg.get('max_batch_value')>2000000 or
        cfg.get('max_order_value')>100000 or not cfg.get('initial_build_test') or
        not cfg['choice_contract'].get('initial_buy_only') or
        cfg.get('allowed_execution_date')!='2026-09-21' or not cfg.get('cancel_on_timeout')):
        raise TradeError('Approved launch boundaries were changed')
    keys={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    previous={x:os.environ.get(x) for x in list(keys.values())+['CHOICE_ACCOUNT_ID','PYTHONPATH']}
    report={'started_at':now.isoformat(),'account_tail':ACCOUNT[-4:],'capital_limit':2000000,
            'mode':'contest_simulation_initial_buy_only','status':'preflight'}
    tokens={}
    try:
        tokens=session_tokens(tuple(keys))
        for key,env in keys.items(): os.environ[env]=tokens[key]
        os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        os.environ['PYTHONPATH']=str(ROOT/'references/quantagent-pr/src')
        # Durable launch gate in addition to the account-bound SQLite journal.
        with (DEST/'launch_attempt.json').open('x',encoding='utf-8') as f:
            json.dump(report,f); f.flush(); os.fsync(f.fileno())
        report['result']=execute_job(cfg,'choice')
        report['status']='complete'
    except Exception as error:
        report.update(status='stopped_review_required',error_type=type(error).__name__)
        if isinstance(error,TradeError): report['reason']=str(error)
    finally:
        tokens.clear()
        for env,value in previous.items():
            if value is None: os.environ.pop(env,None)
            else: os.environ[env]=value
        report['finished_at']=now_cn().isoformat()
        write_json(DEST/'launch_result.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try: main()
    except Exception as error:
        print(str(error) if isinstance(error,TradeError) else type(error).__name__)
        raise SystemExit(1)
