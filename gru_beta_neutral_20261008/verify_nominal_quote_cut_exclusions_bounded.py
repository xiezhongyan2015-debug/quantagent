"""Independent SQL replay of all629 rejected nominal-cut candidates.

Only one stock is materialized in bounded SQL memory. The canonical minute
aggregation and quantity-defined cuts are computed independently of NumPy.
"""
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_nominal_quote_cut_minutes_bounded import DEST, RAW, DIAGNOSTIC
import duckdb

END = [37800000,41400000,50400000,54000000]


def clock(value):
    h,m,s = str(value).split(':');return int(h)*3600000+int(m)*60000+int(D(s)*1000)


def main():
    lock = p.locked('verify_nominal_quote_cut_exclusions')
    path = DEST/'receipt.json';stage = p.json.loads(path.read_text());output = DEST/'independent_verification.json';assert not output.exists()
    assert stage['status'] == 'staged_nominal_quote_cut_minutes_pending_independent_audit'
    assert stage['source_code_sha256'] == p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_nominal_quote_cut_minutes_bounded.py')
    assert stage['hours_staged'] == stage['actual_minutes_staged'] == 0 and not stage['records'] and not stage['raw_panel_modified']
    assert p.sha(DIAGNOSTIC) == stage['diagnostic_receipt_sha256'];diagnostic = p.json.loads(DIAGNOSTIC.read_text())
    requests = {(h['date'],h['symbol'],h['slot']):h for h in diagnostic['records'] if h['status'] == 'actual_nominal_quote_quantity_and_close_exact'}
    excluded = {(h['date'],h['symbol'],h['slot']):h for h in stage['excluded_hours']};assert set(requests) == set(excluded) and len(requests) == stage['requested_hours']
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
    counts = Counter();extra = Counter();verified = [];status_path = DEST/'independent_replay_progress.json'
    for pin in stage['source_pins']:
        date = pin['date'];pair_path = p.Path(pin['pairing_path']);order_path = p.Path(pin['order_path'])
        assert p.sha(pair_path) == pin['pairing_sha256'] and p.sha(order_path) == pin['order_sha256'];pair = p.json.loads(pair_path.read_text());order = p.json.loads(order_path.read_text())
        assert pair['all_rows_price_quantity_and_floor_clock_exact'] and pair['stockdays_checked'] == 624 and order['paired_source_receipt_sha256'] == p.sha(pair_path)
        assert order['intrasecond_event_inversions'] == order['minute_open_changed_by_intrasecond_order'] == order['minute_close_changed_by_intrasecond_order'] == 0
        artifact = p.Path(pair['native_field_artifact']['path']);assert p.identity(artifact) == order['accepted_artifact_identity_guard']
        source_path = p.Path(pin['quote_receipt_path']);proof_path = p.Path(pin['quote_proof_path']);assert p.sha(source_path) == pin['quote_receipt_sha256'] and p.sha(proof_path) == pin['quote_proof_sha256']
        source = p.json.loads(source_path.read_text());proof = p.json.loads(proof_path.read_text());assert proof['passed'];kind = pin['quote_source_kind']
        if kind == 'native_snapshot':
            assert proof['audited_receipt_sha256'] == p.sha(source_path);leaves = {r['symbol']:r for r in source['records'] if r['kind'] == 'snapshot'}
        else:
            assert proof['audited_full_union_trade_prefix_receipt_sha256'] == p.sha(source_path);leaves = {r['symbol']:r['quote_prefix_witnesses'] for r in source['records']}
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
            con.register('wanted_cuts',p.pa.table({'slot':[0,1,2,3],'quantity':cumulative}))
            rows = con.execute('SELECT c.slot,n.trade_count,n.prefix_quantity,n.prefix_cash,n.price_x10000,n.event_ms,n.local_ms,n.tran_id,n.channel,n.prefix_ready FROM native_stock n JOIN wanted_cuts c ON n.prefix_quantity=c.quantity ORDER BY c.slot').fetchall();cuts = {}
            fields = ['source_prefix_trade_count','source_quote_quantity','source_quote_amount_x10000','source_quote_last_price_x10000','source_prefix_last_event_ms','source_prefix_last_local_ms','source_prefix_last_transaction_id','source_prefix_last_channel','source_prefix_ready_ms']
            for row in rows:assert row[0] not in cuts;cuts[int(row[0])] = dict(zip(fields,map(int,row[1:])))
            leaf = leaves[symbol];quote_path = p.Path(leaf['path']);before = p.identity(quote_path);vf = 'TradVolume' if symbol.endswith('.SH') else 'Volume'
            if kind == 'native_snapshot':
                assert before == leaf['verified_identity'];pf = p.pq.ParquetFile(quote_path);cols = ['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','Turnover',vf]
            else:
                raw = quote_path.read_bytes();assert p.hashlib.sha256(raw).hexdigest() == leaf['sha256'];pf = p.pq.ParquetFile(p.pa.BufferReader(raw));assert pf.metadata.num_rows == leaf['rows'];cols = None
            quotes = {};chosen = {}
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=cols,use_threads=False):
                for q in batch.to_pylist():
                    if kind == 'native_snapshot':
                        if q['SecurityID'] != ticker:continue
                        t = clock(q['UpdateTime']);received = clock(q['LocalTime']);seq = str(q['SeqNo']);qty = D(str(q[vf]));amount = D(str(q['Turnover']))*10000;price = D(str(q['LastPrice']))*10000
                    else:
                        if not (q['whole_trade_quantity_prefix_exact'] and q['calculated_trade_prefix_amount_exact'] and q['last_trade_price_exact']):continue
                        t = q['quote_reported_ms'];received = q['quote_received_ms'];seq = str(q['quote_seq_no']);qty = D(q['quote_quantity']);amount = D(q['quote_Turnover'])*10000;price = D(q['quote_LastPrice'])*10000
                    if t not in END:continue
                    slot = END.index(t);cut = cuts.get(slot)
                    if cut is None or qty != cut['source_quote_quantity'] or amount != cut['source_quote_amount_x10000'] or price != cut['source_quote_last_price_x10000']:continue
                    if kind != 'native_snapshot':assert q['last_actual_trade_EventTime_ms'] == cut['source_prefix_last_event_ms']
                    key = t,received,seq
                    if slot not in chosen or key<chosen[slot]:
                        chosen[slot] = key;quotes[slot] = dict(**cut,source_quote_reported_ms=t,source_quote_received_ms=received,source_quote_sequence=seq,source_dependency_ready_ms=max(received,cut['source_prefix_ready_ms']),source_last_event_offset_from_nominal_ms=cut['source_prefix_last_event_ms']-t)
            assert p.identity(quote_path) == before and {str(k):v for k,v in quotes.items()} == sd['actual_exact_nominal_quote_cuts']
            bars = {}
            for row in con.execute('SELECT minute,ARG_MIN(price_x10000,trade_count),MAX(price_x10000),MIN(price_x10000),ARG_MAX(price_x10000,trade_count),SUM(volume) FROM native_stock GROUP BY minute ORDER BY minute').fetchall():
                bars[int(row[0])] = dict(zip(['open','high','low','close','volume'],map(int,row[1:])))
            hs = [h for k,h in requests.items() if k[:2] == (date,symbol)];qpath = p.Path(hs[0]['sina_reference_path']);raw = qpath.read_bytes();digest = p.hashlib.sha256(raw).hexdigest();assert all(h['sina_reference_sha256'] == digest for h in hs)
            text = raw.decode();qr,_ = p.json.JSONDecoder().raw_decode(text[text.index('['):]);sina = {q['day']:q for q in qr}
            for request in hs:
                slot = request['slot'];key = date,symbol,slot;assert not mask[cal['dates'].index(date)*4+slot,cal['union_symbols'].index(symbol)]
                grid = (list(range(570,690))+list(range(780,900)))[slot*60:(slot+1)*60];absent = [m for m in grid if m not in bars]
                for m in grid:
                    v = bars[m];end = m+1;q = sina[f'{date} {end//60:02d}:{end%60:02d}:00'];assert all(D(q[f])*10000 == v[f] for f in ['open','high','low','close']) and D(q['volume']) == v['volume'];extra['sina_positive_minutes_verified'] += 1
                model = None
                if slot in cuts and (not slot or slot-1 in cuts):
                    start = cuts[slot-1]['source_prefix_trade_count'] if slot else 0;end = cuts[slot]['source_prefix_trade_count']
                    carry = cuts[slot-1]['source_quote_last_price_x10000'] if slot else int(dayrow[1]);v = con.execute('SELECT MAX(price_x10000),MIN(price_x10000),ARG_MAX(price_x10000,trade_count),SUM(volume) FROM native_stock WHERE trade_count>? AND trade_count<=?',[start,end]).fetchone()
                    assert v[0] is not None;model = dict(open=carry,high=max(carry,int(v[0])),low=min(carry,int(v[1])),close=int(v[2]),volume=int(v[3]))
                    extra['quantity_defined_interval_model_exact_without_previous_quote_gate' if model == hours[slot] else 'quantity_defined_interval_model_conflicts_even_without_previous_quote_gate'] += 1
                else:extra['previous_cumulative_quantity_not_a_whole_native_trade_cut'] += 1
                reason = 'primary_whole_day_six_conflict' if not day_exact else 'canonical_positive_minute_absent' if absent else 'current_nominal_quote_native_cash_or_prefix_conflict' if slot not in quotes else 'previous_nominal_quote_native_prefix_not_bound' if slot>0 and slot-1 not in quotes else 'previous_tencent_close_native_cut_conflict' if slot and cuts[slot-1]['source_quote_last_price_x10000'] != hours[slot-1]['close'] else 'fixed_nominal_quote_cut_ohlcv_conflict' if model != hours[slot] else None
                assert reason and reason == excluded[key]['reason'];expected_model = None if reason in ['primary_whole_day_six_conflict','canonical_positive_minute_absent','current_nominal_quote_native_cash_or_prefix_conflict','previous_nominal_quote_native_prefix_not_bound'] else model
                assert expected_model == excluded[key]['coarse_model'] and hours[slot] == excluded[key]['coarse_reference'] == request['tencent_hour']
                assert quotes.get(slot) == excluded[key]['current_quote'] and quotes.get(slot-1) == excluded[key]['previous_quote']
                counts[reason] += 1;verified.append(dict(date=date,symbol=symbol,slot=slot,reason=reason,actual_prefix_interval_model_even_without_previous_quote_gate=model,coarse_reference=hours[slot]))
            p.atomic_json(status_path,dict(status='replaying',updated_utc=p.utc(),stockdays_checked=len({(r['date'],r['symbol']) for r in verified}),hours_checked=len(verified),counts=dict(counts),additional_diagnostics=dict(extra),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
        assert p.identity(artifact) == order['accepted_artifact_identity_guard']
    con.close();assert dict(counts) == stage['counts'] and len(verified) == stage['requested_hours'] and p.identity(RAW/'dataset/hour_complete.npy') == mask_identity
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),hours_verified=0,excluded_hours_verified=len(verified),counts=dict(counts),additional_diagnostics=dict(extra),records=verified,
        every_rejection_independently_reproduced=True,all_source_nominal_quote_cash_quantity_price_and_native_prefix_fields_exact=True,canonical_all60_sina_ohlcv_independently_reproduced=True,raw_tencent_source_independently_decoded=True,
        native_daily_six_reproduced=True,actual_prefix_intervals_reconstructed_with_independent_sql=True,canonical_minute_clock_changed=False,candidate_hours_admitted=0,raw_candidate_modified=False,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    p.atomic_json(status_path,dict(status='complete',finished_utc=p.utc(),hours_checked=len(verified),counts=dict(counts),additional_diagnostics=dict(extra)));lock.close()


if __name__ == '__main__':main()
