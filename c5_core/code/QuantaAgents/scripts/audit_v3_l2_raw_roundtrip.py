"""Bounded raw-to-existing-Parquet identity audit; never run the vendor converter.

The two stocks/date were already selected by the preserved converter test.
This does not certify the vendor's economic units, receipt time, or other dates.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import io
import json
import time

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / 'experiment_traces/meta_framework_v3/l2_raw_roundtrip_001'
CODES = ['000001.SZ', '600000.SH']
DAY = '2026-02-10'
COMMON = ['code','wind_code','exchange_code','date','time_raw','event_ts','source_row_no']
NAMES = ['last_price_x1e4','trade_volume','trade_amount','trade_count','iopv_x1e4','trade_flag','bs_flag',
    'cum_volume','cum_amount','high_price_x1e4','low_price_x1e4','open_price_x1e4','prev_close_x1e4',
    *[f'ask_price_{i}_x1e4' for i in range(1,11)],*[f'ask_volume_{i}' for i in range(1,11)],
    *[f'bid_price_{i}_x1e4' for i in range(1,11)],*[f'bid_volume_{i}' for i in range(1,11)],
    'weighted_avg_ask_price_x1e4','weighted_avg_bid_price_x1e4','total_ask_volume','total_bid_volume',
    'unweighted_index_raw','instrument_count','up_count','down_count','flat_count']
HEADER = ['万得代码','交易所代码','自然日','时间','成交价','成交量','成交额','成交笔数','IOPV','成交标志','BS标志',
    '当日累计成交量','当日成交额','最高价','最低价','开盘价','前收盘',
    *[f'申卖价{i}' for i in range(1,11)],*[f'申卖量{i}' for i in range(1,11)],
    *[f'申买价{i}' for i in range(1,11)],*[f'申买量{i}' for i in range(1,11)],
    '加权平均叫卖价','加权平均叫买价','叫卖总量','叫买总量','不加权指数','品种总数','上涨品种数','下跌品种数','持平品种数']


def sha(body): return hashlib.sha256(body).hexdigest()
def read(path): return json.loads(path.read_text(encoding='utf-8'))
def save(path, value):
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,default=str,allow_nan=False)
        f.write('\n')


def identity(path):
    s=path.stat()
    return {'path':str(path),'bytes':s.st_size,'mtime_ns':s.st_mtime_ns}


def stable_body(item):
    p=Path(item['path']); assert identity(p)=={k:item[k] for k in ['path','bytes','mtime_ns']}
    body=p.read_bytes()
    assert identity(p)=={k:item[k] for k in ['path','bytes','mtime_ns']} and len(body)==item['bytes']
    return body


def freeze():
    assert not OUT.exists(), 'Scope already exists; never reselect or resume implicitly'
    quote_plan=read(REPO/'experiment_traces/meta_framework_v3/l2_execution_evidence_001/plan.json')
    market=Path(quote_plan['manifests'][0]['path']).parent.parent
    samples=market.parent/'l2_parquet_pipeline/sample_probe/20260210'
    raw=[{**identity(samples/code/'行情.csv'),'code':code} for code in CODES]
    assert sum(x['bytes'] for x in raw)<=8*1024**2
    OUT.mkdir(); (OUT/'raw').mkdir(); (OUT/'parts').mkdir()
    plan={'created_at':datetime.now(timezone.utc).isoformat(),'deadline_epoch':time.time()+3600,
        'selection':'Existing converter test_real_samples_round_trip fixed 000001.SZ and 600000.SH on 20260210; no result selection',
        'codes':CODES,'date':DAY,'raw':raw,'market_root':str(market),
        'manifest':identity(market/'manifests/20260210.json'),
        'source_test_sha256':sha((REPO/'experiment_traces/meta_framework_v3/l2_provenance_evidence_001/sources/02_test_convert_l2_to_parquet.py').read_bytes()),
        'script_sha256':sha(Path(__file__).read_bytes()),'max_parts':2,'max_selected_rows':12000,
        'max_raw_bytes':8*1024**2,'max_full_source_hash_bytes':256*1024**2,
        'max_projected_column_payload_bytes':64*1024**2,'max_code_scan_payload_bytes':1024**2,
        'max_raw_rowgroup_rows':1000000,'columns':COMMON+NAMES,'header':HEADER,
        'network_calls':0,'model_calls':0,'pnl_calculation':False,'original_converter_execution':False,
        'sealed_2024_2025_opened':False,'execution_valid':False,
        'stops':['one-hour deadline','any source mutation','resource cap','any field discrepancy retained; no source repair or automatic rerun']}
    plan['plan_sha256']=sha(json.dumps(plan,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
    save(OUT/'plan.json',plan)
    return {'plan_sha256':plan['plan_sha256'],'raw_bytes':sum(x['bytes'] for x in raw)}


def chunk_bytes(pf,group,columns):
    g=pf.metadata.row_group(group)
    return sum(g.column(pf.schema_arrow.get_field_index(c)).total_compressed_size for c in columns)


def expected_rows(body, code):
    reader=csv.reader(io.StringIO(body.decode('gb18030'),newline=''))
    header=next(reader)
    if header[-1]=='': header.pop()
    assert header==HEADER, 'Actual raw CSV header differs from preserved converter contract'
    result=[]
    for number,raw in enumerate(reader,1):
        if len(raw)==67 and raw[-1]=='': raw.pop()
        assert len(raw)==66
        raw=[x.replace('\x00','').strip() or None for x in raw]
        assert raw[0]==code and raw[2]=='20260210'
        event=datetime.strptime(raw[2]+raw[3].zfill(9),'%Y%m%d%H%M%S%f')
        row=dict(zip(COMMON,[code[-2:].lower()+code[:6],code,raw[1],event.date(),int(raw[3]),event,number]))
        for index,name in enumerate(NAMES,4):
            value=raw[index]
            if index not in (9,10) and value is not None:
                value=int(value)
                if index in (5,6) and value>2**63-1: value-=2**64
            row[name]=value
        result.append(row)
    return result


def audit():
    plan=read(OUT/'plan.json')
    assert time.time()<plan['deadline_epoch'] and sha(Path(__file__).read_bytes())==plan['script_sha256']
    assert not (OUT/'intent.json').exists(), 'Saved intent exists; inspect receipts, do not automatically reread'
    save(OUT/'intent.json',{'started_at':time.time(),'plan_sha256':plan['plan_sha256']})
    manifest_body=stable_body(plan['manifest']); m=json.loads(manifest_body)
    (OUT/'manifest.json').write_bytes(manifest_body)
    assert m['status']=='complete' and m['trade_date']==DAY
    declared={x['relative_path'].replace('\\','/'):x for x in m['output_files']}
    parts=[]
    for code in CODES:
        found=[x for x in m['table_parts']['quotes'] if x['first_code']<=code<=x['last_code']]
        assert len(found)==1
        path=Path(plan['market_root'])/'quotes'/('trade_date='+DAY)/found[0]['file']
        pf=pq.ParquetFile(path); assert pf.schema_arrow.names==plan['columns']
        candidates=[]
        for group in range(pf.metadata.num_row_groups):
            g=pf.metadata.row_group(group); assert g.num_rows<=plan['max_raw_rowgroup_rows']
            stats=g.column(pf.schema_arrow.get_field_index('wind_code')).statistics
            if not stats or not stats.has_min_max or stats.min<=code<=stats.max: candidates.append(group)
        expected=declared['quotes/'+path.name]
        item={**identity(path),'code':code,'expected_sha256':expected['sha256'],'candidate_groups':candidates,
            'code_scan_bytes':sum(chunk_bytes(pf,g,['wind_code']) for g in candidates),
            'projection_upper_bytes':sum(chunk_bytes(pf,g,plan['columns']) for g in candidates)}
        assert item['bytes']==expected['bytes']
        parts.append(item)
    assert len({x['path'] for x in parts})==len(parts)<=plan['max_parts']
    assert sum(x['bytes'] for x in parts)<=plan['max_full_source_hash_bytes']
    assert sum(x['code_scan_bytes'] for x in parts)<=plan['max_code_scan_payload_bytes']
    assert sum(x['projection_upper_bytes'] for x in parts)<=plan['max_projected_column_payload_bytes']
    save(OUT/'admitted_parts.json',{'plan_sha256':plan['plan_sha256'],'manifest_sha256':sha(manifest_body),'parts':parts})
    receipts=[]; all_diffs=[]; total=0
    for index,(item,raw_item) in enumerate(zip(parts,plan['raw'])):
        assert time.time()<plan['deadline_epoch']
        code=item['code']; raw_body=stable_body(raw_item)
        (OUT/'raw'/f'{code}.csv').write_bytes(raw_body)
        expected=expected_rows(raw_body,code)
        assert len(expected)==m['validation']['quotes']['source_rows_by_wind_code'][code]
        total+=len(expected); assert total<=plan['max_selected_rows']
        # Full file content identity is checked before any projection is used.
        digest=hashlib.sha256()
        with Path(item['path']).open('rb') as f:
            while block:=f.read(8*1024**2): digest.update(block)
        assert digest.hexdigest()==item['expected_sha256']
        pf=pq.ParquetFile(item['path']); matched=[]; tables=[]
        for group in item['candidate_groups']:
            ids=pf.read_row_group(group,columns=['wind_code'])
            mask=pc.equal(ids['wind_code'],code)
            if pc.any(mask).as_py():
                matched.append(group)
                tables.append(pf.read_row_group(group,columns=plan['columns']).filter(mask))
        table=pa.concat_tables(tables); table=table.take(pc.sort_indices(table,sort_keys=[('source_row_no','ascending')]))
        assert identity(Path(item['path']))=={k:item[k] for k in ['path','bytes','mtime_ns']}
        observed=table.to_pylist(); assert len(observed)==len(expected)
        differences=[]
        for row_number,(left,right) in enumerate(zip(expected,observed),1):
            for column in plan['columns']:
                if left[column]!=right[column]:
                    differences.append({'row':row_number,'column':column,'raw_normalized':left[column],'parquet':right[column]})
        target=OUT/'parts'/f'{code}.parquet'; pq.write_table(table,target,compression='zstd')
        receipt={'code':code,'rows':len(expected),'columns':len(plan['columns']),'cells_compared':len(expected)*len(plan['columns']),
            'raw_sha256':sha(raw_body),'full_source_sha256':digest.hexdigest(),'saved_parquet_sha256':sha(target.read_bytes()),
            'matched_groups':matched,'code_scan_payload_bytes':item['code_scan_bytes'],
            'projected_payload_bytes':sum(chunk_bytes(pf,g,plan['columns']) for g in matched),
            'difference_count':len(differences),'differences':differences[:100],'finished_at':time.time()}
        save(OUT/f'receipt_{code}.json',receipt); receipts.append(receipt); all_diffs.extend(differences)
        if differences: break
    value={'completed_at':datetime.now(timezone.utc).isoformat(),'plan_sha256':plan['plan_sha256'],
        'all_cells_identical':not all_diffs and len(receipts)==2,'rows':sum(x['rows'] for x in receipts),
        'cells_compared':sum(x['cells_compared'] for x in receipts),'receipts':receipts,
        'source_hash_bytes':sum(x['bytes'] for x in parts[:len(receipts)]),
        'new_model_calls':0,'execution_valid':False,'formal_target_success':False,
        'limitations':['Only two existing raw samples on 20260210; not proof for January files',
            'Numeric preservation is not official certification of economic units',
            'No original converter version pin or historical receiver timestamps',
            'No account fees, corporate actions, fills, portfolio returns, or OOS success certified']}
    save(OUT/'result.json',value)
    assert value['all_cells_identical'], 'Discrepancies retained; stop without source repair'
    return {k:v for k,v in value.items() if k!='receipts'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('action',choices=['freeze','audit'])
    args=parser.parse_args()
    print(json.dumps(freeze() if args.action=='freeze' else audit(),ensure_ascii=True))
