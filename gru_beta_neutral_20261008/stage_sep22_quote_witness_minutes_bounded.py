"""Observe genuine empty-minute quotes using one stock's numeric trade prefix.

Accepted in-flight source digests are reused behind identity guards. No whole
event file is reread, no Python trade table is constructed, and no price is
carried into an interval without an actual exact-prefix quote in that interval.
Staging alone never changes a panel or clears training admission.
"""
from collections import defaultdict, Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import GRID, labels

DAY = '2026-09-22'
WHOLE = p.BASE/'public_source_probes/additional_native_inventory_20261009/whole_event_archive_sep22'
RAW = p.BASE/'backfilled_panel_candidate_native_millisecond_v11_raw'
DEST = p.OUT/'sep22_quote_witness_minute_staging'
FIELDS = ['event_ms', 'local_ms', 'tran_id', 'channel', 'price_x10000', 'volume']


def clock(value):
    h, m, s = str(value).split(':')
    return int(h)*3600000 + int(m)*60000 + int(D(s)*1000)


def main():
    lock = p.locked('stage_sep22_quote_witness_minutes')
    DEST.mkdir(exist_ok=True)
    output = DEST/'receipt.json'
    assert not output.exists()
    pair_path = p.OUT/'millisecond_pairing_sep22/receipt.json'
    pair = p.json.loads(pair_path.read_text())
    order_path = pair_path.parent/'intrasecond_order_verification.json'
    order = p.json.loads(order_path.read_text())
    assert pair['all_rows_price_quantity_and_floor_clock_exact']
    assert order['paired_source_receipt_sha256'] == p.sha(pair_path)
    assert order['intrasecond_event_inversions'] == order['minute_open_changed_by_intrasecond_order'] == order['minute_close_changed_by_intrasecond_order'] == 0
    artifact = p.Path(pair['native_field_artifact']['path'])
    assert p.identity(artifact) == order['accepted_artifact_identity_guard']
    acquisition_path = WHOLE/'acquisition_and_extraction.json'
    acquisition = p.json.loads(acquisition_path.read_text())
    member_proof_path = WHOLE/'independent_member_verification.json'
    member_proof = p.json.loads(member_proof_path.read_text())
    assert member_proof['passed'] and member_proof['audited_receipt_sha256'] == p.sha(acquisition_path)
    leaves = {r['symbol']: r for r in acquisition['records'] if r['kind']=='snapshot'}
    raw_proof_path = RAW/'raw_full_history_audit.json'
    raw_proof = p.json.loads(raw_proof_path.read_text())
    assert raw_proof['passed'] and raw_proof['audited_raw_meta_sha256'] == p.sha(RAW/'dataset/meta.json')
    calendar = p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    mask = p.np.load(RAW/'dataset/hour_complete.npy', mmap_mode='r')
    symbol_index = {s:i for i,s in enumerate(calendar['union_symbols'])}
    offset = calendar['dates'].index(DAY)*4
    coarse_path = p.OUT/'tencent_minute_references/all_tail_hour_comparison.json'
    coarse = p.json.loads(coarse_path.read_text())
    coarse_proof_path = coarse_path.parent/'independent_hour_comparison_verification.json'
    coarse_proof = p.json.loads(coarse_proof_path.read_text())
    assert coarse_proof['passed'] and coarse_proof['audited_receipt_sha256']==p.sha(coarse_path)
    wanted = defaultdict(dict)
    for group in coarse['groups']:
        if group['date'] != DAY: continue
        data = p.Path(group['comparison_path']).read_bytes()
        assert p.hashlib.sha256(data).hexdigest() == group['comparison_sha256']
        for h in p.json.loads(data)['comparisons']:
            if h['model']!='floor_left' or not h.get('differences') or any(h['differences'].values()): continue
            if not mask[offset+h['slot'], symbol_index[h['symbol']]]:
                wanted[h['symbol']][h['slot']] = h['native_hour']
    receipt = dict(status='staging', started_utc=p.utc(), source_code_sha256=p.sha(__file__), date=DAY,
        source_pairing_sha256=p.sha(pair_path), source_order_proof_sha256=p.sha(order_path),
        accepted_artifact_identity=order['accepted_artifact_identity_guard'],
        source_member_proof_sha256=p.sha(member_proof_path), parent_raw_audit_sha256=p.sha(raw_proof_path),
        independent_hour_proof_sha256=p.sha(coarse_proof_path), requested_hours=sum(map(len,wanted.values())),
        definition='Fixed actual floor-left millisecond trade candles with the existing two-second phase grace; a zero-trade interval requires a real positive same-interval quote with exact whole native trade-prefix quantity, integer calculated cash and last trade price; no inferred quantity or fabricated price.',
        source_live_clock_certified=False, candidate_hours_admitted=0, raw_panel_modified=False, production_ready=False,
        records=[], diagnostics=[], excluded={})
    counts = Counter()
    max_resident_rows = 0

    def finish(ticker, chunks):
        nonlocal max_resident_rows
        symbol = ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
        if symbol not in wanted: return
        a = {f:p.np.concatenate([c[f] for c in chunks]) for f in FIELDS}
        n = len(a['volume']); assert 0<n<=1000000
        max_resident_rows = max(max_resident_rows,n)
        ix = p.np.lexsort((a['channel'], a['tran_id'], a['event_ms']))
        a = {f:v[ix] for f,v in a.items()}
        assert (a['volume']>0).all() and (a['price_x10000']>0).all()
        assert int(a['volume'].max())*int(a['price_x10000'].max())*n < 2**63
        qty = a['volume'].astype('int64'); cash = qty*a['price_x10000']
        cum_qty = p.np.cumsum(qty); cum_cash = p.np.cumsum(cash)
        ready = p.np.maximum(a['event_ms'],a['local_ms'])
        prefix_ready = p.np.maximum.accumulate(ready)
        valid, minutes = labels(a['event_ms']//1000,'floor_left'); assert valid.all()
        unique, begins, sizes = p.np.unique(minutes, return_index=True, return_counts=True)
        bars = {}
        for minute,begin,size in zip(unique,begins,sizes):
            start=int(begin); end=start+int(size); prices=a['price_x10000'][start:end]
            bars[int(minute)] = dict(open=int(prices[0]), high=int(prices.max()), low=int(prices.min()), close=int(prices[-1]),
                volume=int(qty[start:end].sum()), amount_x10000=int(cash[start:end].sum()), trades=int(size),
                first_ms=int(a['event_ms'][start]), first_transaction_id=int(a['tran_id'][start]),
                last_ms=int(a['event_ms'][end-1]), last_transaction_id=int(a['tran_id'][end-1]),
                source_last_event_ms=int(a['event_ms'][end-1]), source_dependency_ready_ms=int(ready[start:end].max()),
                source_kind='actual_native_millisecond_trades')
        primary_path = p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json')
        primary = p.json.loads(primary_path.read_text())
        daily = next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==DAY)
        actual = [int(a['price_x10000'][0]),int(a['price_x10000'].max()),int(a['price_x10000'].min()),int(a['price_x10000'][-1]),int(cum_qty[-1]),int(cum_cash[-1])]
        expected_daily = [int(D(daily[f])*10000) for f in ['open','high','low','close']]+[int(daily['volume']),int(D(daily['amount'])*10000)]
        if daily['tradestatus']!='1' or actual!=expected_daily:
            counts['primary_daily_six_conflict']+=len(wanted[symbol]);return
        missing = {}; running_qty=running_cash=0
        for minute in GRID:
            if minute in bars:
                running_qty+=bars[minute]['volume'];running_cash+=bars[minute]['amount_x10000']
            else: missing[minute]=(running_qty,running_cash)
        leaf=leaves[symbol]; path=p.Path(leaf['path'])
        assert p.identity(path)==leaf['verified_identity']
        vf='TradVolume' if symbol.endswith('SH') else 'Volume'
        cols=['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','Turnover',vf]
        quotes={};stats=Counter()
        for batch in p.pq.ParquetFile(path).iter_batches(batch_size=p.BATCH_ROWS,columns=cols,use_threads=False):
            for q in batch.to_pylist():
                t=clock(q['UpdateTime']);minute=t//60000
                if minute not in missing:continue
                stats['same_empty_interval_quote_rows']+=1
                quantity=D(str(q[vf]));amount=D(str(q['Turnover']));price=D(str(q['LastPrice']))
                if q['SecurityID']!=ticker or quantity<=0 or quantity!=quantity.to_integral_value():continue
                i=int(p.np.searchsorted(cum_qty,int(quantity)))
                if i==n or int(cum_qty[i])!=quantity:continue
                stats['actual_whole_trade_quantity_cut_exact']+=1
                if int(cum_cash[i])!=amount*10000 or int(a['price_x10000'][i])!=price*10000:continue
                stats['quantity_cash_last_price_exact']+=1
                if missing[minute]!=(int(quantity),int(cum_cash[i])) or int(a['event_ms'][i])>=((minute+1)*60000):continue
                received=clock(q['LocalTime']);key=(t,received,str(q['SeqNo']))
                witness=dict(source_quote_reported_ms=t,source_quote_received_ms=received,source_quote_sequence=str(q['SeqNo']),
                    source_quote_quantity=int(quantity),source_quote_amount_x10000=int(cum_cash[i]),source_quote_last_price_x10000=int(a['price_x10000'][i]),
                    source_prefix_trade_count=i+1,source_prefix_last_event_ms=int(a['event_ms'][i]),source_prefix_last_local_ms=int(a['local_ms'][i]),
                    source_prefix_last_transaction_id=int(a['tran_id'][i]),source_prefix_last_channel=int(a['channel'][i]),
                    source_prefix_ready_ms=int(prefix_ready[i]), source_dependency_ready_ms=max(received,int(prefix_ready[i])))
                if minute not in quotes or key<quotes[minute][0]:quotes[minute]=(key,witness)
        assert p.identity(path)==leaf['verified_identity']
        for minute,(_,witness) in quotes.items():
            price=witness['source_quote_last_price_x10000']
            bars[minute]=dict(open=price,high=price,low=price,close=price,volume=0,amount_x10000=0,trades=0,
                source_kind='genuine_quote_in_verified_zero_trade_interval',source_last_event_ms=witness['source_prefix_last_event_ms'],**witness)
        rows=[];hours=[];quarantine=[]
        for slot,expected in sorted(wanted[symbol].items()):
            grid=GRID[slot*60:(slot+1)*60];absent=[m for m in grid if m not in bars]
            if absent:
                counts['missing_genuine_quote']+=1;quarantine.append(dict(slot=slot,reason='missing_genuine_quote',minutes=absent));continue
            b=[bars[m] for m in grid]
            h=dict(slot=slot,date=DAY,open=b[0]['open'],high=max(r['high'] for r in b),low=min(r['low'] for r in b),close=b[-1]['close'],
                volume=sum(r['volume'] for r in b),amount_x10000=sum(r['amount_x10000'] for r in b),
                source_event_ms_max=max(r['source_last_event_ms'] for r in b),source_ready_ms=max(r['source_dependency_ready_ms'] for r in b),
                actual_trade_minutes=sum(r['trades']>0 for r in b),genuine_zero_trade_quote_minutes=sum(r['trades']==0 for r in b))
            if any(h[f]!=expected[f] for f in ['open','high','low','close','volume']):
                counts['hour_reference_conflict_after_real_quote']+=1;quarantine.append(dict(slot=slot,reason='hour_reference_conflict_after_real_quote'));continue
            hours.append(h);rows.extend(dict(symbol=symbol,date=DAY,left_minute=m,**bars[m]) for m in grid)
        receipt['diagnostics'].append(dict(symbol=symbol,actual_trade_rows=n,original_empty_minutes=len(missing),verified_genuine_quote_minutes=len(quotes),quote_stats=dict(stats),quarantined_hours=quarantine))
        if hours:
            folder=DEST/('symbol='+symbol);folder.mkdir(exist_ok=True);output_leaf=folder/'native_candidate_minutes.parquet'
            keys=set().union(*(r.keys() for r in rows))
            p.pq.write_table(p.pa.Table.from_pylist([{k:r.get(k) for k in keys} for r in rows]),output_leaf,compression='zstd')
            receipt['records'].append(dict(symbol=symbol,date=DAY,hours=hours,minute_path=str(output_leaf),minute_sha256=p.sha(output_leaf),verified_identity=p.identity(output_leaf),
                primary_source_path=str(primary_path),primary_source_sha256=p.sha(primary_path),stockday_native_primary_six_exact=True,
                quote_source_path=str(path),quote_source_sha256=leaf['sha256'],quote_source_identity=leaf['verified_identity']))
        receipt.update(updated_utc=p.utc(),excluded=dict(counts),stockdays_checked=len(receipt['diagnostics']))
        p.atomic_json(output,receipt)

    current=None;chunks=[];completed=set();resident=0
    for batch in p.pq.ParquetFile(artifact).iter_batches(batch_size=p.BATCH_ROWS,use_threads=False):
        frame=batch.to_pandas()
        for ticker,part in frame.groupby('ticker',sort=False):
            if ticker!=current:
                if current is not None:finish(current,chunks);completed.add(current)
                assert ticker not in completed
                current=ticker;chunks=[];resident=0
            symbol=ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
            if symbol not in wanted:continue
            resident+=len(part);assert resident<=1000000
            chunks.append({f:part[f].to_numpy(copy=True) for f in FIELDS})
    if current is not None:finish(current,chunks)
    assert p.identity(artifact)==order['accepted_artifact_identity_guard']
    hours=[h for r in receipt['records'] for h in r['hours']]
    receipt.update(status='staged_pending_independent_audit',finished_utc=p.utc(),hours_staged=len(hours),actual_minutes_staged=len(hours)*60,
        genuine_zero_trade_quote_minutes=sum(h['genuine_zero_trade_quote_minutes'] for h in hours),
        maximum_one_stock_numeric_trade_rows=max_resident_rows,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    assert len(hours)+sum(counts.values())==receipt['requested_hours']
    p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':main()
