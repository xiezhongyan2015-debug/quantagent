"""Verify retained actual M1 responses and hourly pilot; no donor admission."""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p

def independent_minutes(rows,date):
    bars={}
    for row in rows:
        if row[0][:8]!=date.replace('-',''):continue
        m=int(row[0][8:10])*60+int(row[0][10:12]);assert m not in bars
        b={f:D(row[i])*10000 for f,i in [('open',1),('close',2),('high',3),('low',4)]};b['volume']=D(row[5])*100
        assert all(v.is_finite() and v==int(v) for v in b.values());bars[m]={k:int(v) for k,v in b.items()}
    expected=list(range(571,691))+list(range(781,901))
    assert set(bars)==set(expected+[570])
    auction=bars[570]
    if auction['volume']>0:
        first=bars[571];first['open']=auction['open'];first['high']=max(first['high'],auction['high']);first['low']=min(first['low'],auction['low']);first['volume']+=auction['volume']
    return {m-1:bars[m] for m in expected}

def main():
    dest=p.OUT/'tencent_scoped_extended_history_m1_probe';output=dest/'independent_combined_probe_verification.json';assert not output.exists()
    receipts=[p.OUT/'tencent_scoped_historical_m1_date_probe/receipt.json',p.OUT/'tencent_scoped_historical_m1_date_probe/exclusive_end_boundary_probe_receipt.json',dest/'receipt.json',dest/'coverage_dates_receipt.json',p.OUT/'tencent_actual_m1_aggregated_hour_pilot/receipt.json']
    source=[];complete={};empty=[];pins=[]
    for rp in receipts:
        receipt=p.json.loads(rp.read_text());pins.append(dict(path=str(rp),sha256=p.sha(rp)))
        rows=receipt.get('results',receipt.get('records'))
        if rows is None:
            rows=[dict(receipt,path=receipt['response_path'],sha256=receipt['response_sha256'],symbol='000099.SZ',date='2026-09-18')]
        for r in rows:
            leaf=p.Path(r.get('path',r.get('source_reference_path')));digest=r.get('sha256',r.get('source_reference_sha256'));assert p.sha(leaf)==digest
            data=p.json.loads(leaf.read_text());symbol=r.get('symbol',receipt.get('symbol'));date=r.get('date',receipt.get('requested_date'));assert symbol and date
            assert data['code']==0;code=symbol[-2:].lower()+symbol[:6];actual=data.get('data',{}).get(code,{}).get('m1',[])
            assert len(actual)==r.get('source_rows',r.get('actual_source_rows')) and len(actual)<=320
            dayrows=[x for x in actual if x[0][:8]==date.replace('-','')]
            source.append(dict(symbol=symbol,date=date,response_path=str(leaf),response_sha256=digest,actual_rows=len(actual),actual_requested_day_rows=len(dayrows)))
            if len(dayrows)==241:complete[symbol,date]=independent_minutes(actual,date)
            elif not actual:empty.append(dict(symbol=symbol,date=date,response_path=str(leaf)))
    pilot=p.json.loads(receipts[-1].read_text());passed=0;daily=0
    for r in pilot['results']:
        q=complete[r['symbol'],r['date']];block=[q[m] for m in range([570,630,780,840][r['slot']],[630,690,840,900][r['slot']])]
        hour=dict(open=block[0]['open'],close=block[-1]['close'],high=max(x['high'] for x in block),low=min(x['low'] for x in block),volume=sum(x['volume'] for x in block));assert hour==r['reference_hour']
        diffs={k:hour[k]-r['native_hour'][k] for k in hour};assert diffs==r['hour_differences'] and (not any(diffs.values()))==r['all_five_hour_fields_exact'];passed+=r['all_five_hour_fields_exact']
        day=dict(open=q[570]['open'],close=q[899]['close'],high=max(x['high'] for x in q.values()),low=min(x['low'] for x in q.values()),volume=sum(x['volume'] for x in q.values()));assert day==r['reference_day']
        pp=p.Path(r['primary_daily_path']);assert p.sha(pp)==r['primary_daily_sha256'];raw=p.json.loads(pp.read_text());pr=next(dict(zip(raw['fields'],x)) for x in raw['rows'] if x[0]==r['date']);delta={k:day[k]-int(D(pr[k])*10000) for k in ['open','high','low','close']};delta['volume']=day['volume']-int(pr['volume']);assert delta==r['primary_day_differences'];daily+=not any(delta.values())
    assert passed==0 and len(source)==18 and len(complete)==10
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_pins=pins,actual_public_requests_verified=len(source),complete_observed_241row_stockdays=len(complete),actual_observed_240_minute_bars=len(complete)*240,source_response_pins=source,empty_responses=empty,actual_four_hour_pilot_exact_hours=passed,pilot_primary_daily_five_exact_stockdays=daily,provider_sampling_and_volume_boundary_conflicts_retained=True,source_quantity_multiplier100_certification_remains_unproven=True,turnover_rate_not_used_as_cash=True,source_native_price_quantity_not_changed=True,missing_source_records_not_created=True,empty_responses_not_called_halts=True,candidate_hours_admitted=0,network_requests=0,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    print(p.json.dumps(dict(requests=len(source),complete_days=len(complete),actual_minutes=len(complete)*240,pilot_exact_hours=passed,pilot_daily_exact_days=daily)),flush=True)

if __name__=='__main__':main()
