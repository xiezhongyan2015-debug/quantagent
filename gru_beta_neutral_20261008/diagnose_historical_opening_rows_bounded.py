"""Inspect source09:30/09:31 rows for the16 cached open-only failures.

Keep actual observed rows and quantities; a zero-volume opening quote never
becomes a fabricated auction trade or a patched minute open.
"""
import csv
import io
from collections import defaultdict,Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .recheck_historical_quote_candidates import DigestReader,normalize_rows

DEST=p.OUT/'historical_opening_row_diagnostic'


def main():
    lock=p.locked('diagnose_historical_opening_rows');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    source=p.OUT/'historical_daily_failure_definition_diagnostic/receipt.json';fail=p.json.loads(source.read_text());assert fail['status']=='complete_cached1422_daily_failure_diagnostic_no_admission'
    wanted=[r for r in fail['records'] if r['failed_daily_fields']==['open_agrees']];assert len(wanted)==16 and sum(len(r['missing_slots']) for r in wanted)==64
    groups=defaultdict(list)
    for r in wanted:groups[r['source_raw_path']].append(r)
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),parent_diagnostic_sha256=p.sha(source),status='reading16_open_only_failure_days',requested_hours=64,requested_stockdays=16,native_files=[],records=[],counts={},candidate_hours_admitted=0,raw_candidate_modified=False,zero_volume_quote_not_relabelled_auction_trade=True,daily_gate_relaxed=False)
    def save():result.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
    save();counts=Counter()
    for filename,requests in sorted(groups.items()):
        path=p.Path(filename);identity=p.identity(path);expected={r['source_raw_sha256'] for r in requests};assert len(expected)==1;selected={r['date']:[] for r in requests};raw=DigestReader(path)
        with io.TextIOWrapper(io.BufferedReader(raw,1<<20),encoding='gbk',newline='') as stream:
            for row in csv.DictReader(stream):
                date=row['k线结束时间'][:10]
                if date in selected:selected[date].append(row)
            digest,count=raw.digest.hexdigest(),raw.count
        assert digest in expected and p.identity(path)==identity and count==identity['size'];result['native_files'].append(dict(path=filename,verified_identity=identity,sha256=digest,hash_during_useful_filtered_read=True,bytes=count))
        for r in requests:
            date,symbol=r['date'],r['symbol'];rows=selected[date];assert len(rows)<=242;bars=normalize_rows(rows);primary_path=p.Path(r['primary_daily_path']);data=primary_path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['primary_daily_sha256'];primary=p.json.loads(data);day=next(dict(zip(primary['fields'],v)) for v in primary['rows'] if v[0]==date)
            opening=[q for q in rows if q['k线结束时间'][11:16]=='09:30'];first=[q for q in rows if q['k线结束时间'][11:16]=='09:31'];assert len(opening)<=1 and len(first)==1
            zero=bool(opening and D(opening[0]['成交量'])==0);opening_matches=bool(opening and D(opening[0]['开盘价'])==D(day['open']));closing_matches=bool(opening and D(opening[0]['收盘价'])==D(day['open']))
            assert bars[0]['open']!=D(day['open']);leaf=DEST/(symbol+'_'+date+'_original_native.json');p.atomic_json(leaf,dict(symbol=symbol,date=date,native_source_path=filename,native_source_sha256=digest,raw_selected_rows=rows,normalized_bars=[{f:str(v) for f,v in b.items()} for b in bars]))
            observation=dict(symbol=symbol,date=date,slots=r['missing_slots'],primary_open=day['open'],primary_preclose=day['preclose'],original_normalized_first_open=str(bars[0]['open']),source0930_rows=opening,source0931_rows=first,
                source0930_zero_volume=zero,source0930_open_matches_primary_open=opening_matches,source0930_close_matches_primary_open=closing_matches,
                source0930_close_matches_primary_preclose=bool(opening and D(opening[0]['收盘价'])==D(day['preclose'])),
                native_selected_path=str(leaf),native_selected_sha256=p.sha(leaf),primary_path=str(primary_path),primary_sha256=r['primary_daily_sha256'],candidate_hours_admitted=0)
            result['records'].append(observation);counts['zero_volume0930_days']+=int(zero);counts['observed0930_open_exact_primary_open_days']+=int(opening_matches);counts['observed0930_close_exact_primary_open_days']+=int(closing_matches);result['counts']=dict(counts);save()
    result.update(status='complete16_open_only_source_diagnostic_no_admission',finished_utc=p.utc(),year_files_usefully_read=len(groups),whole_file_prefingerprints_repeated=0);save();lock.close()


if __name__=='__main__':main()
