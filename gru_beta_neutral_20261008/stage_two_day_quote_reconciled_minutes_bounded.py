"""Stage actual two-day minutes and real quote witnesses with bounded memory.

One stock's compact numeric trades and one quote batch are resident. Completed
native source fingerprints are reused behind their accepted identity guards.
Sep23's independently verified small quote-prefix witnesses are read directly;
Sep22 snapshots are decoded in batches. Nothing here admits a market hour.
"""
from collections import Counter, defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import GRID, labels
from .stage_sep22_quote_witness_minutes_bounded import clock, FIELDS

RAW = p.BASE/'backfilled_panel_candidate_native_expanded_v16_raw'
DEST = p.OUT/'two_day_quote_reconciled_v17_staging'
WHOLE = p.BASE/'public_source_probes/additional_native_inventory_20261009'
DIAGNOSTIC = p.OUT/'remaining_two_day_quote_opportunities_v16/receipt.json'


def main():
    lock = p.locked('stage_two_day_quote_reconciled')
    DEST.mkdir(exist_ok=True);output = DEST/'receipt.json';assert not output.exists()
    diagnostic = p.json.loads(DIAGNOSTIC.read_text())
    assert diagnostic['status'] == 'complete_diagnostic_only_no_admission' and diagnostic['candidate_hours_admitted'] == 0
    raw_audit_path = RAW/'raw_full_history_audit.json';raw_audit = p.json.loads(raw_audit_path.read_text())
    assert raw_audit['passed'] and p.sha(raw_audit_path) == diagnostic['parent_raw_audit_sha256']
    assert raw_audit['audited_raw_meta_sha256'] == p.sha(RAW/'dataset/meta.json')
    mask_identity = next(a['verified_identity'] for a in raw_audit['arrays'] if a['name'] == 'hour_complete.npy')
    assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    mask = p.np.load(RAW/'dataset/hour_complete.npy', mmap_mode='r')
    calendar = p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    wanted = defaultdict(lambda:defaultdict(list));seen = set()
    for h in diagnostic['records']:
        key = h['date'], h['symbol'], h['slot'];assert key not in seen;seen.add(key)
        assert not mask[calendar['dates'].index(h['date'])*4+h['slot'], calendar['union_symbols'].index(h['symbol'])]
        assert h['native_absent_minutes'] and not h['conflicting_independent_actual_minutes'] and not h['missing_independent_actual_minutes']
        wanted[h['date']][h['symbol']].append(h)
    result = dict(status='staging_real_quote_witnesses', started_utc=p.utc(), source_code_sha256=p.sha(__file__),
        numeric_prefix_helper_code_sha256=p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_sep22_quote_witness_minutes_bounded.py'),
        diagnostic_receipt_sha256=p.sha(DIAGNOSTIC), parent_raw_audit_sha256=p.sha(raw_audit_path),
        requested_hours=len(seen), requested_stockdays=sum(len(v) for v in wanted.values()), source_pins=[], records=[], diagnostics=[], excluded_hours=[],
        definition='Unchanged actual chronological millisecond trade first/max/min/last candles and fixed floor-left phase2s grace. A real zero-trade minute needs an observed positive quote within that minute whose cumulative quantity, calculated cash and last price exactly equal an actual whole native trade prefix. Every positive minute matches independent SinaOHLCV. Coarsehour5 must directly match or obey the fixed preceding-observed-close formula with that close independently seen. Whole-day primary six fields remain exact.',
        candidate_hours_admitted=0, raw_panel_modified=False, missing_minutes_imputed=False, source_live_clock_certified=False, production_ready=False)
    counts = Counter();maximum_rows = 0;p.atomic_json(output, result)
    for date in sorted(wanted):
        pair_path = p.OUT/('millisecond_pairing_sep22' if date == '2026-09-22' else 'millisecond_pairing')/'receipt.json'
        pair = p.json.loads(pair_path.read_text());order_path = pair_path.parent/'intrasecond_order_verification.json';order = p.json.loads(order_path.read_text())
        assert pair['all_rows_price_quantity_and_floor_clock_exact'] and pair['stockdays_checked'] == 624
        assert order['paired_source_receipt_sha256'] == p.sha(pair_path)
        assert order['intrasecond_event_inversions'] == order['minute_open_changed_by_intrasecond_order'] == order['minute_close_changed_by_intrasecond_order'] == 0
        artifact = p.Path(pair['native_field_artifact']['path']);assert p.identity(artifact) == order['accepted_artifact_identity_guard']
        if date == '2026-09-22':
            source_path = WHOLE/'whole_event_archive_sep22/acquisition_and_extraction.json';proof_path = source_path.parent/'independent_member_verification.json'
            source = p.json.loads(source_path.read_text());proof = p.json.loads(proof_path.read_text())
            assert proof['passed'] and proof['audited_receipt_sha256'] == p.sha(source_path)
            quote_sources = {r['symbol']:r for r in source['records'] if r['kind'] == 'snapshot'}
            quote_kind = 'native_snapshot'
        else:
            source_path = WHOLE/'whole_event_archive/full_union_trade_prefix_audit/full_union_native_trade_prefix_diagnostics.json'
            proof_path = source_path.parent/'independent_full_union_trade_prefix_verification.json'
            source = p.json.loads(source_path.read_text());proof = p.json.loads(proof_path.read_text())
            assert proof['passed'] and proof['audited_full_union_trade_prefix_receipt_sha256'] == p.sha(source_path)
            quote_sources = {r['symbol']:r['quote_prefix_witnesses'] for r in source['records']}
            quote_kind = 'independently_verified_quote_prefix_witness'
        result['source_pins'].append(dict(date=date, pairing_path=str(pair_path), pairing_sha256=p.sha(pair_path), order_path=str(order_path), order_sha256=p.sha(order_path),
            quote_receipt_path=str(source_path), quote_receipt_sha256=p.sha(source_path), quote_proof_path=str(proof_path), quote_proof_sha256=p.sha(proof_path), quote_source_kind=quote_kind))

        def finish(ticker, chunks):
            nonlocal maximum_rows
            symbol = ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
            if symbol not in wanted[date]:return
            a = {f:p.np.concatenate([c[f] for c in chunks]) for f in FIELDS}
            n = len(a['volume']);assert 0<n<=1000000;maximum_rows = max(maximum_rows, n)
            ix = p.np.lexsort((a['channel'], a['tran_id'], a['event_ms']));a = {f:v[ix] for f,v in a.items()}
            assert (a['volume']>0).all() and (a['price_x10000']>0).all()
            assert int(a['volume'].max())*int(a['price_x10000'].max())*n < 2**63
            qty = a['volume'].astype('int64');cash = qty*a['price_x10000'];cum_qty = p.np.cumsum(qty);cum_cash = p.np.cumsum(cash)
            ready = p.np.maximum(a['event_ms'], a['local_ms']);prefix_ready = p.np.maximum.accumulate(ready)
            valid, minutes = labels(a['event_ms']//1000, 'floor_left');assert valid.all()
            unique, begins, sizes = p.np.unique(minutes, return_index=True, return_counts=True);bars = {}
            for minute, begin, size in zip(unique, begins, sizes):
                start = int(begin);end = start+int(size);prices = a['price_x10000'][start:end]
                bars[int(minute)] = dict(open=int(prices[0]), high=int(prices.max()), low=int(prices.min()), close=int(prices[-1]), volume=int(qty[start:end].sum()),
                    amount_x10000=int(cash[start:end].sum()), trades=int(size), first_ms=int(a['event_ms'][start]), last_ms=int(a['event_ms'][end-1]),
                    first_transaction_id=int(a['tran_id'][start]), last_transaction_id=int(a['tran_id'][end-1]), source_last_event_ms=int(a['event_ms'][end-1]),
                    source_dependency_ready_ms=int(ready[start:end].max()), source_kind='actual_native_millisecond_trades')
            primary_path = p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary = p.json.loads(primary_path.read_text())
            daily = next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0] == date)
            actual_day = [int(a['price_x10000'][0]), int(a['price_x10000'].max()), int(a['price_x10000'].min()), int(a['price_x10000'][-1]), int(cum_qty[-1]), int(cum_cash[-1])]
            expected_day = [int(D(daily[f])*10000) for f in ['open','high','low','close']]+[int(daily['volume']), int(D(daily['amount'])*10000)]
            day_exact = daily['tradestatus'] == '1' and actual_day == expected_day
            hs = wanted[date][symbol];qpath = p.Path(hs[0]['sina_reference_path']);data = qpath.read_bytes();qdigest = p.hashlib.sha256(data).hexdigest();assert all(h['sina_reference_sha256'] == qdigest for h in hs)
            text = data.decode();qrows,_ = p.json.JSONDecoder().raw_decode(text[text.index('['):]);sina = {q['day']:q for q in qrows}
            missing = {};running_qty = running_cash = 0
            for minute in GRID:
                if minute in bars:running_qty += bars[minute]['volume'];running_cash += bars[minute]['amount_x10000']
                else:missing[minute] = running_qty, running_cash
            leaf = quote_sources[symbol];quote_path = p.Path(leaf['path']);before = p.identity(quote_path)
            if quote_kind == 'native_snapshot':assert before == leaf['verified_identity']
            vf = 'TradVolume' if symbol.endswith('.SH') else 'Volume'
            cols = ['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','Turnover',vf] if quote_kind == 'native_snapshot' else None
            if quote_kind != 'native_snapshot':
                raw = quote_path.read_bytes();assert p.hashlib.sha256(raw).hexdigest() == leaf['sha256']
                pf = p.pq.ParquetFile(p.pa.BufferReader(raw));assert pf.metadata.num_rows == leaf['rows']
            else:pf = p.pq.ParquetFile(quote_path)
            quotes = {};stats = Counter()
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS, columns=cols, use_threads=False):
                for q in batch.to_pylist():
                    if quote_kind == 'native_snapshot':
                        if q['SecurityID'] != ticker:continue
                        t = clock(q['UpdateTime']);received = clock(q['LocalTime']);sequence = str(q['SeqNo'])
                        quantity = D(str(q[vf]));amount = D(str(q['Turnover']))*10000;price = D(str(q['LastPrice']))*10000
                    else:
                        if not (q['whole_trade_quantity_prefix_exact'] and q['calculated_trade_prefix_amount_exact'] and q['last_trade_price_exact']):continue
                        t = q['quote_reported_ms'];received = q['quote_received_ms'];sequence = str(q['quote_seq_no'])
                        quantity = D(q['quote_quantity']);amount = D(q['quote_Turnover'])*10000;price = D(q['quote_LastPrice'])*10000
                    minute = t//60000
                    if minute not in missing:continue
                    stats['same_empty_interval_quote_rows'] += 1
                    if quantity<=0 or quantity != quantity.to_integral_value():continue
                    i = int(p.np.searchsorted(cum_qty, int(quantity)))
                    if i == n or int(cum_qty[i]) != quantity:continue
                    stats['actual_whole_trade_quantity_cut_exact'] += 1
                    if int(cum_cash[i]) != amount or int(a['price_x10000'][i]) != price:continue
                    if missing[minute] != (int(quantity), int(cum_cash[i])) or int(a['event_ms'][i]) >= (minute+1)*60000:continue
                    stats['quantity_cash_last_price_exact'] += 1
                    key = t, received, sequence
                    witness = dict(source_quote_reported_ms=t, source_quote_received_ms=received, source_quote_sequence=sequence,
                        source_quote_quantity=int(quantity), source_quote_amount_x10000=int(cum_cash[i]), source_quote_last_price_x10000=int(price), source_prefix_trade_count=i+1,
                        source_prefix_last_event_ms=int(a['event_ms'][i]), source_prefix_last_local_ms=int(a['local_ms'][i]), source_prefix_last_transaction_id=int(a['tran_id'][i]),
                        source_prefix_last_channel=int(a['channel'][i]), source_prefix_ready_ms=int(prefix_ready[i]), source_dependency_ready_ms=max(received,int(prefix_ready[i])))
                    if minute not in quotes or key < quotes[minute][0]:quotes[minute] = key, witness
            assert p.identity(quote_path) == before
            native = dict(bars)
            for minute, (_, witness) in quotes.items():
                price = witness['source_quote_last_price_x10000']
                bars[minute] = dict(open=price, high=price, low=price, close=price, volume=0, amount_x10000=0, trades=0,
                    source_kind='genuine_quote_in_verified_zero_trade_interval', source_last_event_ms=witness['source_prefix_last_event_ms'], **witness)
            rows = [];hours = [];quarantined = []
            for request in sorted(hs,key=lambda h:h['slot']):
                slot = request['slot'];grid = GRID[slot*60:(slot+1)*60];absent = [m for m in grid if m not in bars]
                reason = 'primary_whole_day_six_conflict' if not day_exact else 'missing_genuine_quote' if absent else None
                if reason:
                    excluded = dict(date=date, symbol=symbol, slot=slot, reason=reason, missing_minutes=absent);result['excluded_hours'].append(excluded);quarantined.append(excluded);counts[reason] += 1;continue
                b = [bars[m] for m in grid]
                for m in grid:
                    if m not in native:continue
                    end = m+1;q = sina[f'{date} {end//60:02d}:{end%60:02d}:00'];v = native[m]
                    assert all(v[f] == D(q[f])*10000 for f in ['open','high','low','close']) and D(v['volume']) == D(q['volume'])
                h = dict(slot=slot, date=date, open=b[0]['open'], high=max(v['high'] for v in b), low=min(v['low'] for v in b), close=b[-1]['close'], volume=sum(v['volume'] for v in b),
                    amount_x10000=sum(v['amount_x10000'] for v in b), source_event_ms_max=max(v['source_last_event_ms'] for v in b), source_ready_ms=max(v['source_dependency_ready_ms'] for v in b),
                    actual_trade_minutes=sum(v['trades']>0 for v in b), genuine_zero_trade_quote_minutes=sum(v['trades']==0 for v in b))
                previous = max((m for m in native if m<grid[0]),default=None);carry = native[previous]['close'] if previous is not None else h['open']
                assert previous == request['previous_observed_minute'] and carry == request['previous_observed_close']
                values = {f:h[f] for f in ['open','high','low','close','volume']};formula = dict(open=carry,high=max(carry,h['high']),low=min(carry,h['low']),close=h['close'],volume=h['volume'])
                kind = 'direct_coarse_ohlcv_exact' if values == request['coarse_reference'] else 'fixed_previous_close_coarse_reconciled' if request['previous_close_independent_exact'] and formula == request['coarse_reference'] else None
                if kind is None:
                    excluded = dict(date=date,symbol=symbol,slot=slot,reason='coarse_conflict_after_real_quote',missing_minutes=[]);result['excluded_hours'].append(excluded);quarantined.append(excluded);counts[excluded['reason']] += 1;continue
                h.update(coarse_reference_kind=kind, coarse_reference=request['coarse_reference'], previous_observed_minute=previous,previous_observed_close=carry, previous_close_independent_exact=request['previous_close_independent_exact'],
                    sina_reference_path=str(qpath),sina_reference_sha256=qdigest,independent_positive_minute_ohlcv_exact=True,real_zero_trade_quote_witnesses_exact=True,coarse_definition_reconciliation_exact=True)
                hours.append(h);rows.extend(dict(symbol=symbol,date=date,left_minute=m,**bars[m]) for m in grid);counts[kind] += 1
            result['diagnostics'].append(dict(date=date,symbol=symbol,actual_trade_rows=n,original_empty_minutes=len(missing),verified_genuine_quote_minutes=len(quotes),quote_stats=dict(stats),quarantined_hours=quarantined))
            if hours:
                folder=DEST/('date='+date)/('symbol='+symbol);folder.mkdir(parents=True,exist_ok=True);minute_path=folder/'native_candidate_minutes.parquet'
                keys=set().union(*(r.keys() for r in rows));p.pq.write_table(p.pa.Table.from_pylist([{k:r.get(k) for k in keys} for r in rows]),minute_path,compression='zstd')
                result['records'].append(dict(date=date,symbol=symbol,hours=hours,minute_path=str(minute_path),minute_sha256=p.sha(minute_path),verified_identity=p.identity(minute_path),
                    primary_source_path=str(primary_path),primary_source_sha256=p.sha(primary_path),stockday_native_primary_six_exact=True,quote_source_path=str(quote_path),quote_source_sha256=leaf['sha256'],quote_source_identity=before,quote_source_kind=quote_kind))
            result.update(updated_utc=p.utc(),counts=dict(counts),stockdays_checked=len(result['diagnostics']));p.atomic_json(output,result)

        current=None;chunks=[];completed=set();resident=0
        for batch in p.pq.ParquetFile(artifact).iter_batches(batch_size=p.BATCH_ROWS,use_threads=False):
            frame=batch.to_pandas()
            for ticker,part in frame.groupby('ticker',sort=False):
                if ticker != current:
                    if current is not None:finish(current,chunks);completed.add(current)
                    assert ticker not in completed;current=ticker;chunks=[];resident=0
                symbol=ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
                if symbol not in wanted[date]:continue
                resident += len(part);assert resident<=1000000;chunks.append({f:part[f].to_numpy(copy=True) for f in FIELDS})
        if current is not None:finish(current,chunks)
        assert p.identity(artifact) == order['accepted_artifact_identity_guard']
    hours=[h for r in result['records'] for h in r['hours']]
    assert len(hours)+len(result['excluded_hours']) == result['requested_hours']
    assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    result.update(status='staged_real_quote_minutes_pending_independent_audit',finished_utc=p.utc(),hours_staged=len(hours),actual_minutes_staged=len(hours)*60,
        stockdays_staged=len(result['records']),genuine_zero_trade_quote_minutes=sum(h['genuine_zero_trade_quote_minutes'] for h in hours),
        maximum_one_stock_numeric_trade_rows=maximum_rows,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,result);lock.close()


if __name__ == '__main__':main()
