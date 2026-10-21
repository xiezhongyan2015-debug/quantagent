"""Replay one declared cumulative-volume quote-cut model, without moving M1 clocks.

Each stock's six numeric trade columns are resident separately. Canonical bars
remain actual first/max/min/last trades; coarse cuts are only source evidence.
"""
from collections import Counter, defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import GRID, labels
from .stage_sep22_quote_witness_minutes_bounded import FIELDS, clock
from .diagnose_tencent_nominal_quote_cuts_bounded import RAW, END, WHOLE

DIAGNOSTIC = p.OUT/'tencent_nominal_quote_cut_diagnostic/receipt.json'
DEST = p.OUT/'nominal_quote_cut_reconciled_v18_staging'


def main():
    lock = p.locked('stage_nominal_quote_cut_reconciled_v18')
    DEST.mkdir(exist_ok=True);output = DEST/'receipt.json';assert not output.exists()
    diagnostic = p.json.loads(DIAGNOSTIC.read_text())
    assert diagnostic['status'] == 'complete_nominal_endpoint_diagnostic_no_admission'
    audit_path = RAW/'raw_full_history_audit.json';audit = p.json.loads(audit_path.read_text())
    assert audit['passed'] and p.sha(audit_path) == diagnostic['parent_raw_audit_sha256']
    assert audit['audited_raw_meta_sha256'] == p.sha(RAW/'dataset/meta.json')
    mask_identity = next(a['verified_identity'] for a in audit['arrays'] if a['name'] == 'hour_complete.npy')
    assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    mask = p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    calendar = p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    wanted = defaultdict(lambda:defaultdict(list))
    for h in diagnostic['records']:
        if h['status'] != 'actual_nominal_quote_quantity_and_close_exact':continue
        assert not mask[calendar['dates'].index(h['date'])*4+h['slot'],calendar['union_symbols'].index(h['symbol'])]
        wanted[h['date']][h['symbol']].append(h)
    refs = {};reference_pins = []
    acquisition_path = p.OUT/'tencent_minute_references/all_target_stock_acquisition.json'
    comparison_path = acquisition_path.parent/'all_tail_hour_comparison.json'
    comparison = p.json.loads(comparison_path.read_text())
    assert p.sha(acquisition_path) == comparison['source_acquisition_sha256']
    assert p.sha(comparison_path) == diagnostic['coarse_receipt_sha256']
    for r in p.json.loads(acquisition_path.read_text())['records']:
        if not any(r['symbol'] in stocks for stocks in wanted.values()):continue
        path = p.Path(r['path']);assert p.identity(path) == r['verified_identity']
        data = path.read_bytes();assert p.hashlib.sha256(data).hexdigest() == r['sha256']
        for q in p.json.loads(data)['data'][r['code']]['m60']:
            t = q[0];date = f'{t[:4]}-{t[4:6]}-{t[6:8]}'
            if r['symbol'] not in wanted.get(date,{}):continue
            slot = ['1030','1130','1400','1500'].index(t[-4:])
            v = {f:int(D(q[i])*10000) for f,i in [('open',1),('close',2),('high',3),('low',4)]}
            shares = D(q[5])*100;assert shares == shares.to_integral_value();v['volume'] = int(shares)
            refs.setdefault((date,r['symbol']),{})[slot] = v
        assert p.identity(path) == r['verified_identity']
        reference_pins.append(dict(path=str(path),sha256=r['sha256'],verified_identity=r['verified_identity'],code=r['code'],symbol=r['symbol']))
    result = dict(status='staging_nominal_quote_cut_evidence',started_utc=p.utc(),source_code_sha256=p.sha(__file__),
        diagnostic_receipt_sha256=p.sha(DIAGNOSTIC),parent_raw_audit_sha256=p.sha(audit_path),
        requested_hours=sum(len(hs) for stocks in wanted.values() for hs in stocks.values()),requested_stockdays=sum(len(v) for v in wanted.values()),
        definition='One fixed coarse model: exact same-day cumulative Tencent hour volume identifies unique actual native whole-trade prefixes; The current cut requires an actual quote at the predeclared nominal endpoint, with exact native quantity, calculated cash and last price. The previous Tencent cumulative quantity must also identify a unique whole actual native prefix, and its last price must equal the previous Tencent close; an observed previous nominal quote is retained when present but is not invented when absent. Coarse Open is the previous cut last price (first actual daily trade for slot0), High/Low include it and all actual trades between the cuts, Close is the current cut last price, Volume is the exact cut difference. Canonical M1 clocks and actual first/max/min/last candles never change. All60 positive M1OHLCV match Sina; whole primary daily six fields match. Source event/reception offsets remain explicit.',
        canonical_minute_clock_changed=False,coarse_vendor_implementation_certified=False,source_live_clock_certified=False,
        candidate_hours_admitted=0,raw_panel_modified=False,production_ready=False,predecessor_exclusion_audit_sha256=p.sha(p.OUT/'nominal_quote_cut_minute_staging/independent_verification.json'),source_pins=[],tencent_reference_pins=reference_pins,
        records=[],diagnostics=[],excluded_hours=[],counts={})
    p.atomic_json(output,result);counts = Counter();maximum_rows = 0
    for date,stocks in sorted(wanted.items()):
        pair_path = p.OUT/('millisecond_pairing_sep22' if date == '2026-09-22' else 'millisecond_pairing')/'receipt.json'
        pair = p.json.loads(pair_path.read_text());order_path = pair_path.parent/'intrasecond_order_verification.json';order = p.json.loads(order_path.read_text())
        assert pair['all_rows_price_quantity_and_floor_clock_exact'] and pair['stockdays_checked'] == 624
        assert order['paired_source_receipt_sha256'] == p.sha(pair_path)
        assert order['intrasecond_event_inversions'] == order['minute_open_changed_by_intrasecond_order'] == order['minute_close_changed_by_intrasecond_order'] == 0
        artifact = p.Path(pair['native_field_artifact']['path']);assert p.identity(artifact) == order['accepted_artifact_identity_guard']
        pin = next(q for q in diagnostic['source_quote_pins'] if q['date'] == date)
        source_path = p.Path(pin['receipt_path']);proof_path = p.Path(pin['proof_path'])
        assert p.sha(source_path) == pin['receipt_sha256'] and p.sha(proof_path) == pin['proof_sha256']
        source = p.json.loads(source_path.read_text());proof = p.json.loads(proof_path.read_text());assert proof['passed']
        if date == '2026-09-22':
            assert proof['audited_receipt_sha256'] == p.sha(source_path)
            leaves = {r['symbol']:r for r in source['records'] if r['kind'] == 'snapshot'};kind = 'native_snapshot'
        else:
            assert proof['audited_full_union_trade_prefix_receipt_sha256'] == p.sha(source_path)
            leaves = {r['symbol']:r['quote_prefix_witnesses'] for r in source['records']};kind = 'independently_verified_quote_prefix_witness'
        result['source_pins'].append(dict(date=date,pairing_path=str(pair_path),pairing_sha256=p.sha(pair_path),order_path=str(order_path),order_sha256=p.sha(order_path),
            quote_receipt_path=str(source_path),quote_receipt_sha256=p.sha(source_path),quote_proof_path=str(proof_path),quote_proof_sha256=p.sha(proof_path),quote_source_kind=kind))

        def finish(ticker,chunks):
            nonlocal maximum_rows
            symbol = ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
            if symbol not in stocks:return
            a = {f:p.np.concatenate([c[f] for c in chunks]) for f in FIELDS};n = len(a['volume']);assert 0<n<=1000000;maximum_rows = max(maximum_rows,n)
            ix = p.np.lexsort((a['channel'],a['tran_id'],a['event_ms']));a = {f:v[ix] for f,v in a.items()}
            assert (a['volume']>0).all() and (a['price_x10000']>0).all()
            assert int(a['volume'].max())*int(a['price_x10000'].max())*n < 2**63
            qty = a['volume'].astype('int64');cash = qty*a['price_x10000'];cum_qty = p.np.cumsum(qty);cum_cash = p.np.cumsum(cash)
            ready = p.np.maximum(a['event_ms'],a['local_ms']);prefix_ready = p.np.maximum.accumulate(ready)
            valid,minutes = labels(a['event_ms']//1000,'floor_left');assert valid.all();unique,begins,sizes = p.np.unique(minutes,return_index=True,return_counts=True)
            bars = {}
            for m,begin,size in zip(unique,begins,sizes):
                start = int(begin);end = start+int(size);prices = a['price_x10000'][start:end]
                bars[int(m)] = dict(open=int(prices[0]),high=int(prices.max()),low=int(prices.min()),close=int(prices[-1]),volume=int(qty[start:end].sum()),
                    amount_x10000=int(cash[start:end].sum()),trades=int(size),first_ms=int(a['event_ms'][start]),last_ms=int(a['event_ms'][end-1]),
                    first_transaction_id=int(a['tran_id'][start]),last_transaction_id=int(a['tran_id'][end-1]),source_last_event_ms=int(a['event_ms'][end-1]),
                    source_dependency_ready_ms=int(ready[start:end].max()),source_kind='actual_native_millisecond_trades')
            primary_path = p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary = p.json.loads(primary_path.read_text())
            day = next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0] == date)
            actual_day = [int(a['price_x10000'][0]),int(a['price_x10000'].max()),int(a['price_x10000'].min()),int(a['price_x10000'][-1]),int(cum_qty[-1]),int(cum_cash[-1])]
            expected = [int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
            day_exact = day['tradestatus'] == '1' and actual_day == expected
            hs = stocks[symbol];sina_path = p.Path(hs[0]['sina_reference_path']);data = sina_path.read_bytes();sina_sha = p.hashlib.sha256(data).hexdigest();assert all(h['sina_reference_sha256'] == sina_sha for h in hs)
            text = data.decode();sina_rows,_ = p.json.JSONDecoder().raw_decode(text[text.index('['):]);sina = {q['day']:q for q in sina_rows}
            hour_refs = refs[date,symbol];assert set(hour_refs) == {0,1,2,3};qcuts = p.np.cumsum([hour_refs[s]['volume'] for s in range(4)]).tolist();cuts = {};running = 0
            for slot,qend in enumerate(qcuts):
                j = int(p.np.searchsorted(cum_qty,qend))
                if j<n and int(cum_qty[j]) == qend:
                    cuts[slot] = dict(source_prefix_trade_count=j+1,source_quote_quantity=qend,source_quote_amount_x10000=int(cum_cash[j]),source_quote_last_price_x10000=int(a['price_x10000'][j]),
                        source_prefix_last_event_ms=int(a['event_ms'][j]),source_prefix_last_local_ms=int(a['local_ms'][j]),source_prefix_last_transaction_id=int(a['tran_id'][j]),source_prefix_last_channel=int(a['channel'][j]),source_prefix_ready_ms=int(prefix_ready[j]))
            leaf = leaves[symbol];quote_path = p.Path(leaf['path']);before = p.identity(quote_path);vf = 'TradVolume' if symbol.endswith('.SH') else 'Volume'
            if kind == 'native_snapshot':
                assert before == leaf['verified_identity'];pf = p.pq.ParquetFile(quote_path);cols = ['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','Turnover',vf]
            else:
                data = quote_path.read_bytes();assert p.hashlib.sha256(data).hexdigest() == leaf['sha256'];pf = p.pq.ParquetFile(p.pa.BufferReader(data));assert pf.metadata.num_rows == leaf['rows'];cols = None
            quotes = {};chosen = {}
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=cols,use_threads=False):
                for q in batch.to_pylist():
                    if kind == 'native_snapshot':
                        if q['SecurityID'] != ticker:continue
                        t = clock(q['UpdateTime']);received = clock(q['LocalTime']);seq = str(q['SeqNo']);quantity = D(str(q[vf]));amount = D(str(q['Turnover']))*10000;price = D(str(q['LastPrice']))*10000
                    else:
                        if not (q['whole_trade_quantity_prefix_exact'] and q['calculated_trade_prefix_amount_exact'] and q['last_trade_price_exact']):continue
                        t = q['quote_reported_ms'];received = q['quote_received_ms'];seq = str(q['quote_seq_no']);quantity = D(q['quote_quantity']);amount = D(q['quote_Turnover'])*10000;price = D(q['quote_LastPrice'])*10000
                    if t not in END:continue
                    slot = END.index(t);cut = cuts.get(slot)
                    if cut is None or quantity != cut['source_quote_quantity'] or amount != cut['source_quote_amount_x10000'] or price != cut['source_quote_last_price_x10000']:continue
                    if kind != 'native_snapshot':
                        assert q['last_actual_trade_EventTime_ms'] == cut['source_prefix_last_event_ms']
                    key = t,received,seq
                    if slot not in chosen or key<chosen[slot]:
                        chosen[slot] = key;quotes[slot] = dict(**cut,source_quote_reported_ms=t,source_quote_received_ms=received,source_quote_sequence=seq,
                            source_dependency_ready_ms=max(received,cut['source_prefix_ready_ms']),source_last_event_offset_from_nominal_ms=cut['source_prefix_last_event_ms']-t)
            assert p.identity(quote_path) == before
            rows = [];hours = [];quarantined = []
            for request in sorted(hs,key=lambda h:h['slot']):
                slot = request['slot'];grid = GRID[slot*60:(slot+1)*60];reference = hour_refs[slot];reason = None;model = None
                assert reference == request['tencent_hour'] and qcuts[slot] == request['tencent_cumulative_volume_shares']
                if not day_exact:reason = 'primary_whole_day_six_conflict'
                elif any(m not in bars for m in grid):reason = 'canonical_positive_minute_absent'
                elif slot not in quotes:reason = 'current_nominal_quote_native_cash_or_prefix_conflict'
                elif slot>0 and slot-1 not in cuts:reason = 'previous_cumulative_quantity_not_a_whole_native_trade_cut'
                else:
                    end = cuts[slot]['source_prefix_trade_count'];start = cuts[slot-1]['source_prefix_trade_count'] if slot else 0
                    assert end>start
                    carry = int(a['price_x10000'][start-1]) if start else int(a['price_x10000'][0]);prices = a['price_x10000'][start:end]
                    model = dict(open=carry,high=max(carry,int(prices.max())),low=min(carry,int(prices.min())),close=int(prices[-1]),volume=int(qty[start:end].sum()))
                    if slot and carry != hour_refs[slot-1]['close']:reason = 'previous_tencent_close_native_cut_conflict'
                    elif model != reference:reason = 'fixed_nominal_quote_cut_ohlcv_conflict'
                if reason:
                    r = dict(date=date,symbol=symbol,slot=slot,reason=reason,coarse_model=model,coarse_reference=reference,current_quote=quotes.get(slot),previous_quote=quotes.get(slot-1));quarantined.append(r);result['excluded_hours'].append(r);counts[reason] += 1;continue
                b = [bars[m] for m in grid]
                for m in grid:
                    endminute = m+1;q = sina[f'{date} {endminute//60:02d}:{endminute%60:02d}:00'];v = bars[m]
                    assert all(v[f] == D(q[f])*10000 for f in ['open','high','low','close']) and D(v['volume']) == D(q['volume'])
                h = dict(slot=slot,date=date,open=b[0]['open'],high=max(v['high'] for v in b),low=min(v['low'] for v in b),close=b[-1]['close'],volume=sum(v['volume'] for v in b),
                    amount_x10000=sum(v['amount_x10000'] for v in b),source_event_ms_max=max(v['source_last_event_ms'] for v in b),source_ready_ms=max(v['source_dependency_ready_ms'] for v in b),actual_trade_minutes=60,genuine_zero_trade_quote_minutes=0,
                    coarse_reference_kind='fixed_nominal_quote_cut_reconciled',coarse_reference=reference,coarse_model=model,current_quote_cut=quotes[slot],previous_quote_cut=quotes.get(slot-1),previous_native_trade_cut=cuts.get(slot-1),
                    actual_cut_start_trade_count=start,actual_cut_end_trade_count=end,sina_reference_path=str(sina_path),sina_reference_sha256=sina_sha,
                    independent_positive_minute_ohlcv_exact=True,real_zero_trade_quote_witnesses_exact=True,coarse_definition_reconciliation_exact=True)
                hours.append(h);rows.extend(dict(symbol=symbol,date=date,left_minute=m,**bars[m]) for m in grid);counts['fixed_nominal_quote_cut_reconciled'] += 1
            result['diagnostics'].append(dict(date=date,symbol=symbol,actual_trade_rows=n,primary_daily_six_exact=day_exact,actual_exact_nominal_quote_cuts=quotes,quarantined_hours=quarantined))
            if hours:
                folder = DEST/('date='+date)/('symbol='+symbol);folder.mkdir(parents=True,exist_ok=True);minute_path = folder/'native_candidate_minutes.parquet'
                p.pq.write_table(p.pa.Table.from_pylist(rows),minute_path,compression='zstd')
                result['records'].append(dict(date=date,symbol=symbol,hours=hours,minute_path=str(minute_path),minute_sha256=p.sha(minute_path),verified_identity=p.identity(minute_path),
                    primary_source_path=str(primary_path),primary_source_sha256=p.sha(primary_path),stockday_native_primary_six_exact=True,quote_source_path=str(quote_path),quote_source_sha256=leaf['sha256'],quote_source_identity=before,quote_source_kind=kind))
            result.update(updated_utc=p.utc(),counts=dict(counts),stockdays_checked=len(result['diagnostics']),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)

        current = None;chunks = [];completed = set();resident = 0
        for batch in p.pq.ParquetFile(artifact).iter_batches(batch_size=p.BATCH_ROWS,use_threads=False):
            frame = batch.to_pandas()
            for ticker,part in frame.groupby('ticker',sort=False):
                if ticker != current:
                    if current is not None:finish(current,chunks);completed.add(current)
                    assert ticker not in completed;current = ticker;chunks = [];resident = 0
                symbol = ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
                if symbol not in stocks:continue
                resident += len(part);assert resident<=1000000;chunks.append({f:part[f].to_numpy(copy=True) for f in FIELDS})
        if current is not None:finish(current,chunks)
        assert p.identity(artifact) == order['accepted_artifact_identity_guard']
    hours = [h for r in result['records'] for h in r['hours']]
    assert len(hours)+len(result['excluded_hours']) == result['requested_hours']
    assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    result.update(status='staged_nominal_quote_cut_minutes_pending_independent_audit',finished_utc=p.utc(),hours_staged=len(hours),actual_minutes_staged=len(hours)*60,
        stockdays_staged=len(result['records']),maximum_one_stock_numeric_trade_rows=maximum_rows,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,result);lock.close()


if __name__ == '__main__':main()
