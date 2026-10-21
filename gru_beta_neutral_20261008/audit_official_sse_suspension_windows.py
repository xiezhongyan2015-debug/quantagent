"""Conservative official-event lower bound on unavoidable 80-hour gaps.

Only explicit whole-day events and strictly interior days of closed continuous
intervals are used. Boundaries, partial days, open-ended and unclear events are
not inferred. This is a retrospective audit, not a PIT signal or an exemption.
"""
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
SOURCE = BASE / 'public_source_probes/official_sse_suspension_chrome_20261009'
OUT = SOURCE / 'conservative_window_audit'
CANDIDATE = BASE / 'backfilled_panel_candidate_ipo_prefix_v5'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    calendar = json.loads(calendar_path.read_text())
    dates = np.array(calendar['dates'])
    symbols = calendar['union_symbols']
    symbol_index = {s: i for i, s in enumerate(symbols)}
    hits = np.zeros((len(dates), len(symbols)), dtype=bool)
    source_receipts, unique = [], {}
    for year in ['2019', '2020', '2021', '2022']:
        interval = year + ('1001' if year == '2019' else '0101') + '_' + year + '1231'
        path = SOURCE / ('history_' + interval) / 'complete_source_receipt.json'
        receipt = json.loads(path.read_text())
        assert receipt['status'] == 'all_requested_pages_saved' and receipt['TLS_validation_preserved']
        assert not receipt['personal_credentials_used']
        assert sha(receipt['events_path']) == receipt['events_sha256']
        reconstructed = []
        for page in receipt['pages']:
            assert sha(page['raw_path']) == page['raw_sha256']
            raw = Path(page['raw_path']).read_text()
            prefix, tail = raw.split('(', 1)
            obj, stop = json.JSONDecoder().raw_decode(tail)
            assert tail[stop:].strip() in [')', ');']
            assert obj['pageHelp']['pageNo'] == page['page']
            assert obj['result'] == obj['pageHelp']['data']
            reconstructed.extend(obj['result'])
            for position, event in enumerate(obj['result']):
                key = json.dumps(event, sort_keys=True, ensure_ascii=False)
                if key not in unique:
                    unique[key] = {'event': event, 'source_occurrences': []}
                unique[key]['source_occurrences'].append({'page_raw_path': page['raw_path'],
                                                         'page_raw_sha256': page['raw_sha256'],
                                                         'row_index_zero_based': position})
        assert reconstructed == json.loads(Path(receipt['events_path']).read_text())
        assert len(reconstructed) == receipt['reported_total_events']
        source_receipts.append({'path': str(path), 'sha256': sha(path), 'events': len(reconstructed)})
    evidence, exclusions = [], {}
    for entry in unique.values():
        event = entry['event']
        symbol = event['productCode'] + '.SH'
        if symbol not in symbol_index:
            reason = 'outside_fixed_1178_stock_union'
        else:
            started, ended = event['startStopDate'], event['endStopDate']
            start = pd.Timestamp(started).strftime('%Y-%m-%d')
            if event['type'] == 'LSTP' and event['stopTime'] == 'WH' and ended:
                end = pd.Timestamp(ended).strftime('%Y-%m-%d')
                assert end >= start
                days = (dates >= start) & (dates <= end)
                rule = 'explicit_whole_day_official_event'
            elif event['type'] == 'LXTP' and ended:
                end = pd.Timestamp(ended).strftime('%Y-%m-%d')
                assert end >= start
                days = (dates > start) & (dates < end)
                rule = 'strictly_interior_trading_days_of_closed_continuous_interval'
            else:
                days = np.zeros(len(dates), bool)
                rule = 'partial_day_open_ended_or_unclear_event_not_used'
            if days.any():
                hits[:, symbol_index[symbol]] |= days
                evidence.append({**entry, 'symbol': symbol, 'admission_rule': rule,
                                 'declared_unavoidable_dates': dates[days].tolist()})
                continue
            reason = rule if rule.startswith('partial') else 'no_strictly_interior_calendar_days'
        exclusions[reason] = exclusions.get(reason, 0) + 1
    ds = CANDIDATE / 'dataset'
    members = np.load(ds / 'members.npy', mmap_mode='r')
    complete = np.load(ds / 'hour_complete.npy', mmap_mode='r')
    window = np.load(ds / 'window80_complete.npy', mmap_mode='r')
    assert members.shape == complete.shape == window.shape == (len(dates) * 4, len(symbols))
    hour_hits = np.repeat(hits, 4, axis=0)
    prefix = np.vstack([np.zeros((1, len(symbols)), np.int32), np.cumsum(hour_hits, axis=0, dtype=np.int32)])
    count = prefix[1:] - prefix[np.maximum(np.arange(len(hour_hits)) - 79, 0)]
    affected = count > 0
    # Separately reconstruct every active column with linear convolution.
    for index in np.flatnonzero(hits.any(axis=0)):
        independent = np.convolve(hour_hits[:, index].astype(int), np.ones(80, dtype=int), mode='full')[:len(hour_hits)] > 0
        np.testing.assert_array_equal(affected[:, index], independent)
    conflicts = np.argwhere(hour_hits & complete)
    conflict_details = [{'date': dates[row // 4], 'slot': int(row % 4), 'symbol': symbols[column]}
                        for row, column in conflicts]
    assert not conflict_details, conflict_details[:10]
    assert not (affected & window).any()
    primary_checks = []
    primary = BASE / 'public_source_probes/baostock/union_daily_factors/normalized'
    for index in np.flatnonzero(hits.any(axis=0)):
        path = primary / (symbols[index] + '.json')
        obj = json.loads(path.read_text())
        daily = pd.DataFrame(obj['rows'], columns=obj['fields']).set_index('date').reindex(dates[hits[:, index]])
        inconsistent = daily.code.isna() | daily.tradestatus.ne('0') | pd.to_numeric(daily.volume).ne(0)
        primary_checks.append({'symbol': symbols[index], 'daily_source_path': str(path), 'daily_source_sha256': sha(path),
                               'official_unavoidable_days': int(hits[:, index].sum()),
                               'daily_status_or_volume_disagreement_dates': daily.index[inconsistent].tolist()})
    years = np.repeat(np.array([int(d[:4]) for d in dates]), 4)
    rows = []
    for year in [2020, 2021, 2022]:
        use = years == year
        denominator = int(members[use].sum())
        unavoidable = int((affected[use] & members[use]).sum())
        rows.append({'year': year, 'unchanged_original_member_stockhour_denominator': denominator,
                     'officially_evidenced_unavoidable_incomplete_member_windows': unavoidable,
                     'conservative_optimistic_coverage_ceiling': 1 - unavoidable / denominator,
                     'original_target': .995, 'original_target_proven_unattainable_by_this_subset':
                     1 - unavoidable / denominator < .995,
                     'actual_candidate_coverage': float((window[use] & members[use]).sum() / denominator)})
    np.save(OUT / 'official_unavoidable_hour_mask.npy', hour_hits)
    (OUT / 'admitted_official_event_evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'status': 'conservative_official_subset_audit_complete',
              'source_code_sha256': sha(Path(__file__)), 'source_receipts': source_receipts,
              'calendar_sha256': sha(calendar_path), 'candidate_meta_sha256': sha(ds / 'meta.json'),
              'candidate_member_mask_sha256': sha(ds / 'members.npy'), 'candidate_complete_mask_sha256': sha(ds / 'hour_complete.npy'),
              'unique_official_records': len(unique), 'admitted_event_records': len(evidence),
              'officially_evidenced_stockdays': int(hits.sum()), 'stocks_with_official_evidence': int(hits.any(0).sum()),
              'exclusions': exclusions, 'primary_daily_checks': primary_checks,
              'candidate_complete_hours_conflicting_with_conservative_official_mask': conflict_details,
              'independent_convolution_all_columns_passed': True, 'rows': rows,
              'evidence_path': str(OUT / 'admitted_official_event_evidence.json'),
              'evidence_sha256': sha(OUT / 'admitted_official_event_evidence.json'),
              'official_mask_sha256': sha(OUT / 'official_unavoidable_hour_mask.npy'),
              'limitations': 'SSE conservative subset only; dates at continuous-interval boundaries and open-ended/partial-day events excluded; not a full SSE/SZSE status audit',
              'original_denominators_preserved': True, 'original_targets_preserved': True,
              'retrospective_only_not_used_as_PIT_signal': True, 'prices_or_minutes_created': False,
              'candidate_changed': False, 'production_ready': False}
    (OUT / 'conservative_official_suspension_window_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ('primary_daily_checks', 'source_receipts')}), flush=True)


if __name__ == '__main__':
    main()
