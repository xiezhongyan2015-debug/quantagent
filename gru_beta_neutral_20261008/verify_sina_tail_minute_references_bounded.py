"""Independent columnwise minute-reference audit; no new raw admission."""
from decimal import Decimal as D
from collections import Counter
from . import bounded_tail_pipeline as p

DEST=p.OUT/'sina_tail_minute_expanded'


def main():
    lock=p.locked('verify_sina_tail_expanded')
    receipt_path=DEST/'receipt.json';receipt=p.json.loads(receipt_path.read_text())
    assert receipt['status']=='expanded_references_complete_no_admission'
    assert receipt['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/compare_sina_tail_minute_references_bounded.py')
    pilot_path=p.OUT/'sina_tail_minute_pilot/receipt.json';assert p.sha(pilot_path)==receipt['initial_pilot_receipt_sha256']
    pilot=p.json.loads(pilot_path.read_text());assert pilot['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/probe_sina_tail_minute_concordance_bounded.py')
    assert not receipt['candidate_hours_admitted'] and not receipt['minute_missing_values_filled']
    totals=Counter();verified=[];qualified=[]
    for record in receipt['records']:
        if record['status']!='compared_without_admission':continue
        source=p.Path(record['path']);assert p.identity(source)==record['verified_identity']
        raw=source.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==record['sha256']
        text=raw.decode('utf8');data,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);assert len(data)==record['rows']
        independent={}
        for row in data:
            stamp=p.datetime.strptime(row['day'],'%Y-%m-%d %H:%M:%S');assert stamp.second==0
            key=stamp.strftime('%Y-%m-%d'),stamp.hour*60+stamp.minute-1
            assert key not in independent
            values=[D(row[f])*10000 for f in ['open','high','low','close']]
            assert all(v==v.to_integral_value() and v>0 for v in values)
            assert values[1]>=max(values[0],values[3])>=min(values[0],values[3])>=values[2]
            qty=D(row['volume']);cash=D(row['amount']);assert qty>=0 and cash>=0
            independent[key]=dict(zip(['open','high','low','close'],map(int,values)),volume=qty,amount=cash)
        count=0
        for comparison in record['comparisons']:
            date=comparison['date'];native_path=p.Path(comparison['native_source_path']);before=p.identity(native_path)
            raw=native_path.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==comparison['native_source_sha256']
            frame=p.pq.ParquetFile(p.pa.BufferReader(raw)).read(columns=['ticker','left_minute','open','high','low','close','volume','amount_x10000']).to_pandas()
            frame=frame.loc[frame.ticker.eq(record['symbol'][:6])];assert len(frame)==comparison['native_observed_minutes'] and not frame.left_minute.duplicated().any()
            matched={};exact_counts=Counter();all5=all6=0
            for row in frame.itertuples(index=False):
                q=independent.get((date,int(row.left_minute)))
                if q is None:continue
                exact={f:D(int(getattr(row,f)))==D(q[f]) for f in ['open','high','low','close','volume']}
                cash=D(int(row.amount_x10000))/10000==q['amount']
                for f,value in exact.items():exact_counts[f]+=value
                complete=all(exact.values());all5+=complete;all6+=complete and cash
                matched[int(row.left_minute)]=complete;count+=1
            assert len(matched)==comparison['paired_real_minutes'] and all5==comparison['minute_all5_exact'] and all6==comparison['minute_all6_exact']
            assert dict(exact_counts)==comparison['field_exact_counts']
            grid=list(range(570,690))+list(range(780,900))
            for hour in comparison['hours']:
                expected=grid[hour['slot']*60:(hour['slot']+1)*60]
                paired=sum(m in matched for m in expected)
                complete=paired==60 and all(matched[m] for m in expected)
                assert hour['paired_real_minutes']==paired and hour['all60_price_and_volume_exact']==complete
                totals['all60_price_volume_exact_hours']+=complete
                if complete:qualified.append(dict(symbol=record['symbol'],date=date,slot=hour['slot'],sina_reference_path=str(source),sina_reference_sha256=record['sha256'],native_minute_path=str(native_path),native_minute_sha256=comparison['native_source_sha256']))
            assert p.identity(native_path)==before
            del raw,frame
        assert p.identity(source)==record['verified_identity']
        verified.append(dict(symbol=record['symbol'],paired_real_minutes_checked=count))
    assert dict(totals)==receipt['counts']
    proof=dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(receipt_path),stocks_verified=len(verified),paired_real_minutes_checked=sum(r['paired_real_minutes_checked'] for r in verified),all60_price_volume_exact_hours=totals['all60_price_volume_exact_hours'],cash_disagreements_preserved=True,reported_end_label_to_left_interval_rule_predeclared=True,missing_minutes_imputed=False,raw_candidate_hours_admitted=0,source_arrival_time_or_live_clock_certified=False,production_ready=False,qualified_minute_diagnostic_hours=qualified,stocks=verified,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(DEST/'independent_verification.json',proof);lock.close()


if __name__=='__main__':main()
