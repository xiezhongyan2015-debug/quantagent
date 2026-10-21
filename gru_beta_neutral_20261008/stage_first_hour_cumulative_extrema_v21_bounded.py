"""Replay one declared cumulative-volume quote-cut model, without moving M1 clocks.

Each stock's six numeric trade columns are resident separately. Canonical bars
remain actual first/max/min/last trades; coarse cuts are only source evidence.
"""
from collections import Counter, defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import GRID, labels
from .stage_sep22_quote_witness_minutes_bounded import FIELDS, clock
from .diagnose_tencent_first_hour_cumulative_extrema_bounded import RAW
from .diagnose_tencent_strict_endpoint_quotes_bounded import END, START
from .diagnose_tencent_sampled_quote_hours_bounded import WHOLE

DIAGNOSTIC = p.OUT/'tencent_first_hour_cumulative_extrema_diagnostic/receipt.json'
DEST = p.OUT/'first_hour_cumulative_extrema_v21_staging'


def main():
    lock = p.locked('stage_first_hour_cumulative_extrema_v21')
    DEST.mkdir(exist_ok=True);output = DEST/'receipt.json';assert not output.exists()
    diagnostic = p.json.loads(DIAGNOSTIC.read_text())
    assert diagnostic['status'] == 'complete_first_hour_cumulative_extrema_diagnostic_no_admission'
    audit_path = RAW/'raw_full_history_audit.json';audit = p.json.loads(audit_path.read_text())
    assert audit['passed'] and p.sha(audit_path) == diagnostic['parent_raw_audit_sha256']
    assert audit['audited_raw_meta_sha256'] == p.sha(RAW/'dataset/meta.json')
    mask_identity = next(a['verified_identity'] for a in audit['arrays'] if a['name'] == 'hour_complete.npy')
    assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    mask = p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    calendar = p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    wanted = defaultdict(lambda:defaultdict(list))
    for h in diagnostic['records']:
        if h['status'] != 'published_first_hour_cumulative_ohlcv_exact_pending_prefix_extrema':continue
        assert not mask[calendar['dates'].index(h['date'])*4+h['slot'],calendar['union_symbols'].index(h['symbol'])]
        wanted[h['date']][h['symbol']].append(h)
    refs = {};reference_pins = []
    acquisition_path = p.OUT/'tencent_minute_references/all_target_stock_acquisition.json'
    comparison_path = acquisition_path.parent/'all_tail_hour_comparison.json'
    comparison = p.json.loads(comparison_path.read_text())
    assert p.sha(acquisition_path) == comparison['source_acquisition_sha256']
    strict_path = p.OUT/'tencent_strict_endpoint_quote_diagnostic/receipt.json'
    assert p.sha(strict_path) == diagnostic['parent_diagnostic_sha256']
    strict = p.json.loads(strict_path.read_text())
    sampled_path = p.OUT/'tencent_sampled_quote_hour_diagnostic/receipt.json'
    assert p.sha(sampled_path) == strict['parent_diagnostic_sha256']
    sampled = p.json.loads(sampled_path.read_text())
    parent_diagnostic = p.json.loads((p.OUT/'tencent_nominal_quote_cut_diagnostic/receipt.json').read_text());assert p.sha(comparison_path) == parent_diagnostic['coarse_receipt_sha256']
    assert p.sha(p.OUT/'tencent_nominal_quote_cut_diagnostic/receipt.json') == sampled['parent_diagnostic_sha256']
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
        definition=diagnostic['definition']+' Before staging any actualM1, the fixed first-hour endpoint quote must exactly bind to a unique actual native whole-trade prefix quantity/calculated cash/lastprice and observed cumulativeHigh/Low must equal full actual prefix extrema; all60canonical positiveM1OHLCV must match Sina and the primary entire daily six fields must match. Canonical candles are unchanged actual first/max/min/last trades. Observed quote/extrema fields and real event/receive offsets stay separate; sampled quotes never replace true minute trade extrema.',
        canonical_minute_clock_changed=False,coarse_vendor_implementation_certified=False,source_live_clock_certified=False,
        candidate_hours_admitted=0,raw_panel_modified=False,production_ready=False,source_pins=[],tencent_reference_pins=reference_pins,
        records=[],diagnostics=[],excluded_hours=[],counts={})
    p.atomic_json(output,result);counts = Counter();maximum_rows = 0
    for date,stocks in sorted(wanted.items()):
        pair_path = p.OUT/('millisecond_pairing_sep22' if date == '2026-09-22' else 'millisecond_pairing')/'receipt.json'
        pair = p.json.loads(pair_path.read_text());order_path = pair_path.parent/'intrasecond_order_verification.json';order = p.json.loads(order_path.read_text())
        assert pair['all_rows_price_quantity_and_floor_clock_exact'] and pair['stockdays_checked'] == 624
        assert order['paired_source_receipt_sha256'] == p.sha(pair_path)
        assert order['intrasecond_event_inversions'] == order['minute_open_changed_by_intrasecond_order'] == order['minute_close_changed_by_intrasecond_order'] == 0
        artifact = p.Path(pair['native_field_artifact']['path']);assert p.identity(artifact) == order['accepted_artifact_identity_guard']
        pin = next(q for q in sampled['source_pins'] if q['date'] == date)
        source_path = p.Path(pin['receipt_path']);proof_path = p.Path(pin['proof_path'])
        assert p.sha(source_path) == pin['receipt_sha256'] and p.sha(proof_path) == pin['proof_sha256']
        source = p.json.loads(source_path.read_text());proof = p.json.loads(proof_path.read_text());assert proof['passed']
        assert proof['audited_receipt_sha256' if date == '2026-09-22' else 'native_member_acquisition_receipt_sha256'] == p.sha(source_path)
        leaves = {r['symbol']:r for r in source['records'] if r['kind'] == 'snapshot'};kind = 'native_snapshot'
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
            cum_high = p.np.maximum.accumulate(a['price_x10000']);cum_low = p.np.minimum.accumulate(a['price_x10000'])
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
            hour_refs = refs[date,symbol];assert set(hour_refs) == {0,1,2,3}
            leaf = leaves[symbol];quote_path = p.Path(leaf['path']);before = p.identity(quote_path);vf = 'TradVolume' if symbol.endswith('.SH') else 'Volume'
            if leaf.get('verified_identity'):
                assert before == leaf['verified_identity'];pf = p.pq.ParquetFile(quote_path)
            else:
                data = quote_path.read_bytes();assert p.hashlib.sha256(data).hexdigest() == leaf['sha256'];pf = p.pq.ParquetFile(p.pa.BufferReader(data))
            cols = ['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','OpenPrice','HighPrice','LowPrice','Turnover',vf];quotes = []
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=cols,use_threads=False):
                for q in batch.to_pylist():
                    if q['SecurityID'] != ticker:continue
                    quantity = D(str(q[vf]));amount = D(str(q['Turnover']))*10000;price = D(str(q['LastPrice']))*10000;opening = D(str(q['OpenPrice']))*10000
                    if quantity<=0 or price<=0:continue
                    high = D(str(q['HighPrice']))*10000;low = D(str(q['LowPrice']))*10000
                    assert all(v == v.to_integral_value() for v in [quantity,amount,price,opening,high,low])
                    t = clock(q['UpdateTime']);received = clock(q['LocalTime']);sequence = str(q['SeqNo']);i = int(p.np.searchsorted(cum_qty,int(quantity)))
                    valid_prefix = i<n and int(cum_qty[i]) == quantity and int(cum_cash[i]) == amount and int(a['price_x10000'][i]) == price
                    witness = dict(reported_ms=t,received_ms=received,sequence=sequence,price_x10000=int(price),open_price_x10000=int(opening),quantity=int(quantity),amount_x10000=int(amount),high_price_x10000=int(high),low_price_x10000=int(low),actual_whole_prefix_exact=bool(valid_prefix),actual_prefix_extrema_exact=bool(valid_prefix and int(cum_high[i])==high and int(cum_low[i])==low))
                    if valid_prefix:
                        witness.update(source_prefix_trade_count=i+1,source_prefix_last_event_ms=int(a['event_ms'][i]),source_prefix_last_local_ms=int(a['local_ms'][i]),source_prefix_last_transaction_id=int(a['tran_id'][i]),source_prefix_last_channel=int(a['channel'][i]),source_prefix_ready_ms=int(prefix_ready[i]),source_dependency_ready_ms=max(received,int(prefix_ready[i])),source_last_event_offset_from_reported_ms=int(a['event_ms'][i])-t)
                    quotes.append(witness)
            assert p.identity(quote_path) == before and len(quotes)<=10000;quotes.sort(key=lambda q:(q['reported_ms'],q['received_ms'],q['sequence']))
            endpoints = {s:next((q for q in reversed(quotes) if q['reported_ms']<END[s]),None) for s in range(4)}
            comparable_fields = ['reported_ms','received_ms','sequence','price_x10000','open_price_x10000','quantity','amount_x10000','high_price_x10000','low_price_x10000']
            rows = [];hours = [];quarantined = []
            for request in sorted(hs,key=lambda h:h['slot']):
                slot = request['slot'];grid = GRID[slot*60:(slot+1)*60];reference = hour_refs[slot];reason = None;model = None
                end = endpoints[slot];previous = endpoints.get(slot-1)
                assert slot == 0 and previous is None
                samples = [end] if end else []
                opening = end
                assert reference == request['tencent_hour']
                assert ({f:end[f] for f in comparable_fields} if end else None) == request['current_quote']
                assert len(samples) == 1
                dependencies = samples+[q for q in [end,previous,opening] if q]
                if not day_exact:reason = 'primary_whole_day_six_conflict'
                elif any(m not in bars for m in grid):reason = 'canonical_positive_minute_absent'
                elif not end or not opening or not samples or (slot and not previous):reason = 'source_quote_dependencies_absent'
                elif any(not q['actual_whole_prefix_exact'] for q in dependencies):reason = 'sampled_quote_native_prefix_cash_quantity_or_price_conflict'
                elif not end['actual_prefix_extrema_exact']:reason = 'source_first_hour_cumulative_extrema_actual_prefix_conflict'
                elif slot==0 and opening['open_price_x10000'] != actual_day[0]:reason = 'source_quote_daily_open_actual_first_price_conflict'
                else:
                    op = end['open_price_x10000'] if slot==0 else opening['price_x10000'];prices = [op]+[q['price_x10000'] for q in samples]
                    model = dict(open=op,high=end['high_price_x10000'],low=end['low_price_x10000'],close=end['price_x10000'],volume=end['quantity'])
                    if model != reference:reason = 'fixed_sampled_quote_ohlcv_conflict'
                if reason:
                    r = dict(date=date,symbol=symbol,slot=slot,reason=reason,coarse_model=model,coarse_reference=reference);quarantined.append(r);result['excluded_hours'].append(r);counts[reason] += 1;continue
                b = [bars[m] for m in grid]
                for m in grid:
                    endminute = m+1;q = sina[f'{date} {endminute//60:02d}:{endminute%60:02d}:00'];v = bars[m]
                    assert all(v[f] == D(q[f])*10000 for f in ['open','high','low','close']) and D(v['volume']) == D(q['volume'])
                h = dict(slot=slot,date=date,open=b[0]['open'],high=max(v['high'] for v in b),low=min(v['low'] for v in b),close=b[-1]['close'],volume=sum(v['volume'] for v in b),
                    amount_x10000=sum(v['amount_x10000'] for v in b),source_event_ms_max=max(v['source_last_event_ms'] for v in b),source_ready_ms=max(v['source_dependency_ready_ms'] for v in b),actual_trade_minutes=60,genuine_zero_trade_quote_minutes=0,
                    coarse_reference_kind='fixed_first_hour_cumulative_extrema_reconciled',coarse_reference=reference,coarse_model=model,current_quote_cut=end,previous_quote_cut=previous,opening_quote=opening,
                    sampled_quote_count=len(samples),all_sampled_quote_whole_prefixes_exact=True,coarse_cumulative_prefix_extrema_exact=True,coarse_quote_dependency_ready_ms=max(q['source_dependency_ready_ms'] for q in dependencies),
                    observed_quote_price_extrema_do_not_replace_actual_minute_extrema=True,sina_reference_path=str(sina_path),sina_reference_sha256=sina_sha,
                    independent_positive_minute_ohlcv_exact=True,real_zero_trade_quote_witnesses_exact=True,coarse_definition_reconciliation_exact=True)
                hours.append(h);rows.extend(dict(symbol=symbol,date=date,left_minute=m,**bars[m]) for m in grid);counts['fixed_first_hour_cumulative_extrema_reconciled'] += 1
            result['diagnostics'].append(dict(date=date,symbol=symbol,actual_trade_rows=n,primary_daily_six_exact=day_exact,positive_quote_rows=len(quotes),native_prefix_exact_positive_quote_rows=sum(q['actual_whole_prefix_exact'] for q in quotes),quarantined_hours=quarantined))
            if hours:
                folder = DEST/('date='+date)/('symbol='+symbol);folder.mkdir(parents=True,exist_ok=True);minute_path = folder/'native_candidate_minutes.parquet'
                p.pq.write_table(p.pa.Table.from_pylist(rows),minute_path,compression='zstd')
                result['records'].append(dict(date=date,symbol=symbol,hours=hours,minute_path=str(minute_path),minute_sha256=p.sha(minute_path),verified_identity=p.identity(minute_path),
                    primary_source_path=str(primary_path),primary_source_sha256=p.sha(primary_path),stockday_native_primary_six_exact=True,quote_source_path=str(quote_path),quote_source_sha256=leaf['sha256'],quote_source_identity=before,quote_source_kind=kind))
            result.update(updated_utc=p.utc(),counts=dict(counts),stockdays_checked=len(result['diagnostics']),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)

        pf = p.pq.ParquetFile(artifact);ticker_column = pf.schema_arrow.names.index('ticker')
        rowgroups = {symbol[:6]:[] for symbol in stocks}
        for group in range(pf.metadata.num_row_groups):
            stats = pf.metadata.row_group(group).column(ticker_column).statistics
            assert stats and stats.has_min_max
            low,high = stats.min,stats.max
            if isinstance(low,bytes):low=low.decode();high=high.decode()
            for ticker in rowgroups:
                if low<=ticker<=high:rowgroups[ticker].append(group)
        useful_rows = 0
        for ticker,selected in sorted(rowgroups.items()):
            assert selected;chunks=[];resident=0
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,row_groups=selected,use_threads=False):
                frame=batch.to_pandas();part=frame.loc[frame.ticker.eq(ticker)]
                if part.empty:continue
                resident+=len(part);assert resident<=1000000
                chunks.append({f:part[f].to_numpy(copy=True) for f in FIELDS})
            useful_rows+=resident;finish(ticker,chunks)
        result.setdefault('useful_native_rowgroup_reads',[]).append(dict(date=date,all_source_rowgroups=pf.metadata.num_row_groups,selected_source_rowgroups=len({g for groups in rowgroups.values() for g in groups}),selected_stock_trade_rows=useful_rows,whole_artifact_fingerprint_repeated=False))
        assert p.identity(artifact) == order['accepted_artifact_identity_guard']
    hours = [h for r in result['records'] for h in r['hours']]
    assert len(hours)+len(result['excluded_hours']) == result['requested_hours']
    assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    result.update(status='staged_first_hour_cumulative_extrema_minutes_pending_independent_audit',finished_utc=p.utc(),hours_staged=len(hours),actual_minutes_staged=len(hours)*60,
        stockdays_staged=len(result['records']),maximum_one_stock_numeric_trade_rows=maximum_rows,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,result);lock.close()


if __name__ == '__main__':main()
