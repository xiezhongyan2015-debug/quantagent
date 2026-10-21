"""Independent bounded SQL candle/prefix audit and native quote field audit."""
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_sep22_quote_witness_minutes_bounded import DEST, DAY, RAW, WHOLE
import duckdb


def clock(value):
    hours, minutes, seconds = str(value).split(':')
    return int(hours)*3600000+int(minutes)*60000+int(D(seconds)*1000)


def main():
    lock=p.locked('verify_sep22_quote_witness_minutes')
    stage_path=DEST/'receipt.json';stage=p.json.loads(stage_path.read_text())
    assert stage['status']=='staged_pending_independent_audit' and not stage['raw_panel_modified']
    assert stage['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_sep22_quote_witness_minutes_bounded.py')
    pair_path=p.OUT/'millisecond_pairing_sep22/receipt.json';pair=p.json.loads(pair_path.read_text())
    assert p.sha(pair_path)==stage['source_pairing_sha256'] and pair['all_rows_price_quantity_and_floor_clock_exact']
    order_path=pair_path.parent/'intrasecond_order_verification.json';order=p.json.loads(order_path.read_text())
    assert p.sha(order_path)==stage['source_order_proof_sha256']
    artifact=p.Path(pair['native_field_artifact']['path'])
    assert p.identity(artifact)==stage['accepted_artifact_identity']==order['accepted_artifact_identity_guard']
    member_path=WHOLE/'independent_member_verification.json';member=p.json.loads(member_path.read_text())
    assert member['passed'] and p.sha(member_path)==stage['source_member_proof_sha256']
    acquisition_path=WHOLE/'acquisition_and_extraction.json';acquisition=p.json.loads(acquisition_path.read_text())
    assert p.sha(acquisition_path)==member['audited_receipt_sha256']
    source_quotes={r['symbol']:r for r in acquisition['records'] if r['kind']=='snapshot'}
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text())
    assert audit['passed'] and p.sha(audit_path)==stage['parent_raw_audit_sha256'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');offset=calendar['dates'].index(DAY)*4
    coarse_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json'
    coarse=p.json.loads(coarse_path.read_text());coarse_proof_path=coarse_path.parent/'independent_hour_comparison_verification.json'
    coarse_proof=p.json.loads(coarse_proof_path.read_text())
    assert coarse_proof['passed'] and p.sha(coarse_proof_path)==stage['independent_hour_proof_sha256'] and coarse_proof['audited_receipt_sha256']==p.sha(coarse_path)
    expected={}
    for g in coarse['groups']:
        if g['date']!=DAY:continue
        data=p.Path(g['comparison_path']).read_bytes();assert p.hashlib.sha256(data).hexdigest()==g['comparison_sha256']
        for h in p.json.loads(data)['comparisons']:
            if h['model']=='floor_left' and h.get('differences') and not any(h['differences'].values()):expected[h['symbol'],h['slot']]=h['native_hour']
    records={r['symbol'][:6]:r for r in stage['records']};requests=[];counts=Counter()

    def read_leaf(r):
        path=p.Path(r['minute_path']);assert p.identity(path)==r['verified_identity']
        data=path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['minute_sha256']
        rows=p.pq.ParquetFile(p.pa.BufferReader(data)).read().to_pylist()
        assert len(rows)==len(r['hours'])*60 and len({v['left_minute'] for v in rows})==len(rows)
        return rows

    for ticker,r in records.items():
        for row in read_leaf(r):
            if row['trades']==0:
                requests.append(dict(ticker=ticker,minute=row['left_minute'],q=row['source_quote_quantity']))
    assert len(requests)==stage['genuine_zero_trade_quote_minutes']
    spill=DEST/'independent_sql_spill';spill.mkdir(exist_ok=True)
    con=duckdb.connect();con.execute("SET memory_limit='256MB'");con.execute('SET threads=1')
    con.execute('SET temp_directory=?',[str(spill)]);con.execute("SET max_temp_directory_size='4GB'")
    con.read_parquet(str(artifact)).create_view('all_native')
    con.register('selected',p.pa.table({'ticker':sorted(records)}))
    con.execute('CREATE VIEW native AS SELECT n.*, CASE WHEN event_ms BETWEEN 33900000 AND 34200999 THEN 570 WHEN event_ms BETWEEN 41400000 AND 41402999 THEN 689 WHEN event_ms BETWEEN 54000000 AND 54002999 THEN 899 ELSE event_ms//60000 END AS minute FROM all_native n JOIN selected s USING(ticker)')
    # Window sums use a separate SQL implementation and numeric actual source rows.
    con.register('quote_requests',p.pa.Table.from_pylist(requests))
    prefix=con.execute('''WITH cumulative AS (
        SELECT ticker,event_ms,local_ms,tran_id,channel,price_x10000,
          SUM(volume) OVER w AS q, SUM(price_x10000::HUGEINT*volume::HUGEINT) OVER w AS cash,
          MAX(GREATEST(event_ms,local_ms)) OVER w AS ready, ROW_NUMBER() OVER w AS count
        FROM native WINDOW w AS (PARTITION BY ticker ORDER BY event_ms,tran_id,channel ROWS UNBOUNDED PRECEDING)
        ) SELECT r.ticker,r.minute,c.event_ms,c.local_ms,c.tran_id,c.channel,c.price_x10000,c.q,c.cash,c.ready,c.count
        FROM cumulative c JOIN quote_requests r ON c.ticker=r.ticker AND c.q=r.q''').fetchall()
    prefix_map={(v[0],int(v[1])):tuple(map(int,v[2:])) for v in prefix}
    assert len(prefix_map)==len(prefix)==len(requests)
    cursor=con.execute('''SELECT ticker,minute,
        ARG_MIN(price_x10000,struct_pack(t:=event_ms,id:=tran_id,ch:=channel)),MAX(price_x10000),MIN(price_x10000),
        ARG_MAX(price_x10000,struct_pack(t:=event_ms,id:=tran_id,ch:=channel)),SUM(volume),SUM(price_x10000::HUGEINT*volume::HUGEINT),
        COUNT(*),MIN(event_ms),MAX(event_ms),MAX(GREATEST(event_ms,local_ms)),
        ARG_MIN(tran_id,struct_pack(t:=event_ms,id:=tran_id,ch:=channel)),ARG_MAX(tran_id,struct_pack(t:=event_ms,id:=tran_id,ch:=channel))
        FROM native GROUP BY ticker,minute ORDER BY ticker,minute''')
    verified=[]

    def finish(ticker,actual):
        r=records[ticker];symbol=r['symbol'];rows=read_leaf(r);byminute={v['left_minute']:v for v in rows}
        source=p.Path(r['primary_source_path']);data=source.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['primary_source_sha256']
        primary=p.json.loads(data);day=next(dict(zip(primary['fields'],v)) for v in primary['rows'] if v[0]==DAY)
        daily_actual=[actual[min(actual)]['open'],max(v['high'] for v in actual.values()),min(v['low'] for v in actual.values()),actual[max(actual)]['close'],sum(v['volume'] for v in actual.values()),sum(v['amount_x10000'] for v in actual.values())]
        assert day['tradestatus']=='1' and daily_actual==[int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
        fields=['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','first_transaction_id','last_transaction_id','source_last_event_ms','source_dependency_ready_ms']
        grid=list(range(570,690))+list(range(780,900));running_qty=running_cash=0;cuts={}
        for minute in grid:
            if minute in actual:running_qty+=actual[minute]['volume'];running_cash+=actual[minute]['amount_x10000']
            else:cuts[minute]=(running_qty,running_cash)
        zeros={}
        for row in rows:
            m=row['left_minute'];assert row['symbol']==symbol and row['date']==DAY
            if row['trades']>0:
                assert row['source_kind']=='actual_native_millisecond_trades' and row['volume']>0
                assert all(row[f]==actual[m][f] for f in fields);counts['real_trade_minutes']+=1;continue
            assert m not in actual and row['source_kind']=='genuine_quote_in_verified_zero_trade_interval'
            assert row['volume']==row['amount_x10000']==row['trades']==0
            ev,loc,tid,ch,price,q,cash,ready,count=prefix_map[ticker,m]
            assert cuts[m]==(q,cash) and ev<(m+1)*60000
            assert all(row[f]==v for f,v in dict(source_prefix_last_event_ms=ev,source_prefix_last_local_ms=loc,
                source_prefix_last_transaction_id=tid,source_prefix_last_channel=ch,source_quote_last_price_x10000=price,
                source_quote_quantity=q,source_quote_amount_x10000=cash,source_prefix_ready_ms=ready,source_prefix_trade_count=count).items())
            assert all(row[f]==price for f in ['open','high','low','close'])
            assert row['source_last_event_ms']==ev and row['source_dependency_ready_ms']==max(ready,row['source_quote_received_ms'])
            assert m*60000<=row['source_quote_reported_ms']<(m+1)*60000
            zeros[m]=row;counts['exact_prefix_zero_quote_minutes']+=1
        leaf=source_quotes[symbol];qpath=p.Path(r['quote_source_path'])
        assert r['quote_source_sha256']==leaf['sha256'] and p.identity(qpath)==r['quote_source_identity']==leaf['verified_identity']
        vf='TradVolume' if symbol.endswith('SH') else 'Volume';seen=set()
        for batch in p.pq.ParquetFile(qpath).iter_batches(batch_size=p.BATCH_ROWS,columns=['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','Turnover',vf],use_threads=False):
            for quote in batch.to_pylist():
                t=clock(quote['UpdateTime']);m=t//60000
                if m not in zeros:continue
                z=zeros[m]
                if t!=z['source_quote_reported_ms'] or clock(quote['LocalTime'])!=z['source_quote_received_ms'] or str(quote['SeqNo'])!=z['source_quote_sequence']:continue
                assert quote['SecurityID']==ticker and D(str(quote[vf]))==z['source_quote_quantity']
                assert D(str(quote['Turnover']))*10000==z['source_quote_amount_x10000'] and D(str(quote['LastPrice']))*10000==z['source_quote_last_price_x10000']
                seen.add(m)
        assert seen==set(zeros) and p.identity(qpath)==leaf['verified_identity']
        for h in r['hours']:
            slot=h['slot'];assert not mask[offset+slot,calendar['union_symbols'].index(symbol)]
            block=[byminute[m] for m in grid[slot*60:(slot+1)*60]]
            value=dict(open=block[0]['open'],high=max(v['high'] for v in block),low=min(v['low'] for v in block),close=block[-1]['close'],
                volume=sum(v['volume'] for v in block),amount_x10000=sum(v['amount_x10000'] for v in block),
                source_event_ms_max=max(v['source_last_event_ms'] for v in block),source_ready_ms=max(v['source_dependency_ready_ms'] for v in block),
                actual_trade_minutes=sum(v['trades']>0 for v in block),genuine_zero_trade_quote_minutes=sum(v['trades']==0 for v in block))
            assert all(h[f]==v for f,v in value.items())
            assert all(value[f]==expected[symbol,slot][f] for f in ['open','high','low','close','volume'])
            counts['qualified_hours']+=1
        verified.append(dict(symbol=symbol,hours=len(r['hours']),daily_six_exact=True,quote_source_and_actual_prefix_verified=True))

    current=None;bars={}
    while batch:=cursor.fetchmany(4096):
        for row in batch:
            ticker,minute=row[:2]
            if ticker!=current:
                if current is not None:finish(current,bars)
                current=ticker;bars={}
            f=['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','source_dependency_ready_ms','first_transaction_id','last_transaction_id']
            b=dict(zip(f,map(int,row[2:])));b['source_last_event_ms']=b['last_ms'];bars[int(minute)]=b
    if current is not None:finish(current,bars)
    con.close()
    assert len(verified)==len(records) and counts['qualified_hours']==stage['hours_staged']
    assert counts['real_trade_minutes']+counts['exact_prefix_zero_quote_minutes']==stage['actual_minutes_staged']
    assert p.identity(artifact)==stage['accepted_artifact_identity']
    p.atomic_json(DEST/'independent_verification.json',dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),
        audited_receipt_sha256=p.sha(stage_path),counts=dict(counts),records=verified,stocks_verified=len(verified),
        independent_sql_native_candles_and_actual_trade_prefixes=True,observed_quote_fields_exact=True,all_primary_daily_six_exact=True,
        independent_tencent_hour_ohlcv_exact=True,receiver_dependencies_are_observed=True,source_live_clock_certificate=False,
        raw_candidate_hours_admitted=0,raw_candidate_modified=False,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    lock.close()


if __name__=='__main__':main()
