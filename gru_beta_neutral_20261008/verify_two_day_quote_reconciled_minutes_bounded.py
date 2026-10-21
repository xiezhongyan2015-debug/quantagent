"""Independently replay native candles, empty-interval prefixes and all rejects.

SQL reduces actual native millisecond trades to <=240 minutes per stock. An
empty minute's whole prefix is reconstructed from those independently computed
minutes, including its final exchange identity and maximum recorded reception.
No dense cumulative trade table or rehash of accepted native archives is used.
"""
from collections import Counter, defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_two_day_quote_reconciled_minutes_bounded import DEST, RAW, DIAGNOSTIC
import duckdb


def clock(value):
    h,m,s = str(value).split(':')
    return int(h)*3600000+int(m)*60000+int(D(s)*1000)


def main():
    lock = p.locked('verify_two_day_quote_reconciled')
    path = DEST/'receipt.json';stage = p.json.loads(path.read_text());output = DEST/'independent_verification.json';assert not output.exists()
    assert stage['status'] == 'staged_real_quote_minutes_pending_independent_audit' and not stage['raw_panel_modified']
    assert stage['source_code_sha256'] == p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_two_day_quote_reconciled_minutes_bounded.py')
    assert stage['numeric_prefix_helper_code_sha256'] == p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_sep22_quote_witness_minutes_bounded.py')
    assert p.sha(DIAGNOSTIC) == stage['diagnostic_receipt_sha256'];diagnostic = p.json.loads(DIAGNOSTIC.read_text())
    audit_path = RAW/'raw_full_history_audit.json';audit = p.json.loads(audit_path.read_text())
    assert audit['passed'] and p.sha(audit_path) == stage['parent_raw_audit_sha256'] and audit['audited_raw_meta_sha256'] == p.sha(RAW/'dataset/meta.json')
    mask_identity = next(a['verified_identity'] for a in audit['arrays'] if a['name'] == 'hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    mask = p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal = p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    requested = defaultdict(lambda:defaultdict(list))
    for h in diagnostic['records']:requested[h['date']][h['symbol'][:6]].append(h)
    keys = {(h['date'],h['symbol'],h['slot']) for h in diagnostic['records']}
    accepted = {(r['date'],r['symbol'],h['slot']):h for r in stage['records'] for h in r['hours']}
    rejected = {(h['date'],h['symbol'],h['slot']):h for h in stage['excluded_hours']}
    assert len(keys) == stage['requested_hours'] and set(accepted).isdisjoint(rejected) and set(accepted)|set(rejected) == keys
    records = {(r['date'],r['symbol'][:6]):r for r in stage['records']}
    coarse_path = p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';coarse = p.json.loads(coarse_path.read_text())
    coarse_proof_path = coarse_path.parent/'independent_hour_comparison_verification.json';coarse_proof = p.json.loads(coarse_proof_path.read_text())
    assert coarse_proof['passed'] and coarse_proof['audited_receipt_sha256'] == p.sha(coarse_path)
    assert p.sha(coarse_path) == diagnostic['coarse_comparison_receipt_sha256'] and p.sha(coarse_proof_path) == diagnostic['coarse_proof_sha256']
    acquisition_path = coarse_path.parent/'all_target_stock_acquisition.json';assert p.sha(acquisition_path) == coarse['source_acquisition_sha256']
    refs = {}
    for r in p.json.loads(acquisition_path.read_text())['records']:
        if not any(h[1] == r['symbol'] for h in keys):continue
        qpath = p.Path(r['path']);assert p.identity(qpath) == r['verified_identity'];data = qpath.read_bytes();assert p.hashlib.sha256(data).hexdigest() == r['sha256']
        for q in p.json.loads(data)['data'][r['code']]['m60']:
            t = q[0];date = f'{t[:4]}-{t[4:6]}-{t[6:8]}';slot = ['1030','1130','1400','1500'].index(t[-4:]);key = date,r['symbol'],slot
            if key not in keys:continue
            refs[key] = {f:int(D(q[i])*10000) for f,i in [('open',1),('close',2),('high',3),('low',4)]};refs[key]['volume'] = int(D(q[5])*100)
        assert p.identity(qpath) == r['verified_identity']
    assert set(refs) == keys
    counts = Counter();verified = [];verified_rejections = [];grid = list(range(570,690))+list(range(780,900))
    spill = DEST/'independent_sql_spill';spill.mkdir(exist_ok=True);con = duckdb.connect()
    con.execute("SET memory_limit='192MB'");con.execute('SET threads=1');con.execute('SET temp_directory=?',[str(spill)]);con.execute("SET max_temp_directory_size='2GB'")
    for pin in stage['source_pins']:
        date = pin['date'];pair_path = p.Path(pin['pairing_path']);order_path = p.Path(pin['order_path'])
        assert p.sha(pair_path) == pin['pairing_sha256'] and p.sha(order_path) == pin['order_sha256']
        pair = p.json.loads(pair_path.read_text());order = p.json.loads(order_path.read_text())
        assert pair['all_rows_price_quantity_and_floor_clock_exact'] and pair['stockdays_checked'] == 624 and order['paired_source_receipt_sha256'] == p.sha(pair_path)
        assert order['intrasecond_event_inversions'] == order['minute_open_changed_by_intrasecond_order'] == order['minute_close_changed_by_intrasecond_order'] == 0
        artifact = p.Path(pair['native_field_artifact']['path']);assert p.identity(artifact) == order['accepted_artifact_identity_guard']
        quote_path = p.Path(pin['quote_receipt_path']);quote_proof_path = p.Path(pin['quote_proof_path']);assert p.sha(quote_path) == pin['quote_receipt_sha256'] and p.sha(quote_proof_path) == pin['quote_proof_sha256']
        quote_source = p.json.loads(quote_path.read_text());quote_proof = p.json.loads(quote_proof_path.read_text());assert quote_proof['passed']
        if pin['quote_source_kind'] == 'native_snapshot':
            assert quote_proof['audited_receipt_sha256'] == p.sha(quote_path);leaves = {r['symbol']:r for r in quote_source['records'] if r['kind'] == 'snapshot'}
        else:
            assert quote_proof['audited_full_union_trade_prefix_receipt_sha256'] == p.sha(quote_path);leaves = {r['symbol']:r['quote_prefix_witnesses'] for r in quote_source['records']}
        con.read_parquet(str(artifact)).create_view('all_native',replace=True);con.register('selected',p.pa.table({'ticker':sorted(requested[date])}))
        con.execute('CREATE OR REPLACE VIEW native AS SELECT n.*,CASE WHEN event_ms BETWEEN 33900000 AND 34200999 THEN 570 WHEN event_ms BETWEEN 41400000 AND 41402999 THEN 689 WHEN event_ms BETWEEN 54000000 AND 54002999 THEN 899 ELSE event_ms//60000 END AS minute FROM all_native n JOIN selected s USING(ticker)')
        cursor = con.execute('SELECT ticker,minute,ARG_MIN(price_x10000,struct_pack(t:=event_ms,id:=tran_id,ch:=channel)),MAX(price_x10000),MIN(price_x10000),ARG_MAX(price_x10000,struct_pack(t:=event_ms,id:=tran_id,ch:=channel)),SUM(volume),SUM(price_x10000::HUGEINT*volume::HUGEINT),COUNT(*),MIN(event_ms),MAX(event_ms),MAX(GREATEST(event_ms,local_ms)),ARG_MIN(tran_id,struct_pack(t:=event_ms,id:=tran_id,ch:=channel)),ARG_MAX(tran_id,struct_pack(t:=event_ms,id:=tran_id,ch:=channel)),ARG_MAX(local_ms,struct_pack(t:=event_ms,id:=tran_id,ch:=channel)),ARG_MAX(channel,struct_pack(t:=event_ms,id:=tran_id,ch:=channel)) FROM native GROUP BY ticker,minute ORDER BY ticker,minute')

        def finish(ticker, actual):
            hs = requested[date][ticker];symbol = hs[0]['symbol'];sr = records.get((date,ticker));assert set(actual)<=set(grid)
            primary_path = p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');data = primary_path.read_bytes();primary = p.json.loads(data)
            if sr:assert sr['primary_source_path'] == str(primary_path) and p.hashlib.sha256(data).hexdigest() == sr['primary_source_sha256']
            day = next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0] == date)
            daily = [actual[min(actual)]['open'],max(v['high'] for v in actual.values()),min(v['low'] for v in actual.values()),actual[max(actual)]['close'],sum(v['volume'] for v in actual.values()),sum(v['amount_x10000'] for v in actual.values())]
            day_exact = day['tradestatus'] == '1' and daily == [int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
            cuts = {};qsum = csum = count = ready = 0;last = None
            for m in grid:
                if m in actual:
                    v = actual[m];qsum += v['volume'];csum += v['amount_x10000'];count += v['trades'];ready = max(ready,v['source_dependency_ready_ms']);last = v
                elif last is not None:
                    cuts[m] = dict(source_prefix_last_event_ms=last['last_ms'],source_prefix_last_local_ms=last['last_local_ms'],source_prefix_last_transaction_id=last['last_transaction_id'],
                        source_prefix_last_channel=last['last_channel'],source_quote_last_price_x10000=last['close'],source_quote_quantity=qsum,source_quote_amount_x10000=csum,source_prefix_ready_ms=ready,source_prefix_trade_count=count)
            leaf = leaves[symbol];qpath = p.Path(leaf['path']);before = p.identity(qpath);kind = pin['quote_source_kind'];vf = 'TradVolume' if symbol.endswith('.SH') else 'Volume'
            if kind == 'native_snapshot':assert before == leaf['verified_identity'];pf = p.pq.ParquetFile(qpath);cols = ['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','Turnover',vf]
            else:
                raw = qpath.read_bytes();assert p.hashlib.sha256(raw).hexdigest() == leaf['sha256'];pf = p.pq.ParquetFile(p.pa.BufferReader(raw));assert pf.metadata.num_rows == leaf['rows'];cols = None
            zeros = {};chosen = {}
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=cols,use_threads=False):
                for q in batch.to_pylist():
                    if kind == 'native_snapshot':
                        if q['SecurityID'] != ticker:continue
                        t = clock(q['UpdateTime']);received = clock(q['LocalTime']);seq = str(q['SeqNo']);qty = D(str(q[vf]));cash = D(str(q['Turnover']))*10000;price = D(str(q['LastPrice']))*10000
                    else:
                        if not (q['whole_trade_quantity_prefix_exact'] and q['calculated_trade_prefix_amount_exact'] and q['last_trade_price_exact']):continue
                        t = q['quote_reported_ms'];received = q['quote_received_ms'];seq = str(q['quote_seq_no']);qty = D(q['quote_quantity']);cash = D(q['quote_Turnover'])*10000;price = D(q['quote_LastPrice'])*10000
                    m = t//60000;cut = cuts.get(m)
                    if cut is None or qty<=0 or qty != cut['source_quote_quantity'] or cash != cut['source_quote_amount_x10000'] or price != cut['source_quote_last_price_x10000']:continue
                    assert cut['source_prefix_last_event_ms'] < (m+1)*60000
                    key = t,received,seq
                    if m in chosen and chosen[m] <= key:continue
                    chosen[m] = key;zeros[m] = dict(open=int(price),high=int(price),low=int(price),close=int(price),volume=0,amount_x10000=0,trades=0,
                        source_kind='genuine_quote_in_verified_zero_trade_interval',source_last_event_ms=cut['source_prefix_last_event_ms'],source_quote_reported_ms=t,source_quote_received_ms=received,
                        source_quote_sequence=seq,source_dependency_ready_ms=max(cut['source_prefix_ready_ms'],received),**cut)
            assert p.identity(qpath) == before
            if sr:assert sr['quote_source_path'] == str(qpath) and sr['quote_source_sha256'] == leaf['sha256'] and sr['quote_source_identity'] == before and sr['quote_source_kind'] == kind
            byminute = {};real_fields = ['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','first_transaction_id','last_transaction_id','source_last_event_ms','source_dependency_ready_ms']
            if sr:
                path = p.Path(sr['minute_path']);assert p.identity(path) == sr['verified_identity'];data = path.read_bytes();assert p.hashlib.sha256(data).hexdigest() == sr['minute_sha256']
                rows = p.pq.ParquetFile(p.pa.BufferReader(data)).read().to_pylist();byminute = {r['left_minute']:r for r in rows};assert len(rows) == len(byminute) == len(sr['hours'])*60
                for row in rows:
                    m = row['left_minute'];assert row['symbol'] == symbol and row['date'] == date
                    if row['trades']>0:
                        assert row['source_kind'] == 'actual_native_millisecond_trades' and all(row[f] == actual[m][f] for f in real_fields);counts['real_trade_minutes'] += 1
                    else:assert all(row[f] == v for f,v in zeros[m].items());counts['exact_prefix_zero_quote_minutes'] += 1
            sina_path = p.Path(hs[0]['sina_reference_path']);data = sina_path.read_bytes();digest = p.hashlib.sha256(data).hexdigest();assert all(h['sina_reference_sha256'] == digest for h in hs)
            text = data.decode();qr,_ = p.json.JSONDecoder().raw_decode(text[text.index('['):]);sina = {q['day']:q for q in qr};allbars = {**actual,**zeros}
            for request in hs:
                slot = request['slot'];key = date,symbol,slot;minutes = grid[slot*60:(slot+1)*60];absent = [m for m in minutes if m not in allbars]
                assert not mask[cal['dates'].index(date)*4+slot,cal['union_symbols'].index(symbol)]
                for m in minutes:
                    if m not in actual:continue
                    v = actual[m];end = m+1;q = sina[f'{date} {end//60:02d}:{end%60:02d}:00'];assert all(v[f] == D(q[f])*10000 for f in ['open','high','low','close']) and D(v['volume']) == D(q['volume'])
                reason = 'primary_whole_day_six_conflict' if not day_exact else 'missing_genuine_quote' if absent else None
                if reason is None:
                    block = [allbars[m] for m in minutes];values = dict(open=block[0]['open'],high=max(v['high'] for v in block),low=min(v['low'] for v in block),close=block[-1]['close'],volume=sum(v['volume'] for v in block),amount_x10000=sum(v['amount_x10000'] for v in block),source_event_ms_max=max(v['source_last_event_ms'] for v in block),source_ready_ms=max(v['source_dependency_ready_ms'] for v in block),actual_trade_minutes=sum(v['trades']>0 for v in block),genuine_zero_trade_quote_minutes=sum(v['trades']==0 for v in block))
                    previous = max((m for m in actual if m<minutes[0]),default=None);carry = actual[previous]['close'] if previous is not None else values['open'];prior_exact = False
                    if previous is not None:
                        end = previous+1;q = sina.get(f'{date} {end//60:02d}:{end%60:02d}:00');prior_exact = q is not None and D(q['close'])*10000 == carry
                    formula = dict(open=carry,high=max(carry,values['high']),low=min(carry,values['low']),close=values['close'],volume=values['volume'])
                    coarse_kind = 'direct_coarse_ohlcv_exact' if all(values[f] == refs[key][f] for f in refs[key]) else 'fixed_previous_close_coarse_reconciled' if prior_exact and formula == refs[key] else None
                    if coarse_kind is None:reason = 'coarse_conflict_after_real_quote'
                if reason:
                    assert key in rejected and key not in accepted and rejected[key]['reason'] == reason and rejected[key]['missing_minutes'] == absent
                    counts[reason] += 1;verified_rejections.append(dict(date=date,symbol=symbol,slot=slot,reason=reason,missing_minutes=absent));continue
                assert key in accepted and key not in rejected;h = accepted[key];assert all(h[f] == v for f,v in values.items())
                assert h['coarse_reference_kind'] == coarse_kind and h['coarse_reference'] == refs[key] and h['previous_observed_minute'] == previous and h['previous_observed_close'] == carry and h['previous_close_independent_exact'] == prior_exact
                assert h['sina_reference_path'] == str(sina_path) and h['sina_reference_sha256'] == digest and h['real_zero_trade_quote_witnesses_exact'] and h['independent_positive_minute_ohlcv_exact']
                assert all(m in byminute for m in minutes);counts[coarse_kind] += 1
            if sr:verified.append(dict(date=date,symbol=symbol,hours=len(sr['hours']),all_actual_millisecond_and_zero_quote_fields_exact=True,primary_daily_six_exact=True));assert p.identity(p.Path(sr['minute_path'])) == sr['verified_identity']

        current = None;bars = {}
        while rows := cursor.fetchmany(4096):
            for row in rows:
                ticker,minute = row[:2]
                if ticker != current:
                    if current is not None:finish(current,bars)
                    current = ticker;bars = {}
                fields = ['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','source_dependency_ready_ms','first_transaction_id','last_transaction_id','last_local_ms','last_channel']
                v = dict(zip(fields,map(int,row[2:])));v['source_last_event_ms'] = v['last_ms'];assert int(minute) not in bars;bars[int(minute)] = v
        if current is not None:finish(current,bars)
        assert p.identity(artifact) == order['accepted_artifact_identity_guard']
    con.close();assert {k:v for k,v in counts.items() if k not in ['real_trade_minutes','exact_prefix_zero_quote_minutes']} == stage['counts']
    assert len(verified) == stage['stockdays_staged'] and len(verified_rejections) == len(stage['excluded_hours'])
    assert counts['real_trade_minutes']+counts['exact_prefix_zero_quote_minutes'] == stage['actual_minutes_staged'] == len(accepted)*60
    assert counts['exact_prefix_zero_quote_minutes'] == stage['genuine_zero_trade_quote_minutes'] and p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),counts=dict(counts),records=verified,rejections=verified_rejections,
        stockdays_verified=len(verified),hours_verified=len(accepted),minutes_verified=len(accepted)*60,all_candidate_and_rejected_hours_independently_reproduced=True,
        independent_sql_actual_millisecond_candles_and_empty_interval_trade_prefixes=True,real_observed_quote_fields_exact=True,positive_minute_sina_ohlcv_exact=True,
        raw_tencent_reference_independently_decoded=True,coarse_direct_or_fixed_previous_close_reproduced=True,all_native_daily_six_exact=True,observed_receiver_dependencies_exact=True,
        raw_candidate_hours_admitted=0,raw_candidate_modified=False,source_live_clock_certificate=False,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    lock.close()


if __name__ == '__main__':main()
