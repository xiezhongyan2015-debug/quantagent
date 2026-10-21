"""Read-only preparation of the authorized 2026-09-21, <=2m contest simulation trial."""
import concurrent.futures as cf
import datetime as dt
import json
from pathlib import Path
import sys

from verify_readonly import ROOT
from quanta_agents.factor_lab_a.pcf import download_pcf,build_target,normalize_code
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import CN,write_json,digest,order_cash_reserve
import pandas as pd

DEST=ROOT/'research/choice_pcf_automation/launch_20260921'
REPO=ROOT/'references/quantagent-pr'


def main():
    DEST.mkdir(exist_ok=True)
    now=dt.datetime.now(CN)
    if now.date().isoformat()!='2026-09-20': raise ValueError('Preparation window expired')
    archive=DEST/'state/archive/2026-09-18'
    if (archive/'manifest.json').exists(): raise ValueError('Preserve original launch archive')
    record=download_pcf('563030','2026-09-18',str(archive/'pcf.json'))
    write_json(archive/'manifest.json',{'pcf_date':'2026-09-18','execution_date':'2026-09-21',
        'pcf_sha256':digest({k:record[k] for k in ('date','baseinfo','stocklist')}),
        'captured_at':now.isoformat(),'historical_publication_timestamp_verified':False,
        'note':'Previous-session PCF captured on Sunday, before Monday; timestamp not backdated.'})
    days=['2026-09-17','2026-09-18','2026-09-21','2026-09-22']
    write_json(DEST/'calendar.json',days)
    member=pd.read_parquet(ROOT/'data_expansion/normalized/current_index_membership.parquet')
    if set(member.snapshot_date.astype(str))!={'20260917'}: raise ValueError('Unexpected membership snapshot')
    union=set(member.symbol.map(normalize_code))
    c500=set(member.loc[member['index'].eq('csi500'),'symbol'].map(normalize_code))
    codes=[normalize_code(str(x['C_STOCKCODE'])) for x in record['stocklist']]
    rows=[]; quotes={}; exclusions=[]
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(quote,c):c for c in codes}
        for i,f in enumerate(cf.as_completed(jobs),1):
            code=jobs[f]; q=f.result(); quotes[code]=q
            source_date=dt.datetime.fromisoformat(q['timestamp']).date().isoformat()
            if source_date>'2026-09-18': raise ValueError('Quote is after signal date')
            tradable=int(q['tradable'] and source_date=='2026-09-18' and code in union)
            if not tradable or q['is_st']: exclusions.append(code)
            rows.append({'code':code,'date':'2026-09-18','close':q['last'],
                'reference_date':'2026-09-17',
                'reference_close':q['previous_close'] if source_date=='2026-09-18' else q['last'],
                'member_csi500':int(code in c500),'member_union':int(code in union),
                'is_st':q['is_st'],'tradable':tradable,'source_timestamp':q['timestamp'],
                'membership_snapshot_date':'2026-09-17'})
            if i%40==0: print('Read-only public quotes',i,'/',len(codes),flush=True)
    frame=pd.DataFrame(rows).sort_values('code'); frame.to_csv(DEST/'close_quotes.csv',index=False)
    write_json(DEST/'live_quotes.json',quotes)
    cfg=json.loads((REPO/'config/pcf_automation.example.json').read_text(encoding='utf-8'))
    cfg.update(state_dir=str(DEST/'state'),calendar=str(DEST/'calendar.json'),
        close_quotes=str(DEST/'close_quotes.csv'),live_quotes=str(DEST/'live_quotes.json'),
        paper_account=str(DEST/'paper.json'),enable_choice_orders=True,exclusive_account=True,
        capital_limit=2000000,initial_build_test=True,allowed_execution_date='2026-09-21',
        max_orders=200,max_order_value=100000,max_batch_value=2000000,
        fill_timeout_seconds=30,cancel_on_timeout=True,cancel_timeout_seconds=30,
        execution_fee_buffer_per_order=10,quote_max_age_seconds=30,
        quote_command=['D:/Anaconda/python.exe','-m','quanta_agents.factor_lab_a.choice_quotes',
                       '--code','{code}','--out',str(DEST/'live_quotes.json')])
    cfg['choice_contract'].update(order_id='Data.orderID',initial_buy_only=True,
        validated_submission_with_authorized_test=True,validated_cancel_with_authorized_test=True)
    write_json(DEST/'pcf_automation.local.json',cfg)
    target,summary=build_target(record,frame,pd.DataFrame(columns=['code','shares']),2000000,
                               days,'2026-09-21','2026-09-18')
    target.to_csv(DEST/'target_preview.csv',index=False)
    buys=target.loc[target.delta_shares>0]
    summary.update(preview_only=True,capital_limit=2000000,excluded_or_frozen=exclusions,
        estimated_gross=float(buys.delta_mv.sum()),
        estimated_cash_reserve=sum(order_cash_reserve(cfg,float(x.delta_mv)) for x in buys.itertuples()),
        orders=int(len(buys)),membership_note='2026-09-17 current snapshot carried forward, known before test',
        account_minimum_position_requirement_not_met_by_capped_trial=True)
    write_json(DEST/'target_preview.json',summary)
    print(json.dumps({'orders':summary['orders'],'estimated_gross':summary['estimated_gross'],
        'estimated_cash_reserve':summary['estimated_cash_reserve'],'excluded_or_frozen':exclusions,
        'precheck':summary['precheck']},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
