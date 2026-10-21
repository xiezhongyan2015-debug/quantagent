"""Independent SQL aggregation and exact public response string comparison."""
from datetime import datetime
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
import duckdb


def main():
    lock=p.locked('verify_tail_tencent_hours')
    directory=p.OUT/'tencent_minute_references'
    audit_path=directory/'all_tail_hour_comparison.json'
    audit=p.json.loads(audit_path.read_text())
    acquisition_path=directory/'all_target_stock_acquisition.json'
    acquisition=p.json.loads(acquisition_path.read_text())
    assert audit['status']==acquisition['status']=='complete'
    assert p.sha(acquisition_path)==audit['source_acquisition_sha256']
    assert p.sha(p.ROOT/'gru_beta_neutral_20261008/compare_tail_tencent_hours.py')==audit['source_code_sha256']
    wanted=p.targets()
    needed={}
    for (date,prefix),group in wanted.items():
        for target in group.values():
            needed.setdefault(target['symbol'],set()).add(date)
    refs={}
    for record in acquisition['records']:
        path=p.Path(record['path'])
        assert p.identity(path)==record['verified_identity']
        raw=path.read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==record['sha256']
        obj=p.json.loads(raw)
        rows=obj['data'][record['code']]['m60']
        assert len(rows)==record['rows']
        symbol=record['symbol']
        for row in rows:
            date=f'{row[0][:4]}-{row[0][4:6]}-{row[0][6:8]}'
            if date not in needed[symbol]:
                continue
            slot=['1030','1130','1400','1500'].index(row[0][-4:])
            refs[symbol,date,slot]={f:D(row[i])*10000 for f,i in [('open',1),('close',2),('high',3),('low',4)]}
            refs[symbol,date,slot]['volume']=D(row[5])*100
            assert refs[symbol,date,slot]['volume']==refs[symbol,date,slot]['volume'].to_integral_value()
    minute=p.json.loads((p.OUT/'minute_observation_progress.json').read_text())
    assert minute['status']=='observations_complete_pending_independent_concordance'
    stamp=int(datetime.fromisoformat(minute['updated_utc']).timestamp()*1e9)
    by_group={(g['date'],g['prefix']):g for g in minute['groups']}
    con=duckdb.connect()
    con.execute("SET memory_limit='128MB'")
    con.execute('SET threads=1')
    checked_hours,unique_days,source_files=0,set(),0
    for group in audit['groups']:
        key=group['date'],group['prefix']
        leaf_path=p.Path(group['comparison_path'])
        raw=leaf_path.read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==group['comparison_sha256']
        comparisons=p.json.loads(raw)['comparisons']
        indexed={(r['model'],r['symbol'],r['slot']):r for r in comparisons}
        expected_models=by_group[key]['minute_files']
        for record in expected_models:
            path=p.Path(record['path'])
            identity=p.identity(path)
            assert identity['mtime_ns']<=stamp and identity['ctime_ns']<=stamp
            footer=p.pq.ParquetFile(path)
            assert footer.metadata.num_rows==record['observed_minutes']
            cursor=con.execute('SELECT ticker,CASE WHEN left_minute<690 THEN (left_minute-570)//60 ELSE (left_minute-780)//60+2 END AS slot,ARG_MIN(open,left_minute),MAX(high),MIN(low),ARG_MAX(close,left_minute),SUM(volume),COUNT(*),MIN(left_minute),MAX(left_minute),COUNT(DISTINCT left_minute) FROM read_parquet(?,hive_partitioning=false) GROUP BY ticker,slot',[str(path)])
            fields=['open','high','low','close','volume','observed_minutes','first_minute','last_minute','distinct_minutes']
            native={(r[0],int(r[1])):dict(zip(fields,r[2:])) for r in cursor.fetchall()}
            assert p.identity(path)==identity
            source_files+=1
            counts={f:0 for f in group['totals'][record['model']]}
            for ticker,target in wanted[key].items():
                symbol=target['symbol']
                unique_days.add((symbol,key[0]))
                for slot in range(4):
                    row=indexed[record['model'],symbol,slot]
                    bar=native.get((ticker,slot))
                    ref=refs.get((symbol,key[0],slot))
                    assert row['source_trade_hour_present']==(bar is not None)
                    assert row['coarse_reference_present']==(ref is not None)
                    counts['reference_hours']+=ref is not None
                    counts['observed_trade_hours']+=bar is not None
                    if bar:
                        assert bar['distinct_minutes']==bar['observed_minutes']
                        counts['all_60_trade_minutes_observed_hours']+=bar['observed_minutes']==60
                    if bar and ref:
                        diff={f:int(D(bar[f])-ref[f]) for f in ref}
                        assert diff==row['differences']
                        for f in fields[:-1]:
                            assert int(bar[f])==row['native_hour'][f]
                        prices=all(diff[f]==0 for f in ['open','high','low','close'])
                        assert prices==row['four_prices_exact']
                        counts['all_four_prices_exact_hours']+=prices
                        counts['all_four_prices_and_volume_exact_hours']+=prices and diff['volume']==0
                        counts['all_four_prices_and_volume_within_100_hours']+=prices and abs(diff['volume'])<=100
                    checked_hours+=1
            assert counts==group['totals'][record['model']]
    assert len(unique_days)==21215 and checked_hours==84860*2 and source_files==272
    proof=dict(checked_utc=p.utc(),passed=True,source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(audit_path),
        source_public_response_count=len(acquisition['records']),source_response_bytes=sum(r['bytes'] for r in acquisition['records']),
        stockdays_checked=len(unique_days),both_fixed_clock_hour_comparisons_reproduced=checked_hours,
        minute_summary_files_scanned_once=source_files,large_trade_archives_not_reopened=True,
        minute_summary_fingerprints_reused_with_ctime_mtime_and_footer_guards=True,
        volume_times_100_is_still_diagnostic_hypothesis=True,turnover_rate_not_used_as_cash=True,
        all_preexisting_clock_and_source_precision_failures_retained=True,candidate_hours_admitted=0,
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(directory/'independent_hour_comparison_verification.json',proof)
    con.close()
    lock.close()


if __name__=='__main__':
    main()
