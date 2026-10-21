"""Exclude any vendor status/volume conflict from official coverage bounds."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/official_szse_suspension_chrome_20261009/conservative_window_audit'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    audit_path = OUT / 'combined_official_suspension_window_receipt.json'
    verify_path = OUT / 'independent_official_findings_verification.json'
    audit = json.loads(audit_path.read_text())
    verification = json.loads(verify_path.read_text())
    assert verification['passed'] and verification['audited_receipt_sha256'] == sha(audit_path)
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(calendar_path) == audit['calendar_sha256']
    calendar = json.loads(calendar_path.read_text())
    dates, symbols = calendar['dates'], calendar['union_symbols']
    mask_path = OUT / 'combined_official_unavoidable_hour_mask.npy'
    assert sha(mask_path) == audit['combined_official_mask_sha256']
    hours = np.load(mask_path)
    exclusions = []
    for check in audit['SZSE_primary_daily_checks']:
        assert sha(check['source_path']) == check['source_sha256']
        obj = json.loads(Path(check['source_path']).read_text())
        raw_rows = {row['date']: row for row in (dict(zip(obj['fields'], row)) for row in obj['rows'])}
        for date in check['dates_by_classification']['primary_status_or_nonzero_volume_conflicts']:
            day, stock = dates.index(date), symbols.index(check['symbol'])
            assert hours[day * 4: day * 4 + 4, stock].all()
            hours[day * 4: day * 4 + 4, stock] = False
            exclusions.append({'date': date, 'symbol': check['symbol'], 'primary_row': raw_rows[date],
                'primary_source_path': check['source_path'], 'primary_source_sha256': check['source_sha256'],
                'reason': 'Exclude disputed vendor volume/status day from conservative bound; official raw stop/resume evidence and vendor data remain unchanged'})
    assert len(exclusions) == audit['SZSE_primary_classification_counts']['primary_status_or_nonzero_volume_conflicts']
    # Independently evaluate with both rolling max and convolution.
    affected = pd.DataFrame(hours.astype('uint8')).rolling(80, min_periods=1).max().to_numpy().astype(bool)
    for column in np.flatnonzero(hours.any(0)):
        check = np.convolve(hours[:, column].astype(int), np.ones(80, dtype=int), mode='full')[:len(hours)] > 0
        np.testing.assert_array_equal(affected[:, column], check)
    ds = BASE / 'backfilled_panel_candidate_ipo_prefix_v5/dataset'
    assert sha(ds / 'members.npy') == audit['candidate_member_mask_sha256']
    members = np.load(ds / 'members.npy', mmap_mode='r')
    window = np.load(ds / 'window80_complete.npy', mmap_mode='r')
    assert not (affected & window).any()
    years = np.repeat([int(date[:4]) for date in dates], 4)
    rows = []
    for original in audit['rows']:
        use = years == original['year']
        denominator = int(members[use].sum())
        unavailable = int((members[use] & affected[use]).sum())
        ceiling = 1 - unavailable / denominator
        rows.append({**original, 'officially_evidenced_unavoidable_incomplete_member_windows': unavailable,
            'conservative_optimistic_coverage_ceiling': ceiling,
            'original_target_proven_unattainable_by_this_subset': ceiling < .995})
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
        'source_code_sha256': sha(Path(__file__)), 'audited_receipt_path': str(audit_path),
        'audited_receipt_sha256': sha(audit_path), 'independent_verification_receipt_sha256': sha(verify_path),
        'excluded_conflicted_stockdays': exclusions, 'rows': rows,
        'combined_official_stockdays_after_conflict_exclusion': int(hours.sum() // 4),
        'combined_official_stocks_after_conflict_exclusion': int(hours.any(0).sum()),
        'original_denominators_and_targets_preserved': True, 'independent_rolling_and_convolution_passed': True,
        'candidate_changed': False, 'prices_or_minutes_created': False, 'goal_complete': False}
    (OUT / 'conflicted_day_exclusion_sensitivity_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
