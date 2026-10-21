"""Independent SQL replay of all629 rejected nominal-cut candidates.

Only one stock is materialized in bounded SQL memory. The canonical minute
aggregation and quantity-defined cuts are computed independently of NumPy.
"""
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_first_observed_quote_open_v23_bounded import DEST, RAW, DIAGNOSTIC
import duckdb

END = [37800000,41400000,50400000,54000000]
START = [33900000,37800000,46800000,50400000]


def clock(value):
    h,m,s = str(value).split(':');return int(h)*3600000+int(m)*60000+int(D(s)*1000)


def main():
    lock = p.locked('verify_first_observed_quote_open_v23')
    path = DEST/'receipt.json';stage = p.json.loads(path.read_text());output = DEST/'independent_verification.json';assert not output.exists()
    assert stage['status'] == 'staged_first_observed_quote_open_minutes_pending_independent_audit'
    assert stage['source_code_sha256'] == p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_first_observed_quote_open_v23_bounded.py')
    assert stage['hours_staged'] > 0 and stage['actual_minutes_staged'] == stage['hours_staged']*60 and not stage['raw_panel_modified']
    assert p.sha(DIAGNOSTIC) == stage['diagnostic_receipt_sha256'];diagnostic = p.json.loads(DIAGNOSTIC.read_text())
    requests = {(h['date'],h['symbol'],h['slot']):h for h in diagnostic['records'] if h['status'] == 'fixed_first_observed_quote_ohlcv_exact'}
    excluded = {(h['date'],h['symbol'],h['slot']):h for h in stage['excluded_hours']}
    accepted = {(r['date'],r['symbol'],h['slot']):h for r in stage['records'] for h in r['hours']}
    source_records = {(r['date'],r['symbol']):r for r in stage['records']}
    assert set(accepted).isdisjoint(excluded) and set(requests) == set(accepted)|set(excluded) and len(requests) == stage['requested_hours']
    audit_path = RAW/'raw_full_history_audit.json';audit = p.json.loads(audit_path.read_text());assert audit['passed'] and p.sha(audit_path) == stage['parent_raw_audit_sha256']
    assert audit['audited_raw_meta_sha256'] == p.sha(RAW/'dataset/meta.json')
    mask_identity = next(a['verified_identity'] for a in audit['arrays'] if a['name'] == 'hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    mask = p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal = p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    refs = {}
    for r in stage['tencent_reference_pins']:
        qpath = p.Path(r['path']);assert p.identity(qpath) == r['verified_identity'];data = qpath.read_bytes();assert p.hashlib.sha256(data).hexdigest() == r['sha256']
        for q in p.json.loads(data)['data'][r['code']]['m60']:
            t = q[0];date = f'{t[:4]}-{t[4:6]}-{t[6:8]}'
            if not any(k[0] == date and k[1] == r['symbol'] for k in requests):continue
            slot = ['1030','1130','1400','1500'].index(t[-4:]);v = {f:int(D(q[i])*10000) for f,i in [('open',1),('close',2),('high',3),('low',4)]}
            shares = D(q[5])*100;assert shares == shares.to_integral_value();v['volume'] = int(shares);refs.setdefault((date,r['symbol']),{})[slot] = v
        assert p.identity(qpath) == r['verified_identity']
    diagnostics = {(r['date'],r['symbol']):r for r in stage['diagnostics']};assert len(diagnostics) == stage['stockdays_checked']
    spill = DEST/'independent_sql_spill';spill.mkdir(exist_ok=True);con = duckdb.connect();con.execute("SET memory_limit='192MB'");con.execute('SET threads=1')
    con.execute('SET temp_directory=?',[str(spill)]);con.execute("SET max_temp_directory_size='2GB'")
    counts = Counter();extra = Counter();verified = [];verified_accepted = [];status_path = DEST/'independent_replay_progress.json'
    for pin in stage['source_pins']:
        date = pin['date'];pair_path = p.Path(pin['pairing_path']);order_path = p.Path(pin['order_path'])
        assert p.sha(pair_path) == pin['pairing_sha256'] and p.sha(order_path) == pin['order_sha256'];pair = p.json.loads(pair_path.read_text());order = p.json.loads(order_path.read_text())
        assert pair['all_rows_price_quantity_and_floor_clock_exact'] and pair['stockdays_checked'] == 624 and order['paired_source_receipt_sha256'] == p.sha(pair_path)
        assert order['intrasecond_event_inversions'] == order['minute_open_changed_by_intrasecond_order'] == order['minute_close_changed_by_intrasecond_order'] == 0
        artifact = p.Path(pair['native_field_artifact']['path']);assert p.identity(artifact) == order['accepted_artifact_identity_guard']
        source_path = p.Path(pin['quote_receipt_path']);proof_path = p.Path(pin['quote_proof_path']);assert p.sha(source_path) == pin['quote_receipt_sha256'] and p.sha(proof_path) == pin['quote_proof_sha256']
        source = p.json.loads(source_path.read_text());proof = p.json.loads(proof_path.read_text());assert proof['passed'];kind = pin['quote_source_kind']
        assert kind == 'native_snapshot'
        assert proof['audited_receipt_sha256' if date == '2026-09-22' else 'native_member_acquisition_receipt_sha256'] == p.sha(source_path)
        leaves = {r['symbol']:r for r in source['records'] if r['kind'] == 'snapshot'}
        con.read_parquet(str(artifact)).create_view('all_native',replace=True)
        for _,symbol in sorted(k for k in diagnostics if k[0] == date):
            ticker = symbol[:6];sd = diagnostics[date,symbol];hours = refs[date,symbol];assert set(hours) == {0,1,2,3}
            cumulative = [];running = 0
            for slot in range(4):running += hours[slot]['volume'];cumulative.append(running)
            con.execute('CREATE OR REPLACE TEMP TABLE native_stock AS SELECT *,ROW_NUMBER() OVER w AS trade_count,SUM(volume) OVER w AS prefix_quantity,SUM(price_x10000::HUGEINT*volume::HUGEINT) OVER w AS prefix_cash,MAX(GREATEST(event_ms,local_ms)) OVER w AS prefix_ready,CASE WHEN event_ms BETWEEN 33900000 AND 34200999 THEN 570 WHEN event_ms BETWEEN 41400000 AND 41402999 THEN 689 WHEN event_ms BETWEEN 54000000 AND 54002999 THEN 899 ELSE event_ms//60000 END AS minute FROM all_native WHERE ticker=? WINDOW w AS (ORDER BY event_ms,tran_id,channel ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)',[ticker])
            dayrow = con.execute('SELECT COUNT(*),ARG_MIN(price_x10000,trade_count),MAX(price_x10000),MIN(price_x10000),ARG_MAX(price_x10000,trade_count),SUM(volume),SUM(price_x10000::HUGEINT*volume::HUGEINT) FROM native_stock').fetchone();assert dayrow[0] == sd['actual_trade_rows']
            primary_path = p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary = p.json.loads(primary_path.read_text());day = next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0] == date)
            day_exact = day['tradestatus'] == '1' and list(map(int,dayrow[1:])) == [int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
            assert day_exact == sd['primary_daily_six_exact']
            leaf = leaves[symbol];quote_path = p.Path(leaf['path']);before = p.identity(quote_path);vf = 'TradVolume' if symbol.endswith('.SH') else 'Volume'
            if leaf.get('verified_identity'):
                assert before == leaf['verified_identity'];pf = p.pq.ParquetFile(quote_path)
            else:
                raw = quote_path.read_bytes();assert p.hashlib.sha256(raw).hexdigest() == leaf['sha256'];pf = p.pq.ParquetFile(p.pa.BufferReader(raw))
            cols = ['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','OpenPrice','Turnover',vf];raw_quotes = []
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=cols,use_threads=False):
                for q in batch.to_pylist():
                    if q['SecurityID'] != ticker:continue
                    qty = D(str(q[vf]));amount = D(str(q['Turnover']))*10000;price = D(str(q['LastPrice']))*10000;opening = D(str(q['OpenPrice']))*10000
                    if qty<=0 or price<=0:continue
                    assert all(v == v.to_integral_value() for v in [qty,amount,price,opening])
                    raw_quotes.append(dict(reported_ms=clock(q['UpdateTime']),received_ms=clock(q['LocalTime']),sequence=str(q['SeqNo']),price_x10000=int(price),open_price_x10000=int(opening),quantity=int(qty),amount_x10000=int(amount)))
            assert p.identity(quote_path) == before and len(raw_quotes)<=10000
            con.register('observed_quotes',p.pa.Table.from_pylist(raw_quotes));quotes = []
            qr = con.execute('SELECT q.*,n.trade_count,n.event_ms,n.local_ms,n.tran_id,n.channel,n.prefix_ready FROM observed_quotes q LEFT JOIN native_stock n ON q.quantity=n.prefix_quantity AND q.amount_x10000=n.prefix_cash AND q.price_x10000=n.price_x10000 ORDER BY q.reported_ms,q.received_ms,q.sequence').fetchall()
            fields = ['reported_ms','received_ms','sequence','price_x10000','open_price_x10000','quantity','amount_x10000']
            for row in qr:
                q = dict(zip(fields,row[:7]));valid = row[7] is not None;q['actual_whole_prefix_exact'] = valid
                if valid:
                    q.update(dict(zip(['source_prefix_trade_count','source_prefix_last_event_ms','source_prefix_last_local_ms','source_prefix_last_transaction_id','source_prefix_last_channel','source_prefix_ready_ms'],map(int,row[7:]))))
                    q['source_dependency_ready_ms'] = max(q['received_ms'],q['source_prefix_ready_ms']);q['source_last_event_offset_from_reported_ms'] = q['source_prefix_last_event_ms']-q['reported_ms']
                quotes.append(q)
            assert len(quotes) == sd['positive_quote_rows'] and sum(q['actual_whole_prefix_exact'] for q in quotes) == sd['native_prefix_exact_positive_quote_rows']
            endpoints = {slot:next((q for q in reversed(quotes) if q['reported_ms']<END[slot]),None) for slot in range(4)}
            bars = {}
            for row in con.execute('SELECT minute,ARG_MIN(price_x10000,trade_count),MAX(price_x10000),MIN(price_x10000),ARG_MAX(price_x10000,trade_count),SUM(volume),SUM(price_x10000::HUGEINT*volume::HUGEINT),COUNT(*),MIN(event_ms),MAX(event_ms),ARG_MIN(tran_id,trade_count),ARG_MAX(tran_id,trade_count),MAX(GREATEST(event_ms,local_ms)) FROM native_stock GROUP BY minute ORDER BY minute').fetchall():
                v = dict(zip(['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','first_transaction_id','last_transaction_id','source_dependency_ready_ms'],map(int,row[1:])))
                v['source_last_event_ms'] = v['last_ms'];v['source_kind'] = 'actual_native_millisecond_trades';bars[int(row[0])] = v
            sr = source_records.get((date,symbol));byminute = {}
            if sr:
                assert sr['quote_source_path'] == str(quote_path) and sr['quote_source_sha256'] == leaf['sha256'] and sr['quote_source_identity'] == before and sr['quote_source_kind'] == kind
                assert sr['primary_source_path'] == str(primary_path) and p.sha(primary_path) == sr['primary_source_sha256'] and sr['stockday_native_primary_six_exact'] and day_exact
                mpath = p.Path(sr['minute_path']);assert p.identity(mpath) == sr['verified_identity'];raw = mpath.read_bytes();assert p.hashlib.sha256(raw).hexdigest() == sr['minute_sha256']
                mrows = p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pylist();byminute = {r['left_minute']:r for r in mrows};assert len(mrows) == len(byminute) == len(sr['hours'])*60
                for r in mrows:
                    m = r['left_minute'];assert r['symbol'] == symbol and r['date'] == date and all(r[f] == v for f,v in bars[m].items())
            hs = [h for k,h in requests.items() if k[:2] == (date,symbol)];qpath = p.Path(hs[0]['sina_reference_path']);raw = qpath.read_bytes();digest = p.hashlib.sha256(raw).hexdigest();assert all(h['sina_reference_sha256'] == digest for h in hs)
            text = raw.decode();qr,_ = p.json.JSONDecoder().raw_decode(text[text.index('['):]);sina = {q['day']:q for q in qr}
            for request in hs:
                slot = request['slot'];key = date,symbol,slot;assert not mask[cal['dates'].index(date)*4+slot,cal['union_symbols'].index(symbol)]
                grid = (list(range(570,690))+list(range(780,900)))[slot*60:(slot+1)*60];absent = [m for m in grid if m not in bars]
                for m in grid:
                    v = bars[m];end = m+1;q = sina[f'{date} {end//60:02d}:{end%60:02d}:00'];assert all(D(q[f])*10000 == v[f] for f in ['open','high','low','close']) and D(q['volume']) == v['volume'];extra['sina_positive_minutes_verified'] += 1
                reference = hours[slot];end = endpoints[slot];previous = endpoints.get(slot-1)
                samples = [q for q in quotes if START[slot]<=q['reported_ms']<END[slot]]
                opening = end if slot==0 else next((q for q in quotes if START[slot]<=q['reported_ms']<END[slot]),None) 
                for name,q in [('current_quote',end),('previous_quote',previous),('opening_quote',opening)]:assert ({f:q[f] for f in fields} if q else None) == request[name]
                assert len(samples) == request['observed_price_sample_count']
                dependencies = samples+[q for q in [end,previous,opening] if q];model = None
                reason = 'primary_whole_day_six_conflict' if not day_exact else 'canonical_positive_minute_absent' if absent else 'source_quote_dependencies_absent' if not end or not opening or not samples or (slot and not previous) else 'sampled_quote_native_prefix_cash_quantity_or_price_conflict' if any(not q['actual_whole_prefix_exact'] for q in dependencies) else 'source_quote_daily_open_actual_first_price_conflict' if slot==0 and opening['open_price_x10000'] != int(dayrow[1]) else None
                if not reason:
                    # Independent SQL reduces the published positive quote table;
                    # dependency/prefix proofs above never filter price samples.
                    inclusive = '>='
                    row = con.execute('SELECT MAX(price_x10000),MIN(price_x10000),COUNT(*) FROM observed_quotes WHERE reported_ms '+inclusive+' ? AND reported_ms<?',[START[slot],END[slot]]).fetchone()
                    op = end['open_price_x10000'] if slot==0 else opening['price_x10000']
                    model = dict(open=op,high=max(op,int(row[0])),low=min(op,int(row[1])),close=end['price_x10000'],volume=end['quantity']-(previous['quantity'] if previous else 0))
                    assert int(row[2]) == len(samples)
                    if model != reference:reason = 'fixed_sampled_quote_ohlcv_conflict'
                if reason:
                    assert key in excluded and key not in accepted and reason == excluded[key]['reason'] and model == excluded[key]['coarse_model'] and reference == excluded[key]['coarse_reference']
                    counts[reason] += 1
                else:
                    assert key in accepted and key not in excluded;h = accepted[key];block = [bars[m] for m in grid]
                    expected = dict(open=block[0]['open'],high=max(v['high'] for v in block),low=min(v['low'] for v in block),close=block[-1]['close'],volume=sum(v['volume'] for v in block),amount_x10000=sum(v['amount_x10000'] for v in block),source_event_ms_max=max(v['source_last_event_ms'] for v in block),source_ready_ms=max(v['source_dependency_ready_ms'] for v in block),actual_trade_minutes=60,genuine_zero_trade_quote_minutes=0)
                    assert all(h[f] == v for f,v in expected.items()) and all(m in byminute for m in grid)
                    assert h['coarse_reference_kind'] == 'fixed_first_observed_quote_open_reconciled' and h['coarse_model'] == h['coarse_reference'] == reference == model
                    assert h['current_quote_cut'] == end and h['previous_quote_cut'] == previous and h['opening_quote'] == opening and h['sampled_quote_count'] == len(samples)
                    assert h['coarse_quote_dependency_ready_ms'] == max(q['source_dependency_ready_ms'] for q in dependencies) and h['all_sampled_quote_whole_prefixes_exact'] and h['observed_quote_price_extrema_do_not_replace_actual_minute_extrema']
                    assert h['sina_reference_path'] == str(qpath) and h['sina_reference_sha256'] == digest and h['independent_positive_minute_ohlcv_exact'] and h['real_zero_trade_quote_witnesses_exact'] and h['coarse_definition_reconciliation_exact']
                    counts['fixed_first_observed_quote_open_reconciled'] += 1;extra['all_actual_prefix_quote_samples_independently_verified'] += len(samples)
                    verified_accepted.append(dict(date=date,symbol=symbol,slot=slot,all_native_minute_fields_and_receiver_dependencies_exact=True,all_sampled_quote_native_cash_quantity_last_price_prefixes_exact=True))
                verified.append(dict(date=date,symbol=symbol,slot=slot,reason=reason,fixed_sampled_quote_model=model,coarse_reference=hours[slot]))
            if sr:assert p.identity(p.Path(sr['minute_path'])) == sr['verified_identity']
            p.atomic_json(status_path,dict(status='replaying',updated_utc=p.utc(),stockdays_checked=len({(r['date'],r['symbol']) for r in verified}),hours_checked=len(verified),counts=dict(counts),additional_diagnostics=dict(extra),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
        assert p.identity(artifact) == order['accepted_artifact_identity_guard']
    con.close();assert len(verified_accepted) == stage['hours_staged'];assert dict(counts) == stage['counts'] and len(verified) == stage['requested_hours'] and p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),hours_verified=len(verified_accepted),minutes_verified=len(verified_accepted)*60,stockdays_verified=len(source_records),excluded_hours_verified=len(excluded),counts=dict(counts),additional_diagnostics=dict(extra),records=verified_accepted,rejections=[r for r in verified if r['reason']],
        all_candidates_and_rejections_independently_reproduced=True,all_actual_sampled_quote_cash_quantity_price_and_native_prefix_fields_exact=True,canonical_all60_sina_ohlcv_independently_reproduced=True,raw_tencent_source_independently_decoded=True,
        native_daily_six_reproduced=True,all_actual_sampled_quote_prefixes_and_price_extrema_reconstructed_with_independent_sql=True,canonical_minute_clock_changed=False,candidate_hours_admitted=0,raw_candidate_modified=False,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    p.atomic_json(status_path,dict(status='complete',finished_utc=p.utc(),hours_checked=len(verified),counts=dict(counts),additional_diagnostics=dict(extra)));lock.close()


if __name__ == '__main__':main()
