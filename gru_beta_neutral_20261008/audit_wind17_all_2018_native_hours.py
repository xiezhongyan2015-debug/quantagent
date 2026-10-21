"""Complete all native-hour checks even when the coarse service is unavailable."""
import json
from datetime import datetime, timezone
from pathlib import Path
from verify_wind17_all_2023_gap_findings import ROOT, SOURCE, sha, normalized_day, FIELDS
import pandas as pd

OUT = SOURCE / 'all_2018_minimum_gaps'
DAY_FLAGS = ['source_rows_valid', 'grid_valid', 'primary_day_trading', 'open_agrees', 'close_agrees',
             'volume_difference_within_100_shares', 'quotes_inside_primary_range']


def main():
    ap = OUT / 'all_2018_gap_audit_receipt.json'
    path = OUT / 'all_native_missing_hours_receipt.json'
    assert not path.exists()
    audit = json.loads(ap.read_text())
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'daily_audit_path': str(ap), 'daily_audit_sha256': sha(ap),
               'normalization_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/verify_wind17_all_2023_gap_findings.py'),
               'results': [], 'candidate_changed': False, 'candidate_hours_admitted': 0,
               'reported_minute_quantity_precision_certified': False, 'network_queries_made': 0}
    for item in audit['results']:
        assert sha(item['raw_path']) == item['raw_sha256']
        frame = pd.read_csv(item['raw_path'], encoding='gbk', dtype=str).rename(columns=FIELDS)
        frame.index = pd.to_datetime(frame['k线结束时间'])
        for info in item['target_stockday_checks']:
            date, missing = info['date'], info['actual_missing_slots']
            record = {'symbol': item['canonical_symbol'], 'date': date, 'actual_missing_slots': missing,
                      'daily_source_gate_passed': all(info.get(k) for k in DAY_FLAGS)}
            if not record['daily_source_gate_passed']:
                record.update(status='failed_daily_gate', positive_volume_missing_slots=[], native_amount_pass_missing_slots=[])
            else:
                _, reg, _ = normalized_day(frame, date)
                good = [r.low*r.volume - 1 <= r.amount <= r.high*r.volume + 1 for r in reg.itertuples()]
                positive = [s for s in missing if sum(reg.iloc[s*60:(s+1)*60].volume) > 0]
                eligible = [s for s in positive if all(good[s*60:(s+1)*60])]
                record.update(status='native_missing_hour_checks_complete',
                              positive_volume_missing_slots=positive, native_amount_pass_missing_slots=eligible,
                              native_amount_conflict_minutes=good.count(False))
            receipt['results'].append(record)
    receipt.update(stockdays_examined=len(receipt['results']),
                   actual_missing_hours=sum(len(r['actual_missing_slots']) for r in receipt['results']),
                   daily_and_selected_hour_positive_pass_hours=sum(len(r['positive_volume_missing_slots']) for r in receipt['results']),
                   native_positive_volume_and_amount_pass_hours=sum(len(r['native_amount_pass_missing_slots']) for r in receipt['results']))
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'results'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
