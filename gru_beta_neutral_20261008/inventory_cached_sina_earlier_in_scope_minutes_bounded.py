"""Find actual earlier in-scope cached independent M1, without network transfer."""
from collections import Counter,defaultdict
from . import bounded_tail_pipeline as p
DEST=p.OUT/'cached_sina_earlier_in_scope_minute_inventory'
def main():
    DEST.mkdir(exist_ok=False);cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());dates=set(cal['dates']);receipts=[];seen=set();records=[];counts=Counter()
    for name in ['sina_tail_minute_expanded','sina_tail_nonmonotonic_references','sina_remaining376_tail_references']:
        path=p.OUT/name/'receipt.json';proofpath=path.parent/'independent_verification.json';receipt=p.json.loads(path.read_text());proof=p.json.loads(proofpath.read_text());assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(path);receipts.append(dict(path=str(path),sha256=p.sha(path),proof_sha256=p.sha(proofpath)))
        for source in receipt['records']:
            symbol=source['symbol'];assert symbol not in seen;seen.add(symbol);qpath=p.Path(source['path']);assert p.identity(qpath)==source['verified_identity'];raw=qpath.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==source['sha256'];text=raw.decode();rows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);bydate=defaultdict(dict)
            for row in rows:
                date=row['day'][:10]
                if date not in dates or date>='2026-09-22':continue
                minute=int(row['day'][11:13])*60+int(row['day'][14:16])-1;assert minute not in bydate[date];bydate[date][minute]=row
            for date,bars in bydate.items():
                hours=[]
                for slot,start in enumerate([570,630,780,840]):
                    available=[m for m in range(start,start+60) if m in bars];positive=[m for m in available if p.np.float64(bars[m]['volume'])>0]
                    hours.append(dict(slot=slot,observed_minutes=len(available),positive_minutes=len(positive),all60_source_rows_observed=len(available)==60,all60_source_rows_positive=len(positive)==60));counts['source_observed_minutes']+=len(available);counts['all60_observed_hours']+=len(available)==60;counts['all60_positive_hours']+=len(positive)==60
                records.append(dict(symbol=symbol,date=date,source_path=str(qpath),source_sha256=source['sha256'],source_identity=source['verified_identity'],actual_earlier_observed_minute_rows=len(bars),hours=hours))
            assert p.identity(qpath)==source['verified_identity']
    assert len(seen)==624
    result=dict(status='complete_cached_earlier_in_scope_minute_inventory_no_admission',checked_utc=p.utc(),source_code_sha256=p.sha(__file__),source_receipt_pins=receipts,records=records,counts=dict(counts),symbols_checked=624,calendar_first=min(dates),calendar_last=max(dates),network_requests=0,archive_fingerprints_repeated=False,candidate_hours_admitted=0,raw_candidate_modified=False)
    p.atomic_json(DEST/'receipt.json',result);print(p.json.dumps({k:v for k,v in result.items() if k not in ['records','source_receipt_pins']},ensure_ascii=False))
if __name__=='__main__':main()
