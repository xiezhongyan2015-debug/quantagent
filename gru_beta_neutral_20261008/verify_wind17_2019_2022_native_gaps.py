"""Independent retained-range re-extraction and exact native 2019-22 diagnostics."""
import csv
import hashlib
import io
import json
import sys
import zipfile
import zlib
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
import pyarrow
import numpy as np

BASE=ROOT/'outputs/gru_beta_neutral_20261008'
OUT=BASE/'public_source_probes/additional_public_archives_20261009/wind17/remaining_2019_2022'
GRID=[*range(571,691),*range(781,901)]
PRICE={'开盘价':'open','最高价':'high','最低价':'low','收盘价':'close'}


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


class RetainedRanges(io.RawIOBase):
    def __init__(self,item):
        self.position=0;self.size=item['source_file']['size'];self.fragments=[]
        for r in item['range_receipts']:
            if 'sha256' not in r:continue
            assert r['http_status']==206 and sha(r['path'])==r['sha256']
            assert r['content_range']=='bytes %d-%d/%d'%(r['range_start'],r['range_end'],self.size)
            self.fragments.append((r['range_start'],r['range_end'],Path(r['path']).read_bytes()))
    def seekable(self):return True
    def readable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        p=offset if whence==0 else self.position+offset if whence==1 else self.size+offset
        assert 0<=p<=self.size;self.position=p;return p
    def read(self,size=-1):
        n=self.size-self.position if size<0 else min(size,self.size-self.position)
        if not n:return b''
        for start,end,raw in self.fragments:
            if start<=self.position and self.position+n-1<=end:
                offset=self.position-start;self.position+=n;return raw[offset:offset+n]
        raise ValueError('Unretained archive range required')


def main():
    ap=OUT/'acquisition_and_daily_diagnostics_receipt.json'
    output=OUT/'independent_native_gap_verification.json';assert not output.exists()
    a=json.loads(ap.read_text());assert a.get('completed_utc')
    assert sha(ROOT/'gru_beta_neutral_20261008/fetch_wind17_2019_2022_gaps.py')==a['source_code_sha256']
    assert sha(a['metadata_path'])==a['metadata_sha256'] and sha(a['gap_path'])==a['gap_sha256']
    assert sha(a['actual_target_csv_path'])==a['actual_target_csv_sha256']
    assert sha(a['official_identity_receipt_path'])==a['official_identity_receipt_sha256']
    identity=json.loads(Path(a['official_identity_receipt_path']).read_text())
    aliases={r['symbol']:r for r in identity['records'] if r['symbol'] in identity['research_bridge_symbols']}
    with Path(a['actual_target_csv_path']).open(newline='') as stream:
        targets={(r['symbol'],r['date']):int(r['missing_hours']) for r in csv.DictReader(stream)}
    assert len(targets)==208 and sum(targets.values())==829 and len(a['results'])==94
    dataset=BASE/'backfilled_panel_candidate_canonical_ipo_v6/dataset'
    meta=json.loads((dataset/'meta.json').read_text())
    assert sha(dataset/'hour_complete.npy')==meta['files_sha256']['hour_complete.npy']
    complete=np.load(dataset/'hour_complete.npy',mmap_mode='r')
    calendar_path=ROOT/'gru_research/artifacts/minute_calendar.json'
    calendar=json.loads(calendar_path.read_text())
    seen=set();records=[];states=Counter();failed=Counter()
    whole_day_pass_hours=selected_daily_hours=native_pass_hours=verified_days=observed_days=0
    for item in a['results']:
        symbol,year=item['canonical_symbol'],item['year'];states[item['status']]+=1
        wanted={d:v for (s,d),v in targets.items() if s==symbol and d.startswith(year)}
        assert len(wanted)==item['target_stockdays'] and sum(wanted.values())==item['target_missing_hours']
        column=calendar['union_symbols'].index(symbol)
        if item.get('officially_verified_code_alias_used'):
            alias=aliases[symbol]
            assert sha(alias['official_statement_path'])==alias['official_statement_sha256']
            assert item['observed_source_symbol']==alias['historical_code'].replace('.','')
        if item['status']!='exact_CSV_acquired_and_daily_diagnostics_complete':
            for date,count in wanted.items():
                index=calendar['dates'].index(date)*4;slots=np.flatnonzero(~complete[index:index+4,column]).tolist()
                assert len(slots)==count
                seen.add((symbol,date));records.append({'symbol':symbol,'date':date,'actual_missing_slots':slots,'status':item['status'],'native_pass_missing_slots':[],'coarse_reference_checked':False})
            continue
        raw=Path(item['raw_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==item['raw_sha256'] and zlib.crc32(raw)&0xffffffff==item['CRC32']
        with zipfile.ZipFile(RetainedRanges(item)) as archive:assert archive.read(item['zip_member'])==raw
        rows=list(csv.DictReader(io.StringIO(raw.decode('gbk'))))
        assert len(rows)==item['source_rows'] and all(r['股票代码']==item['observed_source_symbol'] for r in rows)
        stamps=[r['k线结束时间'] for r in rows]
        assert len(set(stamps))==len(stamps) and min(stamps)==item['first_source_timestamp'] and max(stamps)==item['last_source_timestamp']
        assert sha(item['primary_daily_path'])==item['primary_daily_sha256']
        primary=json.loads(Path(item['primary_daily_path']).read_text())
        daily={r[0]:dict(zip(primary['fields'],r)) for r in primary['rows']}
        checks={r['date']:r for r in item['target_stockday_checks']};assert set(checks)==set(wanted)
        for date,count in sorted(wanted.items()):
            seen.add((symbol,date));index=calendar['dates'].index(date)*4
            slots=np.flatnonzero(~complete[index:index+4,column]).tolist();assert len(slots)==count
            selected=sorted((r for r in rows if r['k线结束时间'].startswith(date)),key=lambda r:r['k线结束时间'])
            expected=checks[date]
            if not selected:
                assert expected['status']=='actual_target_date_absent'
                records.append({'symbol':symbol,'date':date,'actual_missing_slots':slots,'status':'actual_target_date_absent','native_pass_missing_slots':[],'coarse_reference_checked':False});continue
            observed_days+=1;regular=[];auction=[]
            for row in selected:
                t=datetime.fromisoformat(row['k线结束时间']);assert t.year==int(year) and t.second==0
                clock=t.hour*60+t.minute
                r={v:Decimal(row[k]) for k,v in PRICE.items()}
                r.update(clock=clock,volume=Decimal(row['成交量'])*100,amount=Decimal(row['成交额']))
                if clock in GRID:regular.append(r)
                elif clock==570 and r['volume']>0:auction.append(r)
                elif clock==780:assert r['volume']==0
            assert [r['clock'] for r in regular]==GRID and len(auction)<=1
            if auction:
                r,b=regular[0],auction[0]
                r['open'],r['high'],r['low']=b['open'],max(r['high'],b['high']),min(r['low'],b['low'])
                r['volume']+=b['volume'];r['amount']+=b['amount']
            p=daily[date];vd=sum(r['volume'] for r in regular)-Decimal(p['volume'])
            positive=[sum(r['volume'] for r in regular[s*60:(s+1)*60])>0 for s in range(4)]
            flags={'source_rows_valid':all(all(r[k].is_finite() and r[k]>0 and r[k]==r[k].quantize(Decimal('.01')) for k in PRICE.values()) and r['low']<=min(r['open'],r['close'])<=max(r['open'],r['close'])<=r['high'] and r['volume'].is_finite() and r['volume']>=0 for r in regular),
                   'grid_valid':True,'primary_day_trading':p['tradestatus']=='1' and Decimal(p['volume'])>0,
                   'open_agrees':regular[0]['open']==Decimal(p['open']),'close_agrees':regular[-1]['close']==Decimal(p['close']),
                   'volume_difference_within_100_shares':abs(vd)<=100,
                   'quotes_inside_primary_range':max(r['high'] for r in regular)<=Decimal(p['high']) and min(r['low'] for r in regular)>=Decimal(p['low']),
                   'all_four_hours_positive_volume':all(positive)}
            assert all(expected[k]==v for k,v in flags.items()),(symbol,date,flags,expected)
            assert float(vd)==expected['volume_difference_shares'] and bool(auction)==expected['positive_0930_source_bar_merged']
            money=[r['low']*r['volume']-1<=r['amount']<=r['high']*r['volume']+1 for r in regular]
            assert money.count(False)==expected['minute_amount_vs_reported_price_times_lot_volume_conflicts']
            assert all(flags.values())==expected['existing_daily_and_hour_gates_passed']
            if all(flags.values()):whole_day_pass_hours+=count
            passes=[s for s in slots if positive[s]] if all(v for k,v in flags.items() if k!='all_four_hours_positive_volume') else []
            selected_daily_hours+=len(passes)
            native=[s for s in passes if all(money[s*60:(s+1)*60])]
            native_pass_hours+=len(native);verified_days+=1
            for k,v in flags.items():
                if not v:failed[k]+=1
            records.append({'symbol':symbol,'date':date,'actual_missing_slots':slots,'status':'raw_native_daily_and_selected_hours_independently_verified',
                            'daily_flags':flags,'selected_positive_daily_pass_slots':passes,'native_pass_missing_slots':native,
                            'reported_volume_difference_shares':str(vd),'coarse_reference_checked':False})
    assert seen==set(targets) and whole_day_pass_hours==a['daily_gate_pass_hours']
    result={'checked_utc':datetime.now(timezone.utc).isoformat(),'passed':True,'source_code_sha256':sha(__file__),
            'acquisition_receipt_path':str(ap),'acquisition_receipt_sha256':sha(ap),'target_stockyears':94,'target_stockdays':208,'actual_missing_hours':829,
            'stockyear_acquisition_status_counts':dict(states),'observed_target_stockdays':observed_days,'native_stockdays_independently_verified':verified_days,
            'producer_whole_day_gate_pass_hours':whole_day_pass_hours,'daily_and_selected_positive_hour_pass_hours':selected_daily_hours,
            'native_amount_pass_hours':native_pass_hours,'failed_daily_gate_stockday_counts':dict(failed),'records':records,
            'candidate_meta_sha256':sha(dataset/'meta.json'),'candidate_fingerprint':meta['data_fingerprint'],
            'calendar_sha256':sha(calendar_path),'coarse_reference_rows_acquired':0,'coarse_reference_qualified_hour_count':None,
            'coarse_reference_null_means_not_tested':True,'whole_archive_LFS_SHA256_verified':False,'minute_quantity_precision_certified':False,
            'candidate_changed':False,'candidate_hours_admitted':0,'production_ready':False,
            'method':'Fresh retained-range hashes, original source-member re-decompression and CRC; independent csv.DictReader, exact Decimal minute/day checks and actual V6 missing slots. No producer or shared normalization helper imported. No network query executed by verifier.'}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
