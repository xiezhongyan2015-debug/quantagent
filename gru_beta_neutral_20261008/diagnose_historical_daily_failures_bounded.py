"""Separate cached daily failures without rescanning any historical year CSV."""
from collections import Counter
from . import bounded_tail_pipeline as p

SOURCE=p.BASE/'public_source_probes/additional_public_archives_20261009/wind17'
DEST=p.OUT/'historical_daily_failure_definition_diagnostic'


def main():
    lock=p.locked('diagnose_historical_daily_failures');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    scope_path=SOURCE/'all_minimum_gap_consolidation/verified_minimum_gap_source_scope.json';scope=p.json.loads(scope_path.read_text());assert scope['passed'] and scope['actual_missing_hours']==3042
    requested={(r['symbol'],r['date']):r for r in scope['records'] if set(r['actual_missing_slots'])-set(r['daily_and_selected_positive_pass_slots'])}
    pins=scope['input_receipt_and_verifier_code_sha256'];days={};source_pins=[]
    for relative in ['all_2018_minimum_gaps/all_2018_gap_audit_receipt.json','remaining_2019_2022/acquisition_and_daily_diagnostics_receipt.json','all_2023_minimum_gaps/all_2023_gap_audit_receipt.json','remaining_late_years/acquisition_and_daily_diagnostics_receipt.json']:
        path=SOURCE/relative;assert p.sha(path)==pins[str(path)];data=p.json.loads(path.read_text());source_pins.append(dict(path=str(path),sha256=p.sha(path)))
        for r in data['results']:
            for d in r['target_stockday_checks']:
                key=r['canonical_symbol'],d['date'];assert key not in days
                days[key]=(r,d)
    assert len(days)==767 and set(requested)<=set(days)
    fields=['source_rows_valid','grid_valid','primary_day_trading','open_agrees','close_agrees','volume_difference_within_100_shares','quotes_inside_primary_range','all_four_hours_positive_volume']
    counts=Counter();combinations=Counter();records=[]
    for key,r in sorted(requested.items()):
        stock,d=days[key];slots=sorted(set(r['actual_missing_slots'])-set(r['daily_and_selected_positive_pass_slots']));failed=[f for f in fields if not d[f]]
        assert failed and stock['raw_path']==r['source_raw_path'] and stock['raw_sha256']==r['source_raw_sha256']
        for f in failed:counts[f]+=len(slots)
        combinations['+'.join(failed)]+=len(slots)
        records.append(dict(symbol=key[0],date=key[1],missing_slots=slots,batch=r['batch'],failed_daily_fields=failed,source_raw_path=stock['raw_path'],source_raw_sha256=stock['raw_sha256'],
            observed_raw_ohlc=d['observed_raw_ohlc'],observed_volume_lots_times_100_shares=d['observed_volume_lots_times_100_shares'],volume_difference_shares=d['volume_difference_shares'],
            positive_0930_source_bar_merged=d['positive_0930_source_bar_merged'],observed_regular_minutes=d['observed_regular_minutes'],source_rows=d['source_rows'],
            primary_daily_path=stock['primary_daily_path'],primary_daily_sha256=stock['primary_daily_sha256'],unchanged_original_daily_contract_passed=False))
    assert sum(len(r['missing_slots']) for r in records)==1422
    p.atomic_json(output,dict(status='complete_cached1422_daily_failure_diagnostic_no_admission',started_and_finished_utc=p.utc(),source_code_sha256=p.sha(__file__),source_scope_sha256=p.sha(scope_path),source_receipt_pins=source_pins,
        stockdays_checked=len(records),missing_hours_checked=1422,field_failure_hours=dict(counts),failure_combination_hours=dict(combinations),records=records,
        source_year_csv_files_reopened=0,source_whole_file_fingerprints_repeated=0,daily_or_volume_gate_relaxed=False,candidate_hours_admitted=0,raw_candidate_modified=False,
        true_suspension_promoted=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss));lock.close()


if __name__=='__main__':main()
