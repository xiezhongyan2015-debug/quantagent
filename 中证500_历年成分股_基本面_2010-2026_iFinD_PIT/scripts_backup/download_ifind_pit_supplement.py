"""Download four additional PIT statement fields at in-pool weekly dates.

This is an explicitly partial field phase. Cash flow and remaining unverified
fields must be added separately before the complete fundamentals task is done.
"""
import argparse
import hashlib
import json
import os
import time
import traceback
from datetime import datetime

import pandas as pd

from download_ifind_fundamental_market import CONTROL, DEST, Download, read, save

FIELDS = {
    'cash':'ths_currency_fund_pit_stock',
    'short_term_borrowing':'ths_st_borrow_pit_stock',
    'long_term_borrowing':'ths_lt_loan_pit_stock',
    'rd_expense':'ths_rad_cost_sum_pit_stock',
}
PENDING = ['deducted_parent_net_profit','operating_cash_flow','capex_cash_paid','historical_industry']


class PITSupplement(Download):
    def __init__(self):
        super().__init__()
        self.plan=pd.read_parquet(DEST/'plan/pit_version_requests.parquet')
        self.groups=list(self.plan.groupby(['asof_date','report_period'],sort=True))
        self.db.execute('CREATE TABLE IF NOT EXISTS pit_supplement4(batch TEXT PRIMARY KEY,rows INTEGER,cells INTEGER,path TEXT,sha256 TEXT,completed TEXT)')
        (DEST/'financial/supplement4').mkdir(exist_ok=True)
        save(DEST/'audit/supplement_field_dictionary.json',dict(fields=FIELDS,pending_fields=PENDING,
            monetary_unit='CNY',flow_period='reported year-to-date, not single-quarter',
            temporal_convention='As-of end of day; usable for trading from the following session.',
            null_policy='Provider nulls remain null. No conversion to zero or automatic null retries.',
            verification='SDK accepted each code in bounded samples. Comprehensive coverage and semantic audit remain pending.'))

    def progress(self,status,**extra):
        count,rows,cells=self.db.execute('SELECT COUNT(*),COALESCE(SUM(rows),0),COALESCE(SUM(cells),0) FROM pit_supplement4').fetchone()
        save(DEST/'progress.json',dict(status=status,phase='pit_supplement_4_fields',pid=os.getpid(),
            updated_at=datetime.now().isoformat(),completed_batches=count,planned_batches=len(self.groups),
            completed_financial_version_rows=rows,planned_financial_version_rows=len(self.plan),
            supplement_data_cells=cells,active_requests=len(self.pending),
            completed_core_fields=13,completed_core_version_rows=79880,
            completed_weekly_market_rows=len(self.universe),downloaded_report_event_pairs=50615,
            latest_basic_quota=self.current_quota,pending_fields=PENDING,
            full_fundamentals_complete=False,**extra))

    def validate_write(self,key,metadata,result):
        codes=set(metadata.thscode)
        values={}
        for item in result.get('data',[]):
            code=item['thscode'];table=item['table']
            if code in values or set(table)!=set(FIELDS.values()):
                raise RuntimeError(f'Duplicate stock or missing fields in {key}')
            if any(len(table[field])!=1 for field in FIELDS.values()):
                raise RuntimeError('Unexpected PIT value shape')
            values[code]={name:table[field][0] for name,field in FIELDS.items()}
        if set(values)!=codes:
            raise RuntimeError(f'PIT security coverage mismatch: {key}')
        frame=metadata.copy()
        for name in FIELDS:
            frame[name]=pd.to_numeric([values[c][name] for c in frame.thscode],errors='raise')
        if (frame.source_update_date>frame.asof_date).any() or (frame.report_period>frame.asof_date).any():
            raise RuntimeError('Future-dated financial version')
        target=DEST/'financial/supplement4'/(key+'.parquet');tmp=target.with_suffix('.tmp')
        frame.to_parquet(tmp,index=False);tmp.replace(target)
        self.db.execute('INSERT OR REPLACE INTO pit_supplement4 VALUES(?,?,?,?,?,?)',
            (key,len(frame),int(result.get('dataVol') or len(frame)*len(FIELDS)),
             str(target.relative_to(DEST)),hashlib.sha256(target.read_bytes()).hexdigest(),datetime.now().isoformat()))
        self.db.commit()

    def audit(self):
        files=self.db.execute('SELECT path,sha256 FROM pit_supplement4').fetchall()
        frames=[]
        for path,digest in files:
            p=DEST/path
            if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise RuntimeError('PIT hash mismatch')
            frames.append(pd.read_parquet(p))
        data=pd.concat(frames,ignore_index=True).sort_values(['asof_date','thscode','report_period'])
        keys=['asof_date','thscode','report_period']
        coverage=data[keys].merge(self.plan[keys],on=keys,how='outer',indicator=True,validate='one_to_one')
        if not (coverage['_merge']=='both').all():raise RuntimeError('PIT planned coverage mismatch')
        membership=data[['asof_date','thscode']].merge(self.universe[['asof_date','thscode']],on=['asof_date','thscode'],how='left',indicator=True,validate='many_to_one')
        if not membership['_merge'].eq('both').all():raise RuntimeError('Outside historical membership')
        if data.groupby('asof_date').thscode.nunique().max()>500:raise RuntimeError('More than 500 names at one time')
        data.to_parquet(DEST/'financial_supplement_4_fields.parquet',index=False)
        core=pd.read_parquet(DEST/'financial_core_13_fields.parquet')
        combined=core.merge(data[keys+list(FIELDS)],on=keys,how='outer',validate='one_to_one',indicator=True)
        if not combined['_merge'].eq('both').all():raise RuntimeError('Core and supplement coverage mismatch')
        combined.drop(columns='_merge').to_parquet(DEST/'financial_verified_17_fields.parquet',index=False)
        report=dict(status='supplement_phase_verified',rows=len(data),fields=list(FIELDS),
            null_counts={name:int(data[name].isna().sum()) for name in FIELDS},
            duplicate_keys=0,planned_coverage_exact=True,all_file_hashes_verified=True,
            pending_fields=PENDING,full_fundamentals_complete=False)
        save(DEST/'audit/pit_supplement4_quality_report.json',report)
        return report

    def run(self,limit=None):
        done={x[0] for x in self.db.execute('SELECT batch FROM pit_supplement4')}
        groups=[(dates,frame) for dates,frame in self.groups if f'pit_supplement4_{dates[0]}_{dates[1]}' not in done]
        if limit:groups=groups[:limit]
        remaining=self.quota()
        remaining_cost=sum(len(df)*len(FIELDS) for _,df in groups)
        if remaining_cost+len(self.plan)*3+len(self.universe)+150000>remaining:raise RuntimeError('Insufficient quota plus safety reserve')
        completed=cursor=0;self.progress('downloading')
        while cursor<len(groups) or self.pending:
            stopped=(DEST/'STOP').exists()
            if stopped and not self.pending:self.progress('paused');return
            worker=read(CONTROL/'worker_status.json')
            if worker['status'] in ('blocked','crashed','stopped','login_failed','login_cancelled'):
                raise RuntimeError(f'Worker stopped new requests; checkpoint retained: {worker}')
            if completed>=50 and not self.pending:self.quota();completed=0
            while cursor<len(groups) and len(self.pending)<12 and completed<50 and not stopped:
                (date,period),frame=groups[cursor]
                key=f'pit_supplement4_{date}_{period}'
                params=f'{date},{period.replace("-","")},1'
                self.submit(key,'THS_BD',[','.join(frame.thscode),';'.join(FIELDS.values()),';'.join([params]*len(FIELDS))],{'format':'format:list'})
                self.pending[key]=(frame,time.monotonic());cursor+=1
            for key,(frame,start) in list(self.pending.items()):
                result=self.consume(key)
                if result is None:
                    if time.monotonic()-start>300:raise RuntimeError(f'Timeout: {key}; do not duplicate request')
                    continue
                self.validate_write(key,frame,result);self.cleanup(key);del self.pending[key];completed+=1
                self.progress('downloading',last_completed_batch=key)
            time.sleep(.15)
        self.quota()
        report=None if limit else self.audit()
        self.progress('supplement_sample_complete' if limit else 'supplement_4_complete_remaining_fields_pending',supplement_audit=report)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int);args=parser.parse_args()
    download=PITSupplement()
    try:download.run(args.limit)
    except Exception:
        download.progress('blocked',error=traceback.format_exc());raise
