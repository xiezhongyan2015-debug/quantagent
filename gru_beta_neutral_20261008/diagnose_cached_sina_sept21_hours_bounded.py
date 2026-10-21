"""Check actual earlier cached M1 against existing canonical native leaves."""
from decimal import Decimal as D
from collections import Counter,defaultdict
from . import bounded_tail_pipeline as p
DEST=p.OUT/'cached_sina_sept21_actual_hour_comparison'
RAW=p.BASE/'backfilled_panel_candidate_native_actual_quantity_v25_raw'
def main():
    DEST.mkdir(exist_ok=True);assert not (DEST/'receipt.json').exists();path=p.OUT/'cached_sina_earlier_in_scope_minute_inventory/receipt.json';inventory=p.json.loads(path.read_text());requests=[dict(r,slot=h['slot']) for r in inventory['records'] for h in r['hours'] if h['all60_source_rows_positive']];assert len(requests)==7
    progresspath=p.OUT/'minute_observation_progress.json';progress=p.json.loads(progresspath.read_text());leaves={(g['date'],g['prefix']):g for g in progress['groups']};comparisonpath=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';comparison=p.json.loads(comparisonpath.read_text());proofpath=comparisonpath.parent/'independent_hour_comparison_verification.json';proof=p.json.loads(proofpath.read_text());assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(comparisonpath);coarse={}
    for g in comparison['groups']:
        if g['date']!='2026-09-21':continue
        data=p.Path(g['comparison_path']).read_bytes();assert p.hashlib.sha256(data).hexdigest()==g['comparison_sha256']
        for r in p.json.loads(data)['comparisons']:
            if r['model']=='floor_left':coarse[r['date'],r['symbol'],r['slot']]=r
    auditpath=RAW/'raw_full_history_audit.json';audit=p.json.loads(auditpath.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json');pin=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy')==pin;mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());groups=defaultdict(list)
    for r in requests:groups[r['date'],r['symbol'][:2]].append(r)
    records=[];sourcepins=[];counts=Counter()
    for key,rs in groups.items():
        source=leaves[key];leaf=next(x for x in source['minute_files'] if x['model']=='floor_left');mpath=p.Path(leaf['path']);identity=p.identity(mpath);data=mpath.read_bytes();assert p.hashlib.sha256(data).hexdigest()==leaf['sha256'];frame=p.pq.ParquetFile(p.pa.BufferReader(data)).read().to_pandas();vpath=p.Path(source['validation_path']);assert p.sha(vpath)==source['validation_sha256'];validation=p.json.loads(vpath.read_text());sourcepins.append(dict(path=str(mpath),sha256=leaf['sha256'],verified_identity=identity,validation_path=str(vpath),validation_sha256=source['validation_sha256']));
        for request in rs:
            symbol=request['symbol'];date=request['date'];slot=request['slot'];qpath=p.Path(request['source_path']);assert p.identity(qpath)==request['source_identity'];raw=qpath.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==request['source_sha256'];text=raw.decode();qrows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);quotes={int(q['day'][11:13])*60+int(q['day'][14:16])-1:q for q in qrows if q['day'][:10]==date};stock=frame.loc[frame.ticker.eq(symbol[:6])].set_index('left_minute');start=[570,630,780,840][slot];grid=list(range(start,start+60));absent=[m for m in grid if m not in stock.index];conflicts=[]
            for m in grid:
                if m not in stock.index:continue
                n=stock.loc[m];q=quotes[m]
                if any(D(int(n[f]))!=D(q[f])*10000 for f in ['open','high','low','close']) or D(int(n['volume']))!=D(q['volume']):conflicts.append(m)
            native=None;ref=None;diff=None;carry=None;carryexact=False
            if not absent:
                block=stock.loc[grid];native=dict(open=int(block.open.iloc[0]),high=int(block.high.max()),low=int(block.low.min()),close=int(block.close.iloc[-1]),volume=int(block.volume.sum()));c=coarse[date,symbol,slot];assert all(native[f]==c['native_hour'][f] for f in native);ref={f:native[f]-c['differences'][f] for f in native};diff=c['differences'];prior=stock.loc[stock.index<start];price=int(prior.close.iloc[-1]) if len(prior) else native['open'];carry=dict(native,open=price,high=max(price,native['high']),low=min(price,native['low']));pm=int(prior.index[-1]) if len(prior) else None;carryexact=pm in quotes and D(quotes[pm]['close'])*10000==price
            already=bool(mask[cal['dates'].index(date)*4+slot,cal['union_symbols'].index(symbol)]);status='actual_minutes_conflict' if absent or conflicts else 'already_admitted' if already else 'direct_coarse_exact_minutes_pending_source_arrival_policy' if native==ref else 'carry_coarse_exact_minutes_pending_source_arrival_policy' if carry==ref and carryexact else 'coarse_conflict';counts[status]+=1
            records.append(dict(date=date,symbol=symbol,slot=slot,status=status,native_minute_absent=absent,actual_minute_conflicts=conflicts,actual60_ohlcv_exact=not absent and not conflicts,native_hour=native,coarse_reference=ref,coarse_differences=diff,fixed_previous_close_model=carry,previous_close_independent_exact=carryexact,source_sina_path=str(qpath),source_sina_sha256=request['source_sha256'],native_minute_path=str(mpath),native_minute_sha256=leaf['sha256'],native_minute_identity=identity,raw_already_admitted=already,actual_source_arrival_certified=False))
        assert p.identity(mpath)==identity
    result=dict(status='complete_cached_sept21_minute_comparison_no_admission',checked_utc=p.utc(),source_code_sha256=p.sha(__file__),inventory_sha256=p.sha(path),minute_observation_progress_sha256=p.sha(progresspath),raw_audit_sha256=p.sha(auditpath),coarse_comparison_sha256=p.sha(comparisonpath),coarse_independent_proof_sha256=p.sha(proofpath),native_minute_source_pins=sourcepins,records=records,counts=dict(counts),network_requests=0,candidate_hours_admitted=0,raw_candidate_modified=False,original_archives_reopened=False)
    p.atomic_json(DEST/'receipt.json',result);print(p.json.dumps({k:v for k,v in result.items() if k not in ['records','native_minute_source_pins']},ensure_ascii=False))
if __name__=='__main__':main()
