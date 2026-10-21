"""Test one fixed observed-second LastPrice reduction on unresolved first hours.
This diagnostic never changes real M1, fits a sampling phase, or admits a bar.
"""
from collections import Counter
from . import bounded_tail_pipeline as p
DEST=p.OUT/'first_hour_observed_second_close_diagnostic'
PARENT=p.OUT/'remaining_quote_conflict_classification_v21/receipt.json'
def main():
    DEST.mkdir(exist_ok=False);parent=p.json.loads(PARENT.read_text());requests=[r for r in parent['records'] if r['slot']==0 and r['differences']['volume']==0];assert len(requests)==257
    endpoint=p.json.loads((p.OUT/'tencent_first_hour_cumulative_extrema_diagnostic/receipt.json').read_text());ends={(r['date'],r['symbol']):r['current_quote'] for r in endpoint['records']}
    result=dict(status='diagnosing_one_observed_second_close_rule',started_utc=p.utc(),source_code_sha256=p.sha(__file__),parent_receipt_sha256=p.sha(PARENT),definition='Within the unique actual trade prefix corresponding exactly to the previously fixed strict-before10:30 source endpoint cumulative shares/cash/lastprice, retain the chronological last actual trade of each observed reported event second. Open remains the actual first trade; High/Low are extrema of those observed second closes and actual Open; Close/quantity remain the fixed endpoint. Empty seconds are absent. No cadence phase, time cut, rounding tolerance, or model is searched. This is an evidence diagnostic only; true minute first/max/min/last remains unchanged.',requested_stockdays=257,records=[],source_pins=[],counts={},useful_rowgroups_read=0,archive_fingerprints_repeated=False,candidate_hours_admitted=0,source_vendor_implementation_certified=False,raw_candidate_modified=False,canonical_M1_changed=False)
    counts=Counter()
    for date in sorted({r['date'] for r in requests}):
        pairpath=p.OUT/('millisecond_pairing_sep22' if date=='2026-09-22' else 'millisecond_pairing')/'receipt.json';pair=p.json.loads(pairpath.read_text());orderpath=pairpath.parent/'intrasecond_order_verification.json';order=p.json.loads(orderpath.read_text());assert pair['all_rows_price_quantity_and_floor_clock_exact'] and order['paired_source_receipt_sha256']==p.sha(pairpath) and order['intrasecond_event_inversions']==0
        path=p.Path(pair['native_field_artifact']['path']);identity=order['accepted_artifact_identity_guard'];assert p.identity(path)==identity
        pf=p.pq.ParquetFile(path);tickercol=pf.schema_arrow.names.index('ticker');index={}
        for g in range(pf.num_row_groups):
            stats=pf.metadata.row_group(g).column(tickercol).statistics;assert stats and stats.min==stats.max
            index.setdefault(str(stats.min),[]).append(g)
        result['source_pins'].append(dict(date=date,pairing_receipt_sha256=p.sha(pairpath),order_receipt_sha256=p.sha(orderpath),path=str(path),accepted_identity=identity,whole_artifact_digest_reused_from_accepted_proof=True,total_rowgroups=pf.num_row_groups))
        for request in [r for r in requests if r['date']==date]:
            symbol=request['symbol'];groups=index[symbol[:6]];cols=['event_ms','tran_id','channel','price_x10000','volume'];chunks=[]
            for g in groups:
                for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,row_groups=[g],columns=cols,use_threads=False):chunks.append({c:batch.column(i).to_numpy(zero_copy_only=False) for i,c in enumerate(cols)})
            a={c:p.np.concatenate([x[c] for x in chunks]) for c in cols};n=len(a['volume']);assert 0<n<=1000000;ix=p.np.lexsort((a['channel'],a['tran_id'],a['event_ms']));a={c:v[ix] for c,v in a.items()};quantity=a['volume'].astype('int64');cash=quantity*a['price_x10000'];cumq=p.np.cumsum(quantity);cumcash=p.np.cumsum(cash);end=ends[date,symbol];cut=int(p.np.searchsorted(cumq,end['quantity']));valid=cut<n and int(cumq[cut])==end['quantity'] and int(cumcash[cut])==end['amount_x10000'] and int(a['price_x10000'][cut])==end['price_x10000']
            row=dict(symbol=symbol,date=date,slot=0,native_rows_read=n,rowgroups_read=len(groups),fixed_endpoint=end,endpoint_actual_unique_prefix_exact=bool(valid),tencent_hour=request['tencent_hour'])
            if valid:
                seconds=a['event_ms'][:cut+1]//1000;ends_ix=p.np.r_[p.np.flatnonzero(seconds[1:]!=seconds[:-1]),cut];prices=a['price_x10000'][ends_ix];opening=int(a['price_x10000'][0]);model=dict(open=opening,high=max(opening,int(prices.max())),low=min(opening,int(prices.min())),close=int(a['price_x10000'][cut]),volume=int(cumq[cut]));diff={f:model[f]-request['tencent_hour'][f] for f in model};exact=not any(diff.values());counts['fixed_observed_second_close_exact' if exact else 'fixed_observed_second_close_conflict']+=1
                folder=DEST/('date='+date)/('symbol='+symbol);folder.mkdir(parents=True);leaf=folder/'actual_second_close_samples.parquet';table=p.pa.table({c:a[c][ends_ix] for c in cols});p.pq.write_table(table,leaf,compression='zstd')
                row.update(observed_second_samples=len(prices),actual_prefix_last_trade_index=cut,actual_prefix_high=int(a['price_x10000'][:cut+1].max()),actual_prefix_low=int(a['price_x10000'][:cut+1].min()),source_cumulative_extrema_prefix_exact=int(a['price_x10000'][:cut+1].max())==end['high_price_x10000'] and int(a['price_x10000'][:cut+1].min())==end['low_price_x10000'],fixed_model=model,differences=diff,all5_exact=exact,actual_sample_path=str(leaf),actual_sample_sha256=p.sha(leaf),actual_sample_identity=p.identity(leaf))
            else:counts['actual_prefix_conflict']+=1
            result['records'].append(row);result['useful_rowgroups_read']+=len(groups);result.update(counts=dict(counts),updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(DEST/'receipt.json',result);del a,chunks
        assert p.identity(path)==identity
    result.update(status='complete_one_observed_second_close_diagnostic_no_admission',finished_utc=p.utc());p.atomic_json(DEST/'receipt.json',result);print(p.json.dumps({k:v for k,v in result.items() if k not in ['records','source_pins']},ensure_ascii=False))
if __name__=='__main__':main()
