"""Frozen nine-stock integration case; uses only an already audited cache."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'experiment_traces/meta_ashare_revision5'
DATA=ROOT/'experiment_traces/meta_raw_daily_data_pilot'
OUT=ROOT/'experiment_traces/meta_raw_portfolio_pilot'
PDF=ROOT/'experiment_traces/meta_corporate_action_sources/20260905T175154Z/pingan_2019_031.pdf'
CODES=['sh600006','sh603786','sh600653','sh600741','sz002296','sz002377','sz002576','sz002527','sz000001']


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path): return json.loads(path.read_text(encoding='utf-8'))
def save(path,obj): path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'plan.json').exists(): raise RuntimeError('Pilot plan already frozen')
    upstream=read(DATA/'result.json')
    for name, expected in upstream['artifact_hashes'].items():
        if sha(DATA/name)!=expected: raise RuntimeError('Frozen raw cache changed')
    targets=[]
    for signal,trade,weight in [('2019-06-21','2019-06-24','0.10'),('2019-06-25','2019-06-26','0')]:
        targets.extend({'symbol':code,'signal_date':signal,'trade_date':trade,
                        'available_at':signal+'T15:05:00+08:00','target_weight':weight} for code in CODES)
    plan={'created_at':datetime.now(timezone.utc).isoformat(),'case':'fixed-raw-portfolio-accounting-integration-v1',
          'codes':CODES,'calendar':['2019-06-20','2019-06-21','2019-06-24','2019-06-25','2019-06-26','2019-06-27','2019-06-28'],
          'start_date':'2019-06-21','end_date':'2019-06-28','initial_cash':'1000000.00','targets':targets,
          'participation_rate':'0.05','rounding_policy':'aggregate_half_up_simulated',
          'allow_incomplete_for_integration':True,'coverage_status':'one documented event only; universe coverage incomplete',
          'unlisted_policy':'keep requested target and rejection; retain unused cash within full initial capital',
          'selection_basis':'same hash-selected 8 names plus previously fixed Pingan dividend case; dates fixed before portfolio output',
          'purpose':'economic integration, no strategy search or Sharpe certification',
          'input_hashes':upstream['artifact_hashes'],'upstream_result_sha256':sha(DATA/'result.json'),
          'source_pdf_sha256':sha(PDF),'model':'not_called','execution_valid':False}
    save(OUT/'plan.json',plan)
    save(OUT/'plan_identity.json',{'sha256':sha(OUT/'plan.json')})
    print(json.dumps({'plan_sha256':sha(OUT/'plan.json'),'requested_codes':len(CODES),'target_rows':len(targets)}),flush=True)


def run():
    plan=read(OUT/'plan.json')
    if sha(OUT/'plan.json')!=read(OUT/'plan_identity.json')['sha256']: raise RuntimeError('Pilot plan changed')
    for name,expected in plan['input_hashes'].items():
        if sha(DATA/name)!=expected: raise RuntimeError('Raw pilot input changed')
    if sha(PDF)!=plan['source_pdf_sha256']: raise RuntimeError('Document identity changed')
    attempt=OUT/'attempts'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    attempt.mkdir(parents=True,exist_ok=False)
    sys.path.insert(0,str(STAGE/'src'))
    from quanta_agents.corporate_action_adapter import load_pingan_2019_031
    from quanta_agents.raw_portfolio_backtest import simulate_raw_portfolio
    inputs=pd.read_parquet(DATA/'raw_daily_rows.parquet')
    inputs.attrs['availability_contract']=read(DATA/'availability_contract.json')
    try:
        result=simulate_raw_portfolio(daily_data=inputs,calendar=plan['calendar'],targets=pd.DataFrame(plan['targets']),
              initial_cash=plan['initial_cash'],corporate_actions=[load_pingan_2019_031(PDF)],
              allow_incomplete_for_integration=True,participation_rate=plan['participation_rate'],
              start_date=plan['start_date'],end_date=plan['end_date'],rounding_policy=plan['rounding_policy'])
        save(attempt/'result.json',result)
        save(attempt/'receipt.json',{'status':'simulation_completed_pending_independent_audit',
             'plan_sha256':sha(OUT/'plan.json'),'result_sha256':sha(attempt/'result.json'),
             'code_sha256':{str(path.relative_to(ROOT)):sha(path) for path in [Path(__file__),
               STAGE/'src/quanta_agents/raw_portfolio_backtest.py',STAGE/'src/quanta_agents/raw_share_ledger.py',
               STAGE/'src/quanta_agents/corporate_action_adapter.py',STAGE/'src/quanta_agents/raw_daily_data.py']},
             'model_calls':0,'strategy_target_success':False,'execution_valid':False})
    except Exception as exc:
        save(attempt/'failure.json',{'error_type':type(exc).__name__,'error':str(exc),'plan_sha256':sha(OUT/'plan.json')})
        raise
    save(OUT/'latest_attempt.json',{'attempt':str(attempt.relative_to(ROOT)),'result_sha256':sha(attempt/'result.json')})
    print(json.dumps({'attempt':str(attempt),'keys':list(result),'execution_valid':False},ensure_ascii=False),flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['prepare','run'])
    args=parser.parse_args(); prepare() if args.action=='prepare' else run()
