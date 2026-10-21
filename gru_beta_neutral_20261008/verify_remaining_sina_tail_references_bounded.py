"""Audit public replies and native minute comparisons by bounded prefix groups."""
from collections import Counter,defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p


def main():
    lock=p.locked('verify_sina_remaining376');dest=p.OUT/'sina_remaining376_tail_references'
    path=dest/'receipt.json';receipt=p.json.loads(path.read_text());output=dest/'independent_verification.json';assert not output.exists()
    assert receipt['status'] in ['expanded_references_complete_no_admission','bounded_queries_stopped_on_public_permission_or_rate_response']
    assert receipt['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/probe_remaining_sina_tail_references_bounded.py')
    assert receipt['symbols_requested']==376 and receipt['candidate_hours_admitted']==0 and not receipt['missing_minutes_imputed']
    assert len({r['symbol'] for r in receipt['records']})==len(receipt['records'])
    prior_symbols=set()
    for pin in receipt['prior_query_pins']:
        prior_path=p.Path(pin['receipt_path']);proof_path=p.Path(pin['proof_path']);assert p.sha(prior_path)==pin['receipt_sha256'] and p.sha(proof_path)==pin['proof_sha256']
        proof=p.json.loads(proof_path.read_text());assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(prior_path)
        prior_symbols|={r['symbol'] for r in p.json.loads(prior_path.read_text())['records']}
    targets={t['symbol'] for (date,prefix),group in p.targets().items() if date=='2026-09-23' for t in group.values()}
    expected=targets-prior_symbols;actual={r['symbol'] for r in receipt['records']};assert len(expected)==376 and actual<=expected
    if receipt['status']=='expanded_references_complete_no_admission':assert actual==expected
    progress_path=p.OUT/'minute_observation_progress.json';assert p.sha(progress_path)==receipt['native_progress_sha256'];progress=p.json.loads(progress_path.read_text())
    original_leaves={leaf['path']:leaf for g in progress['groups'] for leaf in g['minute_files'] if leaf['model']=='floor_left'}
    pins={pin['path']:pin for pin in receipt['native_source_leaf_pins']};assert len(pins)==len(receipt['native_source_leaf_pins'])
    for pin in pins.values():assert pin['sha256']==original_leaves[pin['path']]['sha256'] and pin['rows']==original_leaves[pin['path']]['observed_minutes']
    by_prefix=defaultdict(list)
    for r in receipt['records']:by_prefix[r['symbol'][:2]].append(r)
    totals=Counter();verified=[];qualified=[];errors=[]
    for prefix,records in sorted(by_prefix.items()):
        leaf_paths={c['native_source_path'] for r in records if r['status']=='compared_without_admission' for c in r['comparisons']};frames={}
        for leaf in sorted(leaf_paths):
            pin=pins[leaf];source=p.Path(leaf);assert p.identity(source)==pin['verified_identity']
            footer=p.pq.ParquetFile(source);assert footer.metadata.num_rows==pin['rows']
            frames[leaf]=footer.read(columns=['ticker','left_minute','open','high','low','close','volume','amount_x10000']).to_pandas()
        for r in records:
            if r['status']!='compared_without_admission':errors.append(dict(symbol=r['symbol'],status=r['status'],error_type=r.get('error_type')));continue
            source=p.Path(r['path']);assert p.identity(source)==r['verified_identity'];data=source.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['sha256']
            text=data.decode();rows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);assert len(rows)==r['rows']
            refs={}
            for q in rows:
                stamp=p.datetime.strptime(q['day'],'%Y-%m-%d %H:%M:%S');assert stamp.second==0
                key=stamp.date().isoformat(),stamp.hour*60+stamp.minute-1;assert key not in refs
                values=[D(q[f])*10000 for f in ['open','high','low','close']]
                assert all(v>0 and v==v.to_integral_value() for v in values)
                assert values[1]>=max(values[0],values[3])>=min(values[0],values[3])>=values[2]
                qty=D(q['volume']);amount=D(q['amount']);assert qty>=0 and qty==qty.to_integral_value() and amount>=0
                refs[key]=dict(zip(['open','high','low','close'],map(int,values)),volume=qty,amount=amount)
            paired_total=0
            for comparison in r['comparisons']:
                date= comparison['date'];leaf=comparison['native_source_path'];assert comparison['native_source_sha256']==pins[leaf]['sha256']
                f=frames[leaf];stock=f.loc[f.ticker.eq(r['symbol'][:6])];assert len(stock)==comparison['native_observed_minutes'] and not stock.left_minute.duplicated().any()
                paired={};counts=Counter();all5=all6=0
                for row in stock.itertuples(index=False):
                    q=refs.get((date,int(row.left_minute)))
                    if q is None:continue
                    exact={f:D(int(getattr(row,f)))==D(q[f]) for f in ['open','high','low','close','volume']};cash=D(int(row.amount_x10000))/10000==q['amount']
                    good=all(exact.values());paired[int(row.left_minute)]=good;all5+=good;all6+=good and cash
                    for field,value in exact.items():counts[field]+=value
                assert len(paired)==comparison['paired_real_minutes'] and all5==comparison['minute_all5_exact'] and all6==comparison['minute_all6_exact'] and dict(counts)==comparison['field_exact_counts']
                assert sum(d==date for d,m in refs)==comparison['independent_observed_minutes'];paired_total+=len(paired)
                grid=list(range(570,690))+list(range(780,900))
                for hour in comparison['hours']:
                    minutes=grid[hour['slot']*60:(hour['slot']+1)*60];present=sum(m in paired for m in minutes);good=present==60 and all(paired[m] for m in minutes)
                    assert hour['paired_real_minutes']==present and hour['all60_price_and_volume_exact']==good;totals['all60_price_volume_exact_hours']+=good
                    if good:qualified.append(dict(symbol=r['symbol'],date=date,slot=hour['slot'],sina_reference_path=str(source),sina_reference_sha256=r['sha256'],native_minute_path=leaf,native_minute_sha256=comparison['native_source_sha256']))
            assert p.identity(source)==r['verified_identity'];verified.append(dict(symbol=r['symbol'],paired_real_minutes_checked=paired_total))
        for leaf in leaf_paths:assert p.identity(p.Path(leaf))==pins[leaf]['verified_identity']
        del frames
    assert dict(totals)==receipt['counts']
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),requested_symbols=376,
        stocks_verified=len(verified),queried_symbols=len(receipt['records']),all_requested_queries_complete=actual==expected,failed_queries_preserved=errors,
        paired_real_minutes_checked=sum(r['paired_real_minutes_checked'] for r in verified),all60_price_volume_exact_hours=totals['all60_price_volume_exact_hours'],
        qualified_minute_diagnostic_hours=qualified,stocks=verified,cash_disagreements_preserved=True,reported_end_label_to_left_interval_rule_predeclared=True,
        shared_native_minute_leaves_scanned_once=len(pins),accepted_native_fingerprints_reused_with_identity_footer_guards=True,
        missing_minutes_imputed=False,raw_candidate_hours_admitted=0,source_arrival_time_or_live_clock_certified=False,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    lock.close()


if __name__=='__main__':main()
