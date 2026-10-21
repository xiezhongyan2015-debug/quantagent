"""Independent reconstruction from retained official raw pages, using pandas.

Does not import the source acquisition or suspension-window auditor.
"""
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/official_szse_suspension_chrome_20261009'
OUT = SOURCE / 'conservative_window_audit'
AUDIT = OUT / 'combined_official_suspension_window_receipt.json'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    receipt = json.loads(AUDIT.read_text())
    assert receipt['independent_convolution_all_columns_passed']
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_official_szse_suspension_windows.py') == receipt['source_code_sha256']
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(calendar_path) == receipt['calendar_sha256']
    calendar = json.loads(calendar_path.read_text())
    dates = pd.Index(calendar['dates'])
    symbols = calendar['union_symbols']
    raw_rows = {}
    pages_checked = 0
    for source in receipt['source_receipts']:
        assert sha(source['path']) == source['sha256']
        r = json.loads(Path(source['path']).read_text())
        assert r['status'] == 'all_requested_pages_saved'
        for page in r['pages']:
            assert sha(page['raw_path']) == page['raw_sha256']
            data = json.loads(Path(page['raw_path']).read_bytes())[0]['data']
            for row in data:
                raw_rows[json.dumps(row, sort_keys=True, ensure_ascii=False)] = row
            pages_checked += 1
    events = defaultdict(lambda: defaultdict(dict))
    for key, row in raw_rows.items():
        symbol = row['zqdm'] + '.SZ'
        if symbol not in symbols:
            continue
        for field, kind in [('tpkssj', 'stop'), ('fpkssj', 'resume')]:
            if row[field]:
                match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}) (开市|\d{2}:\d{2}:\d{2})', row[field])
                assert match
                day, clock = match.groups()
                ts = day + ' ' + ('09:30:00' if clock == '开市' else clock)
                events[symbol][ts].setdefault((kind, clock), []).append(row)
    expected = set()
    matrix = pd.DataFrame(False, index=dates, columns=symbols)
    for symbol, grouped in events.items():
        pending = None
        for ts, actions in sorted(grouped.items()):
            if len(actions) != 1:
                pending = None
                continue
            (kind, clock), rows = next(iter(actions.items()))
            if kind == 'stop' and clock == '开市':
                valid = any(row['tpsj'] == '停牌' or bool(row['fpkssj']) for row in rows)
                pending = ts[:10] if valid else None
            elif kind == 'resume' and clock == '开市':
                valid = any(row['tpsj'] == '取消停牌' or bool(row['tpkssj']) for row in rows)
                if pending and valid and pending < ts[:10]:
                    selection = (dates >= pending) & (dates < ts[:10])
                    if selection.any():
                        matrix.loc[selection, symbol] = True
                        expected.add((symbol, pending, ts[:10]))
                pending = None
            else:
                pending = None
    assert sha(receipt['evidence_path']) == receipt['evidence_sha256']
    evidence = json.loads(Path(receipt['evidence_path']).read_text())
    found = {(e['symbol'], e['stop_at_open_inclusive'], e['resume_at_open_exclusive']) for e in evidence}
    assert found == expected and len(found) == len(evidence)
    szse = np.repeat(matrix.to_numpy(), 4, axis=0)
    np.testing.assert_array_equal(szse, np.load(OUT / 'szse_official_unavoidable_hour_mask.npy'))
    sse = np.load(BASE / 'public_source_probes/official_sse_suspension_chrome_20261009/conservative_window_audit/official_unavoidable_hour_mask.npy')
    combined = sse | szse
    np.testing.assert_array_equal(combined, np.load(OUT / 'combined_official_unavoidable_hour_mask.npy'))
    # Different implementation: pandas rolling over the full matrix.
    affected = pd.DataFrame(combined.astype('uint8')).rolling(80, min_periods=1).max().to_numpy().astype(bool)
    ds = BASE / 'backfilled_panel_candidate_ipo_prefix_v5/dataset'
    for name, key in [('meta.json', 'candidate_meta_sha256'), ('members.npy', 'candidate_member_mask_sha256'),
                      ('hour_complete.npy', 'candidate_complete_mask_sha256')]:
        assert sha(ds / name) == receipt[key]
    members = np.load(ds / 'members.npy', mmap_mode='r')
    complete = np.load(ds / 'hour_complete.npy', mmap_mode='r')
    window = np.load(ds / 'window80_complete.npy', mmap_mode='r')
    assert not (combined & complete).any() and not (affected & window).any()
    daily_counts = Counter({key: 0 for key in ['primary_missing_rows',
        'primary_status_or_nonzero_volume_conflicts', 'status_0_volume_unreported', 'status_0_volume_explicit_0']})
    for check in receipt['SZSE_primary_daily_checks']:
        assert sha(check['source_path']) == check['source_sha256']
        obj = json.loads(Path(check['source_path']).read_text())
        primary = {row['date']: row for row in (dict(zip(obj['fields'], row)) for row in obj['rows'])}
        classified = {key: [] for key in daily_counts}
        for date in matrix.index[matrix[check['symbol']]]:
            row = primary.get(date)
            if not row or not row.get('code'):
                key = 'primary_missing_rows'
            else:
                try:
                    volume = float(row['volume'])
                except (ValueError, TypeError):
                    volume = float('nan')
                if row['tradestatus'] != '0' or (not np.isnan(volume) and volume != 0):
                    key = 'primary_status_or_nonzero_volume_conflicts'
                elif np.isnan(volume):
                    key = 'status_0_volume_unreported'
                else:
                    key = 'status_0_volume_explicit_0'
            classified[key].append(date)
            daily_counts[key] += 1
        assert classified == check['dates_by_classification']
    assert dict(daily_counts) == receipt['SZSE_primary_classification_counts']
    years = np.repeat([int(day[:4]) for day in dates], 4)
    for row in receipt['rows']:
        year = years == row['year']
        den = int(members[year].sum())
        hit = int((affected[year] & members[year]).sum())
        assert den == row['unchanged_original_member_stockhour_denominator']
        assert hit == row['officially_evidenced_unavoidable_incomplete_member_windows']
        assert 1 - hit / den == row['conservative_optimistic_coverage_ceiling']
        assert (1 - hit / den < .995) == row['original_target_proven_unattainable_by_this_subset']
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
        'source_code_sha256': sha(Path(__file__)), 'audited_receipt_sha256': sha(AUDIT),
        'all_raw_pages_fresh_sha256_checked': pages_checked, 'independently_reconstructed_closed_intervals': len(expected),
        'SZSE_stockdays': int(matrix.to_numpy().sum()), 'combined_stockdays': int(combined.sum() // 4),
        'independent_chronological_state_machine_and_pandas_rolling_passed': True,
        'independent_primary_daily_classification_counts': dict(daily_counts),
        'original_member_denominators_preserved': True, 'candidate_changed': False, 'new_hours_admitted': 0,
        'rows': receipt['rows']}
    (OUT / 'independent_official_findings_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
