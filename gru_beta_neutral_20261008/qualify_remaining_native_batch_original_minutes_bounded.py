"""Keep unresolved native-M1 definition conflicts explicit after M5 matches.

An exact five-minute bar does not certify intraminute OHLC definitions. This
diagnostic compares every observed original minute, without fitting an offset
or replacing native prices with previous closes or source-reference values.
"""
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from . import acquire_remaining_historical_native_batch_bounded as acquisition
p.sys.path.insert(0,str(p.ROOT/'gru_beta_neutral_20261008/canonical_ipo_v6'))
from .canonical_ipo_v6.backfilled_hour_panel import factor_state

def main(number):
    acquisition.configure_batch(number);dest=acquisition.DEST
    output=dest/'original_minute_and_cash_qualification.json';assert not output.exists()
    acp=dest/'receipt.json';ac=p.json.loads(acp.read_text())
    cp=dest/'coarse_reference_diagnostic.json';coarse=p.json.loads(cp.read_text())
    proofp=dest/'independent_coarse_verification.json';proof=p.json.loads(proofp.read_text())
    assert proof['passed'] and p.sha(cp)==proof['audited_coarse_diagnostic_sha256']
    dp=dest/'independent_daily_diagnostic.json';daily=p.json.loads(dp.read_text());assert p.sha(dp)==proof['audited_native_daily_diagnostic_sha256'] and daily['audited_acquisition_sha256']==p.sha(acp)
    op=p.OUT/'historical_quote_candidate_recheck/receipt.json';original=p.json.loads(op.read_text());originals={(r['symbol'],r['date']):r for r in original['results']}
    cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());mask=p.np.load(acquisition.RAW/'dataset/hour_complete.npy',mmap_mode='r')
    tables={};pins=[]
    for r in ac['results']:
        leaf=p.Path(r['selected_path']);assert p.identity(leaf)==r['selected_verified_identity'] and p.sha(leaf)==r['selected_sha256']
        tables[r['code'],r['field']]=p.pq.ParquetFile(leaf).read(use_threads=False).to_pylist();pins.append(dict(path=str(leaf),sha256=r['selected_sha256'],verified_identity=r['selected_verified_identity']))
    counts=Counter();records=[];source_pins=[]
    for r in coarse['results']:
        if not r['strict_coarse_pass_slots']:continue
        symbol,date=r['symbol'],r['date'];code=symbol[:6]
        fields={f:[v['value'] for v in tables[code,f] if v['datetime'].date().isoformat()==date] for f in acquisition.FIELDS};assert all(len(v)==240 for v in fields.values())
        prior=originals.get((symbol,date));oldbars=None
        if prior and prior.get('native_selected_path'):
            leaf=p.Path(prior['native_selected_path']);body=leaf.read_bytes();assert p.hashlib.sha256(body).hexdigest()==prior['native_selected_sha256']
            oldbars=p.json.loads(body)['normalized_bars'];assert len(oldbars)==240
            source_pins.append(dict(path=str(leaf),sha256=prior['native_selected_sha256'],verified_identity=p.identity(leaf)))
        primarypath=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary=p.json.loads(primarypath.read_text())
        factor=float(dict(zip(cal['dates'],factor_state(primary,cal['dates'])))[date]);assert p.np.isfinite(factor) and factor>0
        for slot in r['strict_coarse_pass_slots']:
            assert not mask[cal['dates'].index(date)*4+slot,cal['union_symbols'].index(symbol)]
            bad=[];conflicts=[];matches=Counter();previous_matches=0
            for i in range(slot*60,(slot+1)*60):
                b={f:D(str(fields[f][i])) for f in acquisition.FIELDS}
                if b['amount']<max(D(0),b['volume']-100)*b['low']-1 or b['amount']>(b['volume']+100)*b['high']+1:bad.append(i)
                if oldbars is not None:
                    delta={f:str(b[f]-D(oldbars[i][f])) for f in acquisition.FIELDS}
                    price_bad=any(D(delta[f])!=0 for f in ['open','high','low','close'])
                    for f in acquisition.FIELDS:matches[f+'_exact_minutes']+=D(delta[f])==0
                    if i%120 and b['open']==D(oldbars[i-1]['close']):previous_matches+=1
                    if price_bad:conflicts.append(dict(minute_index=i,differences=delta))
            status='known_source_minute_definition_conflict' if conflicts else ('missing_original_minute_reference' if oldbars is None else 'original_minute_prices_correspond_without_source_definition_certificate')
            records.append(dict(symbol=symbol,date=date,slot=slot,strict12_five_minute_ohlc_and_volume_pass=True,asof_adjustment_factor=factor,
                additional_cash_feasibility_failed_minute_indices=bad,original_minute_field_matches=dict(matches),original_minute_price_conflicts=conflicts,
                diagnostic_native_open_equals_previous_original_close_minutes=previous_matches,diagnostic_previous_close_comparison_changes_no_source_values=True,
                status=status,source_quantity_precision='published_integer_share_values; true_trade_quantity_uncertified',candidate_hours_admitted=0))
            counts['strict_coarse_hours_checked']+=1;counts['cash_feasibility_pass_hours']+=not bad;counts['original_minute_price_conflict_hours']+=bool(conflicts)
            counts['missing_original_minute_reference_hours']+=oldbars is None;counts['all60_original_minute_prices_equal_hours']+=oldbars is not None and not conflicts
    assert counts['strict_coarse_hours_checked']==coarse['strict_coarse_pass_hours']
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),source_acquisition_sha256=p.sha(acp),independent_coarse_proof_sha256=p.sha(proofp),original_minute_receipt_sha256=p.sha(op),selected_native_pins=pins,original_minute_pins=source_pins,counts=dict(counts),records=records,native_minute_clock_or_prices_modified=False,source_quantity_or_amount_values_reallocated=False,minute_definition_provider_or_live_clock_certified=False,candidate_hours_admitted=0,raw_panel_modified=False,training_admission_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    print(p.json.dumps(dict(counts=counts,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)),flush=True)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--batch',type=int,default=1);args=parser.parse_args();main(args.batch)
