"""Compare real QQ end-labelled M1 and observed auction to native second bars.

The exclusive query endpoint is15:01 so that the real15:00 row is retained.
No absent source row is created, no turnover-rate field is treated as cash.
"""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p

def normalize_actual_rows(rows,date):
    selected={}
    for row in rows:
        if row[0][:8]!=date.replace('-',''):continue
        assert len(row)>=6 and len(row[0])==12
        minute=int(row[0][8:10])*60+int(row[0][10:12]);assert minute not in selected
        values={f:D(row[i])*10000 for f,i in [('open',1),('close',2),('high',3),('low',4)]}
        values['volume']=D(row[5])*100
        assert all(v.is_finite() and v==v.to_integral_value() for v in values.values())
        assert min(values[f] for f in ['open','high','low','close'])>0 and values['volume']>=0
        selected[minute]={f:int(v) for f,v in values.items()}
    grid=list(range(571,691))+list(range(781,901))
    assert all(m in selected for m in grid)
    assert set(selected)<=set(grid+[570])
    auction=selected.get(570)
    if auction and auction['volume']>0:
        first=selected[571]
        first.update(open=auction['open'],high=max(first['high'],auction['high']),low=min(first['low'],auction['low']),volume=first['volume']+auction['volume'])
    return {m-1:selected[m] for m in grid},auction

def main():
    out=p.OUT/'tencent_scoped_historical_m1_date_probe';output=out/'initial_actual_minute_comparison.json';assert not output.exists()
    date='2026-09-18';symbol='000099.SZ';qpath=out/'202609181501_actual_response.json'
    proof=p.json.loads((out/'exclusive_end_boundary_probe_receipt.json').read_text());assert p.sha(qpath)==proof['response_sha256']
    quotes,auction=normalize_actual_rows(p.json.loads(qpath.read_text())['data']['sz000099']['m1'],date)
    progresspath=p.OUT/'minute_observation_progress.json';progress=p.json.loads(progresspath.read_text());g=next(r for r in progress['groups'] if r['date']==date and r['prefix']==symbol[:2])
    leaf=next(r for r in g['minute_files'] if r['model']=='floor_left');path=p.Path(leaf['path']);assert p.sha(path)==leaf['sha256']
    frame=p.pq.ParquetFile(path).read(use_threads=False).to_pandas();frame=frame.loc[frame.ticker.eq(symbol[:6])].set_index('left_minute')
    vpath=p.Path(g['validation_path']);assert p.sha(vpath)==g['validation_sha256'];v=next(r for r in p.json.loads(vpath.read_text())['stockdays'] if r['symbol']==symbol)
    records=[]
    for slot,start in enumerate([570,630,780,840]):
        missing=[];diff=[];equal={f:0 for f in ['open','high','low','close','volume']};positive=0
        for minute in range(start,start+60):
            if minute not in frame.index:missing.append(minute);continue
            n=frame.loc[minute];q=quotes[minute];delta={f:int(n[f])-q[f] for f in equal}
            for f in equal:equal[f]+=delta[f]==0
            positive+=int(n.trades)>0 and int(n.volume)>0
            if any(delta.values()):diff.append(dict(minute=minute,differences=delta))
        records.append(dict(slot=slot,actual_native_positive_minutes=positive,native_missing_minutes=missing,field_exact_minutes=equal,all60_ohlcv_exact=not missing and not diff,differences=diff))
    receipt=dict(checked_utc=p.utc(),source_code_sha256=p.sha(__file__),source_reference_path=str(qpath),source_reference_sha256=p.sha(qpath),native_minute_path=str(path),native_minute_sha256=leaf['sha256'],native_verified_identity=p.identity(path),validation_path=str(vpath),validation_sha256=g['validation_sha256'],source_primary_daily_six_exact=v['clocks']['floor_left']['daily_six_fields_exact'],source_transaction_order_flag=v['unique_transaction_order_certified'],fixed_reference_timestamp_policy='QQm1 actual endlabels shifted minus1, observed positive0930auction merged into first actual0931bar; exclusive query end1501',observed_auction=auction,source_quantity_precision_certified=False,provider_minute_definition_certified=False,source_arrival_certified=False,candidate_hours_admitted=0,records=records)
    p.atomic_json(output,receipt);print(p.json.dumps([{k:v for k,v in r.items() if k!='differences'} for r in records]));print('daily/order',receipt['source_primary_daily_six_exact'],receipt['source_transaction_order_flag'])

if __name__=='__main__':main()
