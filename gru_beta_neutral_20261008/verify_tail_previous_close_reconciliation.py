"""Independent SQL/native and raw vendor reply audit of the coarse diagnostic."""
from collections import Counter,defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
import duckdb


def main():
    directory=p.OUT/'tail_previous_close_coarse_reconciliation'
    output=directory/'independent_verification.json';assert not output.exists()
    path=directory/'receipt.json';r=p.json.loads(path.read_text())
    assert r['status']=='complete_fixed_formula_diagnostic_no_admission' and r['candidate_hours_admitted']==0
    assert r['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/diagnose_tail_hour_previous_close_bounded.py')
    wanted={(h['date'],h['symbol'],h['slot']):h for h in r['records']};assert len(wanted)==len(r['records'])==1111
    acquisition_path=p.OUT/'tencent_minute_references/all_target_stock_acquisition.json';acquisition=p.json.loads(acquisition_path.read_text())
    coarse_path=acquisition_path.parent/'all_tail_hour_comparison.json';coarse=p.json.loads(coarse_path.read_text())
    assert p.sha(coarse_path)==r['coarse_comparison_sha256'] and coarse['source_acquisition_sha256']==p.sha(acquisition_path)
    refs={};needed={h['symbol'] for h in r['records']}
    for source in acquisition['records']:
        if source['symbol'] not in needed:continue
        qpath=p.Path(source['path']);assert p.identity(qpath)==source['verified_identity']
        data=qpath.read_bytes();assert p.hashlib.sha256(data).hexdigest()==source['sha256']
        for values in p.json.loads(data)['data'][source['code']]['m60']:
            stamp=values[0];date=f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}';slot=['1030','1130','1400','1500'].index(stamp[-4:])
            key=date,source['symbol'],slot
            if key not in wanted:continue
            q={f:int(D(values[i])*10000) for f,i in [('open',1),('close',2),('high',3),('low',4)]};q['volume']=int(D(values[5])*100)
            assert q==wanted[key]['coarse_reference'];refs[key]=q
        assert p.identity(qpath)==source['verified_identity']
    assert set(refs)==set(wanted)
    raw=p.BASE/'backfilled_panel_candidate_historical_partial_reported_v14_raw';audit_path=raw/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text())
    assert audit['passed'] and p.sha(audit_path)==r['parent_raw_audit_sha256'] and audit['audited_raw_meta_sha256']==p.sha(raw/'dataset/meta.json')
    identity=next(x['verified_identity'] for x in audit['arrays'] if x['name']=='hour_complete.npy');assert p.identity(raw/'dataset/hour_complete.npy')==identity
    mask=p.np.load(raw/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    by_leaf=defaultdict(list)
    for h in r['records']:by_leaf[h['native_minute_path']].append(h)
    pins={x['path']:x for x in r['leaf_pins']};counts=Counter();diffs=Counter();qualified=[]
    con=duckdb.connect();con.execute("SET memory_limit='128MB'");con.execute('SET threads=1')
    for leaf,records in sorted(by_leaf.items()):
        source=p.Path(leaf);pin=pins[leaf];assert p.identity(source)==pin['verified_identity']
        # Each small accepted minute leaf is scanned once; no whole trade archive.
        con.read_parquet(leaf,hive_partitioning=False).create_view('minute_source',replace=True)
        cursor=con.execute('SELECT ticker,left_minute,open,high,low,close,volume,trades FROM minute_source ORDER BY ticker,left_minute')
        native=defaultdict(dict)
        for ticker,m,o,hi,lo,c,v,n in cursor.fetchall():native[ticker][int(m)]=dict(open=int(o),high=int(hi),low=int(lo),close=int(c),volume=int(v),trades=int(n))
        con.execute('CREATE OR REPLACE VIEW marked AS SELECT *,CASE WHEN left_minute<690 THEN (left_minute-570)//60 ELSE (left_minute-780)//60+2 END AS slot FROM minute_source')
        columns=['open','high','low','close','volume']
        totals={(ticker,int(slot)):dict(zip(columns,map(int,v))) for ticker,slot,*v in con.execute('SELECT ticker,slot,ARG_MIN(open,left_minute),MAX(high),MIN(low),ARG_MAX(close,left_minute),SUM(volume) FROM marked GROUP BY ticker,slot').fetchall()}
        by_ref=defaultdict(list)
        for h in records:by_ref[h['independent_minute_path']].append(h)
        for qpath,requested in sorted(by_ref.items()):
            qsource=p.Path(qpath);qbefore=p.identity(qsource);data=qsource.read_bytes();digest=p.hashlib.sha256(data).hexdigest()
            assert all(h['independent_minute_sha256']==digest for h in requested)
            text=data.decode();rows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);qrefs={q['day']:q for q in rows}
            for h in requested:
                date,symbol,slot=h['date'],h['symbol'],h['slot'];key=date,symbol,slot;start=[570,630,780,840][slot];bars=native[symbol[:6]]
                a=totals[symbol[:6],slot];assert a==h['native']
                for m in range(start,start+60):
                    b=bars[m];end=m+1;q=qrefs[f'{date} {end//60:02d}:{end%60:02d}:00'];assert b['volume']>0 and b['trades']>0
                    assert all(b[f]==D(q[f])*10000 for f in ['open','high','low','close']) and D(b['volume'])==D(q['volume'])
                previous=max((m for m in bars if m<start),default=None);carry=bars[previous]['close'] if previous is not None else a['open']
                assert previous==h['previous_observed_minute'] and carry==h['previous_observed_close']
                prev_exact=False
                if previous is not None:
                    end=previous+1;q=qrefs.get(f'{date} {end//60:02d}:{end%60:02d}:00');prev_exact=q is not None and D(q['close'])*10000==carry
                model=dict(a,open=carry,high=max(carry,a['high']),low=min(carry,a['low']));same=model==refs[key]
                assert model==h['fixed_formula'] and same==h['fixed_formula_all5_exact'] and prev_exact==h['previous_close_independent_minute_exact']
                assert {f:a[f]-refs[key][f] for f in a}==h['original_coarse_differences']
                complete=bool(mask[cal['dates'].index(date)*4+slot,cal['union_symbols'].index(symbol)]);assert complete==h['raw_already_admitted']
                counts['minute_exact_diagnostic_hours']+=1;counts['already_admitted_raw_hours']+=complete;counts['fixed_coarse_formula_exact_all_hours']+=same
                if not complete:
                    counts['remaining_minute_exact_hours']+=1;counts['remaining_fixed_formula_exact']+=same
                    counts['remaining_fixed_formula_and_previous_close_reference_exact']+=same and prev_exact
                    diffs[','.join(sorted(f for f in a if a[f]!=refs[key][f]))]+=1
                    if same and prev_exact:qualified.append(dict(date=date,symbol=symbol,slot=slot))
            assert p.identity(qsource)==qbefore
        assert p.identity(source)==pin['verified_identity'];del native,totals
    con.close();assert dict(counts)==r['counts'] and dict(diffs)==r['remaining_difference_counts'] and len(qualified)==104
    assert p.identity(raw/'dataset/hour_complete.npy')==identity
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),
        all1111_minute_reference_hours_independently_reproduced=True,raw_tencent_responses_independently_decoded=True,counts=dict(counts),
        qualified_fixed_formula_diagnostic_hours=qualified,candidate_hours_admitted=0,source_vendor_implementation_certified=False,
        native_first_trade_minute_definition_unchanged=True,other523_coarse_conflicts_preserved=True,production_ready=False,
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))


if __name__=='__main__':main()
