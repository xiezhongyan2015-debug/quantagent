"""Bind previously independent native audits to every actual V6 minimum gap."""
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_public_archives_20261009/wind17'
OUT = SOURCE / 'all_minimum_gap_consolidation'
NAME = 'backfilled_panel_candidate_canonical_ipo_v6'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'verified_minimum_gap_source_scope.json'
    assert not output.exists()
    pins = {}

    def read(relative, expected=None):
        path = SOURCE / relative
        digest = sha(path)
        if expected is not None:
            assert digest == expected, relative
        value = json.loads(path.read_text())
        if 'source_code_sha256' in value:
            pins[str(path)] = digest
        return value

    proofs = {
        '2018': read('all_2018_minimum_gaps/independent_all_2018_native_verification.json'),
        '2019_2022': read('remaining_2019_2022/independent_native_gap_verification.json'),
        '2023': read('independent_all_2023_gap_findings/independent_all_2023_findings_verification.json'),
        '2024_2026': read('remaining_late_years/independent_late_year_findings_verification.json'),
    }
    verifier_files = {
        '2018': 'verify_wind17_2018_native_findings.py',
        '2019_2022': 'verify_wind17_2019_2022_native_gaps.py',
        '2023': 'verify_wind17_all_2023_gap_findings.py',
        '2024_2026': 'verify_wind17_late_year_findings.py',
    }
    for batch, proof in proofs.items():
        assert proof['passed'] and proof['candidate_hours_admitted'] == 0
        path = ROOT / 'gru_beta_neutral_20261008' / verifier_files[batch]
        assert sha(path) == proof['source_code_sha256']
        pins[str(path)] = sha(path)

    p18 = proofs['2018']
    d18 = read('all_2018_minimum_gaps/all_2018_gap_audit_receipt.json', p18['daily_audit_sha256'])
    h18 = read('all_2018_minimum_gaps/all_native_missing_hours_receipt.json', p18['native_audit_sha256'])
    read('all_2018_minimum_gaps/bounded_coarse_eligible_days_receipt.json', p18['coarse_terminal_failure_sha256'])
    p19 = proofs['2019_2022']
    d19 = read('remaining_2019_2022/acquisition_and_daily_diagnostics_receipt.json', p19['acquisition_receipt_sha256'])
    p23 = proofs['2023']
    d23 = read('all_2023_minimum_gaps/all_2023_gap_audit_receipt.json', p23['audited_daily_receipt_sha256'])
    h23 = read('all_2023_hour_concordance/hour_concordance_receipt.json', p23['audited_hour_receipt_sha256'])
    p24 = proofs['2024_2026']
    d24 = read('remaining_late_years/acquisition_and_daily_diagnostics_receipt.json', p24['acquisition_daily_receipt_sha256'])
    h24 = read('remaining_late_years/late_year_hour_diagnostics_receipt.json', p24['hour_receipt_sha256'])

    dataset = BASE / NAME / 'dataset'
    meta_path = dataset / 'meta.json'
    meta = json.loads(meta_path.read_text())
    assert meta['data_fingerprint'] == p19['candidate_fingerprint']
    assert sha(meta_path) == p19['candidate_meta_sha256']
    assert sha(dataset / 'hour_complete.npy') == meta['files_sha256']['hour_complete.npy']
    complete = np.load(dataset / 'hour_complete.npy', mmap_mode='r')
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(calendar_path) == p19['calendar_sha256']
    calendar = json.loads(calendar_path.read_text())
    columns = {s: i for i, s in enumerate(calendar['union_symbols'])}
    date_indices = {d: i for i, d in enumerate(calendar['dates'])}
    gap_path = BASE / NAME / 'gap_audit/missing_stockdays.parquet'
    targets = {}
    for item in pq.read_table(gap_path).to_pylist():
        if item['minimum_requested'] and item['primary_tradestatus'] == '1' and item['primary_volume'] > 0:
            key = item['symbol'], item['date']
            assert key not in targets
            i = date_indices[item['date']] * 4
            slots = np.flatnonzero(~complete[i:i+4, columns[item['symbol']]]).tolist()
            assert len(slots) == item['missing_hours']
            targets[key] = slots
    assert len(targets) == 767 and sum(map(len, targets.values())) == 3042
    gap_receipt = json.loads((BASE / NAME / 'gap_audit/receipt.json').read_text())
    assert gap_receipt['minimum_intervals']['missing_primary_trading_hours'] == 3042
    assert gap_receipt['full_union_august_september']['missing_nonmember_primary_trading_hours'] == 84860

    raw_index = {}
    for batch, daily in [('2018', d18), ('2019_2022', d19), ('2023', d23), ('2024_2026', d24)]:
        for stock in daily['results']:
            if batch in ('2019_2022', '2024_2026'):
                assert stock['status'] == 'exact_CSV_acquired_and_daily_diagnostics_complete'
            assert sha(stock['raw_path']) == stock['raw_sha256']
            assert stock['member_CRC32_verified']
            for day in stock['target_stockday_checks']:
                key = stock['canonical_symbol'], day['date']
                assert key not in raw_index
                assert day.get('source_rows', 0) >= 240 and day['grid_valid']
                raw_index[key] = (batch, stock['raw_path'], stock['raw_sha256'], day)

    assert set(raw_index) == set(targets)
    h23_index = {(r['canonical_symbol'], r['date']): r for r in h23['results']}
    h24_index = {(r['symbol'], r['date']): r for r in h24['results']}
    records = {}

    def add(symbol, date, batch, slots, daily_slots, native_slots, coarse_checked, strict_slots):
        key = symbol, date
        assert key not in records and key in targets
        assert slots == targets[key], key
        assert set(strict_slots) <= set(native_slots) <= set(daily_slots) <= set(slots)
        source_batch, path, digest, _ = raw_index[key]
        assert source_batch == batch
        records[key] = {
            'symbol': symbol, 'date': date, 'batch': batch, 'actual_missing_slots': slots,
            'daily_and_selected_positive_pass_slots': daily_slots,
            'native_amount_pass_slots': native_slots,
            'independent_coarse_checked_native_slots': native_slots if coarse_checked else [],
            'strict_coarse_diagnostic_pass_slots': strict_slots,
            'source_date_observed': True, 'source_raw_path': path, 'source_raw_sha256': digest,
            'minute_quantity_precision_certified': False, 'candidate_hours_admitted': 0,
        }

    for r in h18['results']:
        add(r['symbol'], r['date'], '2018', r['actual_missing_slots'],
            r['positive_volume_missing_slots'], r['native_amount_pass_missing_slots'], False, [])
    for r in p19['records']:
        assert r['status'] == 'raw_native_daily_and_selected_hours_independently_verified'
        add(r['symbol'], r['date'], '2019_2022', r['actual_missing_slots'],
            r['selected_positive_daily_pass_slots'], r['native_pass_missing_slots'], False, [])
    for key, r in h23_index.items():
        day = raw_index[key][3]
        slots = targets[key]
        assert len(slots) == r['current_missing_hours'] and slots == [0, 1, 2, 3]
        daily_slots = slots if day['existing_daily_and_hour_gates_passed'] else []
        add(*key, '2023', slots, daily_slots, r.get('native_amount_pass_slots', []),
            r['status'] == 'strict_hour_checks_completed', r.get('strict_hours', []))
    for key, r in h24_index.items():
        day = raw_index[key][3]
        slots = targets[key]
        assert len(slots) == r['existing_missing_hours'] and slots == [0, 1, 2, 3]
        daily_slots = slots if day['existing_daily_and_hour_gates_passed'] else []
        add(*key, '2024_2026', slots, daily_slots, r.get('native_amount_pass_slots', []),
            r['status'] == 'strict_hour_diagnostics_complete', r.get('hours', []))
    assert set(records) == set(targets)

    hour_records = []
    batches = {}
    categories = Counter()
    for key, r in sorted(records.items()):
        for slot in r['actual_missing_slots']:
            daily = slot in r['daily_and_selected_positive_pass_slots']
            native = slot in r['native_amount_pass_slots']
            coarse = slot in r['independent_coarse_checked_native_slots']
            strict = slot in r['strict_coarse_diagnostic_pass_slots']
            category = ('daily_or_selected_positive_volume_failed' if not daily else
                        'native_minute_amount_failed' if not native else
                        'independent_coarse_not_tested' if not coarse else
                        'independent_coarse_concordance_failed' if not strict else
                        'strict_diagnostic_only_source_precision_uncertified')
            categories[category] += 1
            hour_records.append({'symbol': r['symbol'], 'date': r['date'], 'slot': slot,
                                 'batch': r['batch'], 'daily_and_positive_hour_pass': daily,
                                 'native_amount_pass': native, 'independent_coarse_checked': coarse,
                                 'strict_coarse_diagnostic_pass': strict, 'remaining_reason': category,
                                 'source_raw_path': r['source_raw_path'], 'source_raw_sha256': r['source_raw_sha256']})
    for batch in proofs:
        days = [r for r in records.values() if r['batch'] == batch]
        batches[batch] = {
            'stockyears': len({(r['symbol'], r['date'][:4]) for r in days}),
            'stockdays': len(days), 'actual_missing_hours': sum(len(r['actual_missing_slots']) for r in days),
            'daily_and_positive_hour_pass_hours': sum(len(r['daily_and_selected_positive_pass_slots']) for r in days),
            'native_amount_pass_hours': sum(len(r['native_amount_pass_slots']) for r in days),
            'coarse_checked_native_hours': sum(len(r['independent_coarse_checked_native_slots']) for r in days),
            'strict_diagnostic_only_hours': sum(len(r['strict_coarse_diagnostic_pass_slots']) for r in days),
        }
    expected = {
        '2018': (310, 1217, 148, 24), '2019_2022': (208, 829, 512, 16),
        '2023': (218, 872, 852, 58), '2024_2026': (31, 124, 108, 1),
    }
    for batch, counts in expected.items():
        observed = batches[batch]
        assert tuple(observed[k] for k in ('stockdays', 'actual_missing_hours', 'daily_and_positive_hour_pass_hours', 'native_amount_pass_hours')) == counts
    assert dict(categories) == {
        'daily_or_selected_positive_volume_failed': 1422, 'native_minute_amount_failed': 1521,
        'independent_coarse_not_tested': 40, 'independent_coarse_concordance_failed': 56,
        'strict_diagnostic_only_source_precision_uncertified': 3,
    }
    csv_path = OUT / 'every_actual_missing_hour_diagnostics.csv'
    with csv_path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(hour_records[0]))
        writer.writeheader(); writer.writerows(hour_records)
    result = {
        'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
        'source_code_sha256': sha(__file__), 'candidate_version': NAME,
        'candidate_meta_sha256': sha(meta_path), 'candidate_fingerprint': meta['data_fingerprint'],
        'current_actual_missing_mask_sha256': sha(dataset / 'hour_complete.npy'),
        'current_gap_stockdays_sha256': sha(gap_path), 'calendar_sha256': sha(calendar_path),
        'input_receipt_and_verifier_code_sha256': pins,
        'method': 'Fresh receipt/code chains and selected raw CSV digests; exact disjoint source-stockday set and every actual V6 missing slot. Native and coarse results are bound to previously independent byte/CRC/Decimal audits, not newly re-audited minute prices.',
        'unique_stocks': len({s for s, _ in records}), 'stockyears': sum(r['stockyears'] for r in batches.values()),
        'stockdays': len(records), 'actual_missing_hours': len(hour_records),
        'all_actual_minimum_gap_dates_have_observed_native_source_records': True,
        'daily_and_selected_positive_hour_pass_hours': 1620, 'native_amount_pass_hours': 99,
        'coarse_checked_native_hours': 59, 'coarse_not_tested_native_hours': 40,
        'strict_diagnostic_only_hours': 3, 'remaining_reason_hour_counts': dict(categories),
        'batches': batches, 'hour_diagnostics_csv_path': str(csv_path), 'hour_diagnostics_csv_sha256': sha(csv_path),
        'records': list(records.values()), 'candidate_changed': False, 'candidate_hours_admitted': 0,
        'minimum_missing_hours': 3042, 'full_union_nonmember_tail_missing_hours': 84860,
        'source_minute_quantity_precision_certified': False,
        'full_union_source_history_completed': False, 'production_ready': False,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ('records', 'input_receipt_and_verifier_code_sha256')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
