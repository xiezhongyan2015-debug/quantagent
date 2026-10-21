"""Download provider report-update dates before budgeting PIT financial versions.

An update date is a candidate version, not proof that every financial field
changed. The raw provider strings are preserved for audit.
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


class ReportEvents(Download):
    def __init__(self):
        super().__init__()
        self.candidates = pd.read_parquet(DEST / 'plan/financial_candidates.parquet')
        self.db.execute('CREATE TABLE IF NOT EXISTS report_events(batch TEXT PRIMARY KEY,rows INTEGER,cells INTEGER,path TEXT,sha256 TEXT,completed TEXT)')
        self.batches = []
        for period, frame in self.candidates.groupby('report_period'):
            codes = sorted(frame.thscode.tolist())
            for offset in range(0,len(codes),500):
                self.batches.append((period, offset//500, codes[offset:offset+500]))

    def progress(self, status, **extra):
        count, rows, cells = self.db.execute('SELECT COUNT(*),COALESCE(SUM(rows),0),COALESCE(SUM(cells),0) FROM report_events').fetchone()
        save(DEST / 'progress.json',dict(status=status, phase='report_update_date_inventory',pid=os.getpid(),
            updated_at=datetime.now().isoformat(),completed_batches=count,planned_batches=len(self.batches),
            completed_stock_report_pairs=rows,planned_stock_report_pairs=len(self.candidates),
            metadata_data_cells=cells,completed_weekly_market_rows=len(self.universe),
            active_requests=len(self.pending),latest_basic_quota=self.current_quota,
            financial_values_status='not_bulk_downloaded',historical_industry_status='pending',**extra))

    def validate_write(self, key, period, codes, result):
        values = {}
        for item in result.get('data',[]):
            code = item['thscode']
            raw = item['table']['ths_report_changedate_pit_stock']
            if code in values or len(raw)!=1:
                raise RuntimeError('Unexpected report-update response shape')
            value = raw[0]
            events = [] if value is None or str(value).strip() in ('','--') else str(value).split(',')
            dates = sorted({pd.Timestamp(d.strip()).strftime('%Y-%m-%d') for d in events})
            if any(d < period or d > '2026-09-24' for d in dates):
                raise RuntimeError(f'Event outside requested interval: {code}/{period}')
            values[code] = dates
        if set(values)!=set(codes):
            raise RuntimeError('Missing or extra securities in update-date response')
        frame = pd.DataFrame([dict(thscode=c,report_period=period,event_dates_json=json.dumps(values[c]),
                                  provider_event_count=len(values[c])) for c in codes])
        target = DEST / 'financial' / (key+'.parquet')
        tmp=target.with_suffix('.tmp');frame.to_parquet(tmp,index=False);tmp.replace(target)
        self.db.execute('INSERT OR REPLACE INTO report_events VALUES(?,?,?,?,?,?)',
            (key,len(frame),int(result.get('dataVol') or len(frame)),str(target.relative_to(DEST)),
             hashlib.sha256(target.read_bytes()).hexdigest(),datetime.now().isoformat()))
        self.db.commit()

    def summarize(self):
        entries = self.db.execute('SELECT path FROM report_events').fetchall()
        frames = [pd.read_parquet(DEST / p[0]) for p in entries]
        if not frames:
            return
        merged = pd.concat(frames,ignore_index=True)
        if merged.duplicated(['thscode','report_period']).any():
            raise RuntimeError('Duplicate report event keys')
        joined = self.candidates.merge(merged,on=['thscode','report_period'],how='left',validate='one_to_one')
        if joined.event_dates_json.isna().any():
            raise RuntimeError('Missing candidate coverage')
        versions=[]
        for row in joined.itertuples(index=False):
            dates=json.loads(row.event_dates_json)
            baseline=int(any(d<=row.first_needed_asof for d in dates))
            versions.append(baseline+sum(row.first_needed_asof<d<=row.last_needed_asof for d in dates))
        joined['candidate_versions_in_needed_window']=versions
        joined.to_parquet(DEST / 'plan/report_event_inventory.parquet',index=False)
        estimated_values=int(sum(versions))*20
        report=dict(status='inventory_complete',stock_report_pairs=len(joined),
            report_pairs_without_provider_events=int((joined.provider_event_count==0).sum()),
            candidate_version_count=int(sum(versions)),financial_fields=20,
            estimated_financial_value_cells=estimated_values,
            weekly_market_and_industry_cells=2*len(self.universe),
            update_date_metadata_cells=len(joined),
            estimated_total_before_additional_metadata=estimated_values+2*len(self.universe)+len(joined),
            budget_ceiling_cells=2500000,
            note='Update dates are candidate versions, not verified value changes. Additional metadata and sample costs are excluded.')
        save(DEST / 'audit/report_version_budget.json',report)
        return report

    def run(self, limit=None):
        done={r[0] for r in self.db.execute('SELECT batch FROM report_events')}
        groups=[(p,i,c) for p,i,c in self.batches if f'report_events_{p}_{i:02}' not in done]
        if limit:groups=groups[:limit]
        remaining=self.quota()
        if sum(len(c) for _,_,c in groups)+100000>remaining:
            raise RuntimeError('Insufficient quota for event inventory')
        cursor=completed=0
        self.progress('downloading_report_dates')
        while cursor<len(groups) or self.pending:
            stopped=(DEST/'STOP').exists()
            if stopped and not self.pending:
                self.progress('paused');return
            worker=read(CONTROL/'worker_status.json')
            if worker['status'] in ('blocked','crashed','stopped','login_failed','login_cancelled'):
                raise RuntimeError(f'Worker stopped: {worker}')
            if completed>=50 and not self.pending:
                self.quota();completed=0
            while cursor<len(groups) and len(self.pending)<12 and completed<50 and not stopped:
                period, index, codes=groups[cursor]
                key=f'report_events_{period}_{index:02}'
                self.submit(key,'THS_BD',[','.join(codes),'ths_report_changedate_pit_stock',
                    f'{period},2026-09-24,604,1,{period.replace("-","")}'],{'format':'format:list'})
                self.pending[key]=(period,codes,time.monotonic());cursor+=1
            for key,(period,codes,start) in list(self.pending.items()):
                result=self.consume(key)
                if result is None:
                    if time.monotonic()-start>300:raise RuntimeError(f'Timeout: {key}; checkpoint retained')
                    continue
                self.validate_write(key,period,codes,result);self.cleanup(key)
                del self.pending[key];completed+=1
                self.progress('downloading_report_dates',last_completed_period=period)
            time.sleep(.15)
        self.quota()
        report=None if limit else self.summarize()
        self.progress('report_dates_sample_complete' if limit else 'report_date_inventory_complete',version_budget=report)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int);args=parser.parse_args()
    download=ReportEvents()
    try:download.run(args.limit)
    except Exception:
        download.progress('blocked',error=traceback.format_exc());raise
