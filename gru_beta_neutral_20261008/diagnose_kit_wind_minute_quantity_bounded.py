"""Compare observed quantity fields, without repairing or admitting any bar.

Only four relevant stock/year streams are read, once each for useful date
selection and the accepted digest together. Existing selected days are reused.
Published minute quantities are never allocated from a daily total.
"""
import csv
import io
from collections import Counter, defaultdict
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_EVEN
from . import bounded_tail_pipeline as p
from .recheck_historical_quote_candidates import DigestReader, normalize_rows, GRID

KIT=p.BASE/'public_source_probes/new_public_history_and_tail_inventory_20261009/Lucifer744__KiT_data_demo/historical_minute_overlap_diagnostic'
WIND=p.BASE/'public_source_probes/additional_public_archives_20261009/wind17'
DEST=p.OUT/'kit_wind_minute_quantity_diagnostic'


def main():
    lock=p.locked('diagnose_kit_wind_minute_quantity');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    kit_path=KIT/'receipt.json';kit=p.json.loads(kit_path.read_text());assert kit['status']=='complete_72_hour_diagnostic_no_admission' and len(kit['records'])==18
    scope_path=WIND/'all_minimum_gap_consolidation/verified_minimum_gap_source_scope.json';scope=p.json.loads(scope_path.read_text());assert scope['passed'] and scope['actual_missing_hours']==3042
    scope_by_day={(r['symbol'],r['date']):r for r in scope['records']}
    cache_path=p.OUT/'historical_quote_candidate_recheck/receipt.json';cache=p.json.loads(cache_path.read_text());cached={(d['symbol'],d['date']):d for f in cache['native_files'] for d in f['days']}
    result=dict(status='selecting_only18_observed_days',started_utc=p.utc(),source_code_sha256=p.sha(__file__),kit_receipt_sha256=p.sha(kit_path),scope_sha256=p.sha(scope_path),cached_recheck_sha256=p.sha(cache_path),
        quantity_hypotheses=['published_quantity_direct','floor_published_quantity_to100_shares','ceil_published_quantity_to100_shares','nearest_even_published_quantity_to100_shares'],
        fixed_clock_rule='Observed end09:31..11:30/13:01..15:00; positive native09:30 auction merged into09:31; no source rows invented.',
        unknown_KiT_upstream_and_adjustment_policy_not_certified=True,candidate_hours_admitted=0,raw_candidate_modified=False,daily_gate_relaxed=False,records=[],native_files=[],counts={})
    def save():
        result.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
    save();groups=defaultdict(list);leaves={}
    for r in kit['records']:
        key=r['symbol'],r['date'];s=scope_by_day[key]
        if key in cached:
            c=cached[key];path=p.Path(c['selected_path']);assert p.sha(path)==c['selected_sha256'];leaves[key]=c
        else:groups[s['source_raw_path']].append((r,s))
    for filename,group in sorted(groups.items()):
        path=p.Path(filename);identity=p.identity(path);expected={s['source_raw_sha256'] for r,s in group};assert len(expected)==1
        selected={r['date']:[] for r,s in group};raw=DigestReader(path)
        with io.TextIOWrapper(io.BufferedReader(raw,1<<20),encoding='gbk',newline='') as stream:
            for row in csv.DictReader(stream):
                date=row['k线结束时间'][:10]
                if date in selected:selected[date].append(row)
            digest,count=raw.digest.hexdigest(),raw.count
        assert p.identity(path)==identity and digest in expected and count==identity['size']
        file_record=dict(path=filename,sha256=digest,bytes=count,verified_identity=identity,hash_computed_during_useful_filtered_read=True,selected_days=[])
        for r,s in group:
            rows=selected[r['date']];assert len(rows)<=242
            bars=normalize_rows(rows);leaf=DEST/(r['symbol']+'_'+r['date']+'_original_native.json')
            p.atomic_json(leaf,dict(symbol=r['symbol'],date=r['date'],native_source_path=filename,native_source_sha256=digest,raw_selected_rows=rows,end_minute_grid=GRID,normalized_bars=[{f:str(v) for f,v in b.items()} for b in bars]))
            c=dict(symbol=r['symbol'],date=r['date'],selected_path=str(leaf),selected_sha256=p.sha(leaf));leaves[(r['symbol'],r['date'])]=c;file_record['selected_days'].append(c)
        result['native_files'].append(file_record);save()
    counts=Counter()
    for r in kit['records']:
        key=r['symbol'],r['date'];leaf=leaves[key];native_path=p.Path(leaf['selected_path']);assert p.sha(native_path)==leaf['selected_sha256'];native=p.json.loads(native_path.read_text())
        bars=[{f:D(v) for f,v in b.items()} for b in native['normalized_bars']]
        source=p.Path(r['selected_published_minutes_path']);assert p.identity(source)==r['selected_verified_identity'] and p.sha(source)==r['selected_published_minutes_sha256']
        frame=p.pq.read_table(source).to_pandas().sort_values('datetime');assert len(frame)==240
        minutes=(frame.datetime.dt.hour*60+frame.datetime.dt.minute).tolist();assert minutes==GRID
        qty=[D(str(x)) for x in frame.volume];assert all(q>=0 and q==q.to_integral_value() for q in qty)
        old=[b['volume'] for b in bars];delta=[q-v for q,v in zip(qty,old)]
        models={
            'published_quantity_direct':qty,
            'floor_published_quantity_to100_shares':[(q/100).to_integral_value(rounding=ROUND_FLOOR)*100 for q in qty],
            'ceil_published_quantity_to100_shares':[(q/100).to_integral_value(rounding=ROUND_CEILING)*100 for q in qty],
            'nearest_even_published_quantity_to100_shares':[(q/100).to_integral_value(rounding=ROUND_HALF_EVEN)*100 for q in qty]}
        matched={name:sum(a==b for a,b in zip(values,old)) for name,values in models.items()}
        assert sum(qty)==r['source_volume_sum']==r['primary_volume']
        details=[dict(end_minute=GRID[i],published_quantity_shares=str(qty[i]),original_lots_times100_shares=str(old[i]),quantity_difference_shares=str(delta[i]),original_amount_CNY=str(bars[i]['amount']),original_raw_prices={f:str(bars[i][f]) for f in ['open','high','low','close']},published_adjusted_prices={f:float(frame.iloc[i][f]) for f in ['open','high','low','close']}) for i in range(240)]
        detail_path=DEST/(r['symbol']+'_'+r['date']+'_minute_field_comparison.json');p.atomic_json(detail_path,dict(symbol=r['symbol'],date=r['date'],minutes=details,quantity_only_diagnostic_no_field_substitution=True))
        observation=dict(symbol=r['symbol'],date=r['date'],slots=r['slots'],native_selected_path=str(native_path),native_selected_sha256=leaf['selected_sha256'],kit_selected_path=str(source),kit_selected_sha256=r['selected_published_minutes_sha256'],
            source_primary_daily_path=r['primary_path'],source_primary_daily_sha256=r['primary_sha256'],minute_grid_exact=True,minute_rows=240,published_daily_volume=int(sum(qty)),original_daily_volume=int(sum(old)),primary_daily_volume=r['primary_volume'],
            published_minus_original_quantity_sum=int(sum(delta)),minimum_minute_quantity_difference=int(min(delta)),maximum_minute_quantity_difference=int(max(delta)),nonzero_quantity_difference_minutes=sum(d!=0 for d in delta),
            negative_quantity_difference_minutes=sum(d<0 for d in delta),fixed_quantity_model_matched_minutes=matched,fixed_model_all240_exact=[n for n,c in matched.items() if c==240],
            minute_comparison_path=str(detail_path),minute_comparison_sha256=p.sha(detail_path),quantity_field_substitution_admitted=False)
        result['records'].append(observation);counts['checked_days']+=1;counts['checked_minutes']+=240
        for name,count in matched.items():counts[name+'_exact_minutes']+=count;counts[name+'_exact_days']+=int(count==240)
        result['counts']=dict(counts);save()
    result.update(status='complete18day_quantity_diagnostic_no_admission',finished_utc=p.utc(),whole_file_prefingerprints_repeated=0,source_year_files_usefully_read=len(groups),existing_selected_days_reused=len(kit['records'])-sum(len(v) for v in groups.values()));save();lock.close()


if __name__=='__main__':main()
