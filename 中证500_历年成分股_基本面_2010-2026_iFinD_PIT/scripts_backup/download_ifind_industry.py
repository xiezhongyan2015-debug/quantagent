"""Historical, date-parameterized provider SW level-one codes for weekly members."""
import argparse
import hashlib
import os
import re
import time
import traceback
from datetime import datetime

import pandas as pd
from download_ifind_fundamental_market import CONTROL, DEST, Download, read, save

FIELD = 'ths_the_sw_industry_code_stock'
PENDING = ['deducted_parent_net_profit', 'operating_cash_flow', 'capex_cash_paid']


class Industry(Download):
    def __init__(self):
        super().__init__()
        self.db.execute('CREATE TABLE IF NOT EXISTS industry(asof TEXT PRIMARY KEY,rows INTEGER,cells INTEGER,path TEXT,sha256 TEXT,completed TEXT)')
        save(DEST/'audit/industry_field_dictionary.json', dict(field=FIELD, params='1,{asof_date}',
            level='first level', value='iFinD SW industry identifier (for example S43); not an exchange security code',
            source='https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/super-command-web/index.html#/BasicData',
            parameter_evidence='Official UI shows industry level and cutoff date; 2010/2019/2025 samples accepted.',
            taxonomy_version='Provider default SW series, exact revision not established. Do not label as fixed SW2021 or compare codes across revisions without a mapping.',
            label_policy='Chinese label omitted because SDK-decoded label samples contain irreversible mojibake. Original samples retained. No inferred labels.',
            temporal_convention='Historical cutoff is the in-pool week-end trading date. Signals usable from next trading session.'))

    def progress(self, status, **extra):
        batches, rows, cells = self.db.execute('SELECT COUNT(*),COALESCE(SUM(rows),0),COALESCE(SUM(cells),0) FROM industry').fetchone()
        save(DEST/'progress.json', dict(status=status, phase='weekly_historical_industry', pid=os.getpid(),
            updated_at=datetime.now().isoformat(), completed_batches=batches, planned_batches=self.universe.asof_date.nunique(),
            completed_industry_rows=rows, planned_industry_rows=len(self.universe), industry_cells=cells,
            completed_financial_fields=17, completed_financial_version_rows=79880, completed_weekly_market_rows=427995,
            active_requests=len(self.pending), latest_basic_quota=self.current_quota,
            pending_fields=PENDING, full_fundamentals_complete=False, **extra))

    def validate_write(self, date, codes, result):
        values = {}
        for item in result.get('data', []):
            code = item['thscode']; table = item['table']
            if code in values or set(table) != {FIELD} or len(table[FIELD]) != 1:
                raise RuntimeError('Invalid industry result schema')
            value = table[FIELD][0]
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', value)):
                raise RuntimeError(f'Unexpected industry code: {value!r}')
            values[code] = value
        if set(values) != set(codes) or len(codes) > 500:
            raise RuntimeError('Industry security coverage mismatch')
        frame = pd.DataFrame([dict(asof_date=date, thscode=c, sw_l1_provider_code=values[c]) for c in codes])
        output = DEST/'weekly'/f'industry_{date}.parquet'; tmp = output.with_suffix('.tmp')
        frame.to_parquet(tmp, index=False); tmp.replace(output)
        self.db.execute('INSERT OR REPLACE INTO industry VALUES(?,?,?,?,?,?)',
            (date,len(frame),int(result.get('dataVol') or len(frame)),str(output.relative_to(DEST)),
             hashlib.sha256(output.read_bytes()).hexdigest(),datetime.now().isoformat()))
        self.db.commit()

    def audit(self):
        frames=[]
        for date, count, path, digest in self.db.execute('SELECT asof,rows,path,sha256 FROM industry'):
            p=DEST/path
            if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise RuntimeError('Industry hash mismatch')
            frame=pd.read_parquet(p)
            if len(frame)!=count or set(frame.asof_date)!={date}:raise RuntimeError('Industry manifest mismatch')
            frames.append(frame)
        data=pd.concat(frames,ignore_index=True).sort_values(['asof_date','thscode'])
        keys=['asof_date','thscode']
        coverage=data[keys].merge(self.universe[keys],on=keys,how='outer',validate='one_to_one',indicator=True)
        if not coverage['_merge'].eq('both').all():raise RuntimeError('Industry membership mismatch')
        if data.groupby('asof_date').thscode.nunique().max()>500:raise RuntimeError('Too many constituents')
        output=DEST/'weekly_historical_industry.parquet'; data.to_parquet(output,index=False)
        market=pd.read_parquet(DEST/'weekly_market_cap.parquet')
        market.merge(data,on=keys,how='inner',validate='one_to_one').to_parquet(DEST/'weekly_market_and_industry.parquet',index=False)
        report=dict(status='industry_code_phase_verified',rows=len(data),weekly_dates=int(data.asof_date.nunique()),
            null_codes=int(data.sw_l1_provider_code.isna().sum()),distinct_codes=sorted(data.sw_l1_provider_code.dropna().unique().tolist()),
            duplicate_keys=0,membership_exact=True,all_file_hashes_verified=True,
            consolidated_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
            taxonomy_revision_unverified=True,labels_omitted_due_to_encoding=True,pending_fields=PENDING,full_fundamentals_complete=False)
        save(DEST/'audit/industry_quality_report.json',report)
        return report

    def run(self,limit=None):
        done={x[0] for x in self.db.execute('SELECT asof FROM industry')}
        groups=[(date,sorted(frame.thscode.tolist())) for date,frame in self.universe.groupby('asof_date') if date not in done]
        if limit:groups=groups[:limit]
        remaining=self.quota()
        if sum(len(codes) for _,codes in groups)+79880*3+150000>remaining:
            raise RuntimeError('Insufficient remaining quota for industry, pending fields and reserve')
        cursor=completed=0; self.progress('downloading')
        while cursor<len(groups) or self.pending:
            stopped=(DEST/'STOP').exists()
            if stopped and not self.pending:self.progress('paused');return
            state=read(CONTROL/'worker_status.json')
            if state['status'] in ('blocked','crashed','stopped','login_failed','login_cancelled'):
                raise RuntimeError(f'Worker unavailable: {state}')
            if completed>=50 and not self.pending:self.quota();completed=0
            while cursor<len(groups) and len(self.pending)<12 and completed<50 and not stopped:
                date,codes=groups[cursor];key='fund_industry_'+date
                self.submit(key,'THS_BD',[','.join(codes),FIELD,'1,'+date],{'format':'format:list'})
                self.pending[key]=(date,codes,time.monotonic());cursor+=1
            for key,(date,codes,started) in list(self.pending.items()):
                result=self.consume(key)
                if result is None:
                    if time.monotonic()-started>300:raise RuntimeError(f'Timeout; checkpoint retained: {key}')
                    continue
                self.validate_write(date,codes,result);self.cleanup(key);del self.pending[key];completed+=1
                self.progress('downloading',last_completed_date=date)
            time.sleep(.15)
        self.quota()
        self.progress('industry_sample_complete' if limit else 'industry_complete_three_financial_fields_pending',
            industry_audit=None if limit else self.audit())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int);args=parser.parse_args()
    worker=Industry()
    try:worker.run(args.limit)
    except Exception:
        worker.progress('blocked',error=traceback.format_exc());raise
