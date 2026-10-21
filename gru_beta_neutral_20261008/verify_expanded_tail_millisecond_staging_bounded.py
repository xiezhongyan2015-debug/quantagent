"""Independently audit every newly qualified or rejected minute-exact hour."""
from collections import Counter,defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_expanded_tail_millisecond_minutes_bounded import DEST,RAW
import duckdb


def main():
    lock=p.locked('verify_expanded_tail_ms_staging');path=DEST/'receipt.json';stage=p.json.loads(path.read_text());output=DEST/'independent_verification.json';assert not output.exists()
    assert stage['status']=='staged_actual_millisecond_hours_pending_independent_audit' and not stage['raw_panel_modified']
    assert stage['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_expanded_tail_millisecond_minutes_bounded.py')
    assert stage['source_stock_bars_helper_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_reconciled_tail_millisecond_minutes_bounded.py')
    reference_path=p.Path(stage['reference_proof_path']);reference=p.json.loads(reference_path.read_text());assert reference['passed'] and p.sha(reference_path)==stage['reference_proof_sha256']
    assert reference['audited_receipt_sha256']==p.sha(reference_path.parent/'receipt.json') and reference['all_requested_queries_complete']
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and p.sha(audit_path)==stage['parent_raw_audit_sha256'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    identity=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy')==identity
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    requested=defaultdict(lambda:defaultdict(list));counts=Counter()
    for h in reference['qualified_minute_diagnostic_hours']:
        row=cal['dates'].index(h['date'])*4+h['slot'];col=cal['union_symbols'].index(h['symbol'])
        if mask[row,col]:counts['already_admitted_raw_hours']+=1;continue
        requested[h['date']][h['symbol'][:6]].append(h)
    accepted={(r['date'],r['symbol'],h['slot']):h for r in stage['records'] for h in r['hours']};rejected={(h['date'],h['symbol'],h['slot']):h for h in stage['rejected_hours']}
    expected={(h['date'],h['symbol'],h['slot']) for tickers in requested.values() for hs in tickers.values() for h in hs}
    assert set(accepted).isdisjoint(rejected) and set(accepted)|set(rejected)==expected
    coarse_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';coarse=p.json.loads(coarse_path.read_text());assert p.sha(coarse_path)==stage['coarse_comparison_receipt_sha256']
    coarse_proof_path=coarse_path.parent/'independent_hour_comparison_verification.json';coarse_proof=p.json.loads(coarse_proof_path.read_text());assert coarse_proof['passed'] and p.sha(coarse_proof_path)==stage['coarse_independent_proof_sha256']
    assert coarse_proof['audited_receipt_sha256']==p.sha(coarse_path)
    acquisition_path=coarse_path.parent/'all_target_stock_acquisition.json';acquisition=p.json.loads(acquisition_path.read_text());assert p.sha(acquisition_path)==coarse['source_acquisition_sha256']
    refs={};symbols={h[1] for h in expected}
    for r in acquisition['records']:
        if r['symbol'] not in symbols:continue
        qpath=p.Path(r['path']);assert p.identity(qpath)==r['verified_identity'];data=qpath.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['sha256']
        for q in p.json.loads(data)['data'][r['code']]['m60']:
            t=q[0];date=f'{t[:4]}-{t[4:6]}-{t[6:8]}';slot=['1030','1130','1400','1500'].index(t[-4:]);key=date,r['symbol'],slot
            if key not in expected:continue
            refs[key]={f:int(D(q[i])*10000) for f,i in [('open',1),('close',2),('high',3),('low',4)]};refs[key]['volume']=int(D(q[5])*100)
        assert p.identity(qpath)==r['verified_identity']
    assert set(refs)==expected
    verified=[];verified_rejections=[];spill=DEST/'independent_sql_spill';spill.mkdir(exist_ok=True);con=duckdb.connect()
    con.execute("SET memory_limit='192MB'");con.execute('SET threads=1');con.execute('SET temp_directory=?',[str(spill)]);con.execute("SET max_temp_directory_size='2GB'")
    staged={(r['date'],r['symbol'][:6]):r for r in stage['records']}
    for pin in stage['native_source_pins']:
        date=pin['date'];pair_path=p.Path(pin['pairing_path']);order_path=p.Path(pin['order_path']);assert p.sha(pair_path)==pin['pairing_sha256'] and p.sha(order_path)==pin['order_sha256']
        pair=p.json.loads(pair_path.read_text());order=p.json.loads(order_path.read_text());assert pair['all_rows_price_quantity_and_floor_clock_exact'] and pair['stockdays_checked']==624 and order['paired_source_receipt_sha256']==p.sha(pair_path)
        assert order['intrasecond_event_inversions']==order['minute_open_changed_by_intrasecond_order']==order['minute_close_changed_by_intrasecond_order']==0
        artifact=p.Path(pair['native_field_artifact']['path']);assert p.identity(artifact)==order['accepted_artifact_identity_guard']
        if not requested[date]:continue
        con.read_parquet(str(artifact)).create_view('all_native',replace=True);con.register('selected',p.pa.table({'ticker':sorted(requested[date])}))
        con.execute('CREATE OR REPLACE VIEW native AS SELECT n.*,CASE WHEN event_ms BETWEEN 33900000 AND 34200999 THEN 570 WHEN event_ms BETWEEN 41400000 AND 41402999 THEN 689 WHEN event_ms BETWEEN 54000000 AND 54002999 THEN 899 ELSE event_ms//60000 END AS minute FROM all_native n JOIN selected s USING(ticker)')
        cursor=con.execute('SELECT ticker,minute,ARG_MIN(price_x10000,struct_pack(t:=event_ms,id:=tran_id)),MAX(price_x10000),MIN(price_x10000),ARG_MAX(price_x10000,struct_pack(t:=event_ms,id:=tran_id)),SUM(volume),SUM(price_x10000::HUGEINT*volume::HUGEINT),COUNT(*),MIN(event_ms),MAX(event_ms),MAX(GREATEST(event_ms,local_ms)),ARG_MIN(tran_id,struct_pack(t:=event_ms,id:=tran_id)),ARG_MAX(tran_id,struct_pack(t:=event_ms,id:=tran_id)) FROM native GROUP BY ticker,minute ORDER BY ticker,minute')
        current=None;bars={}

        def finish(ticker,actual):
            hs=requested[date][ticker];symbol=hs[0]['symbol'];sr=staged.get((date,ticker));primary_path=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');data=primary_path.read_bytes();primary=p.json.loads(data)
            if sr:assert str(primary_path)==sr['primary_source_path'] and p.hashlib.sha256(data).hexdigest()==sr['primary_source_sha256']
            day=next(dict(zip(primary['fields'],v)) for v in primary['rows'] if v[0]==date)
            native_day=[actual[min(actual)]['open'],max(b['high'] for b in actual.values()),min(b['low'] for b in actual.values()),actual[max(actual)]['close'],sum(b['volume'] for b in actual.values()),sum(b['amount_x10000'] for b in actual.values())]
            day_exact=day['tradestatus']=='1' and native_day==[int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
            byminute={}
            if sr:
                leaf=p.Path(sr['minute_path']);assert p.identity(leaf)==sr['verified_identity'];data=leaf.read_bytes();assert p.hashlib.sha256(data).hexdigest()==sr['minute_sha256'];rows=p.pq.ParquetFile(p.pa.BufferReader(data)).read().to_pylist();byminute={b['left_minute']:b for b in rows};assert len(rows)==len(byminute)==len(sr['hours'])*60
                fields=['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','first_transaction_id','last_transaction_id','source_last_event_ms','source_dependency_ready_ms']
                for b in rows:
                    assert b['symbol']==symbol and b['date']==date and b['source_kind']=='actual_millisecond_trade' and b['volume']>0 and b['trades']>0
                    assert all(b[f]==actual[b['left_minute']][f] for f in fields);counts['actual_minutes_verified']+=1
            qpath=p.Path(hs[0]['sina_reference_path']);data=qpath.read_bytes();digest=p.hashlib.sha256(data).hexdigest();assert all(h['sina_reference_sha256']==digest for h in hs)
            text=data.decode();qrows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);qrefs={q['day']:q for q in qrows};stock_accepted=0
            for request in hs:
                slot=request['slot'];key=date,symbol,slot;start=[570,630,780,840][slot];block=[actual[m] for m in range(start,start+60)]
                for m in range(start,start+60):
                    b=actual[m];end=m+1;q=qrefs[f'{date} {end//60:02d}:{end%60:02d}:00'];assert b['volume']>0 and b['trades']>0
                    assert all(b[f]==D(q[f])*10000 for f in ['open','high','low','close']) and D(b['volume'])==D(q['volume'])
                values=dict(open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close'],volume=sum(b['volume'] for b in block),amount_x10000=sum(b['amount_x10000'] for b in block),source_event_ms_max=max(b['source_last_event_ms'] for b in block),source_ready_ms=max(b['source_dependency_ready_ms'] for b in block))
                actual_hour={f:values[f] for f in refs[key]};previous=max((m for m in actual if m<start),default=None);carry=actual[previous]['close'] if previous is not None else values['open'];prior_exact=False
                if previous is not None:
                    end=previous+1;q=qrefs.get(f'{date} {end//60:02d}:{end%60:02d}:00');prior_exact=q is not None and D(q['close'])*10000==carry
                formula=dict(open=carry,high=max(carry,values['high']),low=min(carry,values['low']),close=values['close'],volume=values['volume'])
                kind='direct_coarse_ohlcv_exact' if actual_hour==refs[key] else 'fixed_previous_close_coarse_reconciled' if prior_exact and formula==refs[key] else None
                reason='primary_whole_day_six_conflict' if not day_exact else 'unexplained_coarse_conflict' if kind is None else None
                if reason:
                    assert key in rejected and key not in accepted;r=rejected[key];assert r['reason']==reason and r['native_hour']==actual_hour and r['coarse_reference']==refs[key]
                    assert r['coarse_differences']=={f:actual_hour[f]-refs[key][f] for f in refs[key]} and r['previous_observed_minute']==previous and r['previous_observed_close']==carry and r['previous_close_independent_exact']==prior_exact
                    counts[reason]+=1;verified_rejections.append(dict(date=date,symbol=symbol,slot=slot,reason=reason));continue
                assert key in accepted and key not in rejected;h=accepted[key];assert all(h[f]==v for f,v in values.items()) and h['coarse_reference']==refs[key] and h['coarse_reference_kind']==kind
                assert h['sina_reference_path']==str(qpath) and h['sina_reference_sha256']==digest and h['previous_observed_minute']==previous and h['previous_observed_close']==carry and h['previous_close_independent_exact']==prior_exact
                assert h['independent_minute60_ohlcv_exact'] and h['coarse_definition_reconciliation_exact'];counts[kind]+=1;stock_accepted+=1
                assert all(m in byminute for m in range(start,start+60))
            if sr:
                assert stock_accepted==len(sr['hours']) and day_exact and p.identity(p.Path(sr['minute_path']))==sr['verified_identity']
                verified.append(dict(date=date,symbol=symbol,hours=stock_accepted,all_actual_millisecond_fields_exact=True,primary_daily_six_exact=True))

        while rows:=cursor.fetchmany(4096):
            for v in rows:
                ticker,minute=v[:2]
                if ticker!=current:
                    if current is not None:finish(current,bars)
                    current=ticker;bars={}
                fields=['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','source_dependency_ready_ms','first_transaction_id','last_transaction_id'];assert int(minute) not in bars
                b=dict(zip(fields,map(int,v[2:])));b['source_last_event_ms']=b['last_ms'];bars[int(minute)]=b
        if current is not None:finish(current,bars)
        assert p.identity(artifact)==order['accepted_artifact_identity_guard']
    con.close();expected_counts={k:v for k,v in counts.items() if k!='actual_minutes_verified'};assert expected_counts==stage['counts']
    qualified=counts['direct_coarse_ohlcv_exact']+counts['fixed_previous_close_coarse_reconciled'];assert qualified==stage['hours_staged']==len(accepted) and counts['actual_minutes_verified']==qualified*60
    assert len(verified)==len(stage['records']) and len(verified_rejections)==len(stage['rejected_hours']) and p.identity(RAW/'dataset/hour_complete.npy')==identity
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),counts=dict(counts),stockdays_verified=len(verified),hours_verified=qualified,minutes_verified=qualified*60,
        all_candidate_and_rejected_hours_independently_reproduced=True,native_millisecond_fields_aggregated_independently=True,raw_tencent_responses_independently_decoded=True,
        all60_minute_independent_ohlcv_exact=True,all_native_daily_six_exact=True,observed_source_reception_dependencies_exact=True,native_first_trade_price_preserved=True,
        coarse_reference_direct_or_fixed_previous_close_formula_reproduced=True,other_coarse_conflicts_preserved=True,source_vendor_implementation_certified=False,
        raw_candidate_hours_admitted=0,raw_candidate_modified=False,source_live_clock_certificate=False,production_ready=False,records=verified,rejections=verified_rejections,
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss));lock.close()


if __name__=='__main__':main()
