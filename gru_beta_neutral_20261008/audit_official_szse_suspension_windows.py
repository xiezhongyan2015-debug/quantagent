"""Audit SZSE closed open-to-open intervals and combine the SSE lower bound.

Only consecutive unambiguous official stop/resume actions at market open are
paired. Resume dates are excluded. No open-ended stops, partial-day intervals,
prices, executable bars, or PIT claims are inferred.
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
CANDIDATE = BASE / 'backfilled_panel_candidate_ipo_prefix_v5'
SSE = BASE / 'public_source_probes/official_sse_suspension_chrome_20261009/conservative_window_audit'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    calendar = json.loads(calendar_path.read_text())
    dates = np.array(calendar['dates'])
    symbols = calendar['union_symbols']
    index = {symbol: i for i, symbol in enumerate(symbols)}
    unique, source_receipts = {}, []
    for interval in ['20191001_20191231', '20200101_20201231', '20210101_20211231', '20220101_20221231']:
        path = SOURCE / ('history_' + interval) / 'complete_source_receipt.json'
        receipt = json.loads(path.read_text())
        assert receipt['status'] == 'all_requested_pages_saved' and receipt['TLS_validation_preserved']
        assert not receipt['personal_credentials_used']
        assert sha(receipt['events_path']) == receipt['events_sha256']
        reconstructed = []
        for page in receipt['pages']:
            assert sha(page['raw_path']) == page['raw_sha256']
            obj = json.loads(Path(page['raw_path']).read_bytes())
            assert len(obj) == 1 and not obj[0].get('error')
            md = obj[0]['metadata']
            assert md['catalogid'] == '1798' and md['tabkey'] == 'tab1'
            assert md['pageno'] == page['page'] and md['recordcount'] == receipt['reported_total_events']
            reconstructed.extend(obj[0]['data'])
            for row_number, event in enumerate(obj[0]['data']):
                key = json.dumps(event, sort_keys=True, ensure_ascii=False)
                if key not in unique:
                    unique[key] = {'event': event, 'source_occurrences': []}
                unique[key]['source_occurrences'].append({'page_raw_path': page['raw_path'],
                    'page_raw_sha256': page['raw_sha256'], 'row_index_zero_based': row_number})
        assert reconstructed == json.loads(Path(receipt['events_path']).read_text())
        assert len(reconstructed) == receipt['reported_total_events']
        source_receipts.append({'path': str(path), 'sha256': sha(path), 'events': len(reconstructed)})
    actions = defaultdict(lambda: defaultdict(list))
    exclusions = Counter()
    for entry in unique.values():
        row = entry['event']
        symbol = row['zqdm'] + '.SZ'
        if symbol not in index:
            exclusions['outside_fixed_1178_stock_union'] += 1
            continue
        for field, action in [('tpkssj', 'stop'), ('fpkssj', 'resume')]:
            if not row[field]:
                continue
            match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}) (开市|\d{2}:\d{2}:\d{2})', row[field])
            assert match, row
            date, clock = match.groups()
            clock_order = '09:30:00' if clock == '开市' else clock
            key = (date, clock_order, action, clock)
            actions[symbol][key].append(entry)
    day_hits = np.zeros((len(dates), len(symbols)), dtype=bool)
    evidence, pending = [], []
    for symbol, by_action in sorted(actions.items()):
        # All actions, including intraday actions, block pairing over ambiguity.
        ordered = sorted(by_action)
        timestamp_counts = Counter((k[0], k[1]) for k in ordered)
        admitted_stops = set()
        for position, stop in enumerate(ordered[:-1]):
            resume = ordered[position + 1]
            if stop[2:] != ('stop', '开市') or resume[2:] != ('resume', '开市'):
                continue
            if timestamp_counts[stop[:2]] != 1 or timestamp_counts[resume[:2]] != 1:
                continue
            if stop[0] >= resume[0]:
                continue
            use = (dates >= stop[0]) & (dates < resume[0])
            if not use.any():
                continue
            # Reject unknown stand-alone action semantics; combined explicit
            # stop/resume rows with a declared duration may also qualify.
            stop_rows = by_action[stop]
            resume_rows = by_action[resume]
            good_stop = [e for e in stop_rows if e['event']['tpsj'] == '停牌' or e['event']['fpkssj']]
            good_resume = [e for e in resume_rows if e['event']['tpsj'] == '取消停牌' or e['event']['tpkssj']]
            if not good_stop or not good_resume:
                continue
            day_hits[:, index[symbol]] |= use
            admitted_stops.add(stop)
            evidence.append({'symbol': symbol, 'stop_at_open_inclusive': stop[0],
                'resume_at_open_exclusive': resume[0], 'declared_unavoidable_dates': dates[use].tolist(),
                'admission_rule': 'consecutive_unambiguous_official_market_open_stop_and_resume_actions',
                'stop_evidence': good_stop, 'resume_evidence': good_resume})
        for action in ordered:
            if action[2] == 'stop' and action not in admitted_stops:
                pending.append({'symbol': symbol, 'action': action, 'source_evidence': by_action[action],
                    'reason': 'no_consecutive_unambiguous_closed_open_to_open_interval'})
    szse_hours = np.repeat(day_hits, 4, axis=0)
    sse_path = SSE / 'conservative_official_suspension_window_receipt.json'
    sse_receipt = json.loads(sse_path.read_text())
    sse_mask_path = SSE / 'official_unavoidable_hour_mask.npy'
    assert sha(sse_mask_path) == sse_receipt['official_mask_sha256']
    assert sse_receipt['calendar_sha256'] == sha(calendar_path)
    sse_hours = np.load(sse_mask_path)
    assert sse_hours.shape == szse_hours.shape
    hours = sse_hours | szse_hours
    ds = CANDIDATE / 'dataset'
    members = np.load(ds / 'members.npy', mmap_mode='r')
    complete = np.load(ds / 'hour_complete.npy', mmap_mode='r')
    window = np.load(ds / 'window80_complete.npy', mmap_mode='r')
    assert members.shape == complete.shape == window.shape == hours.shape
    prefix = np.vstack([np.zeros((1, len(symbols)), np.int32), np.cumsum(hours, axis=0, dtype=np.int32)])
    affected = prefix[1:] - prefix[np.maximum(np.arange(len(hours)) - 79, 0)] > 0
    for column in np.flatnonzero(hours.any(0)):
        independent = np.convolve(hours[:, column].astype(int), np.ones(80, dtype=int), mode='full')[:len(hours)] > 0
        np.testing.assert_array_equal(affected[:, column], independent)
    conflicts = [{'date': dates[row // 4], 'slot': int(row % 4), 'symbol': symbols[column]}
                 for row, column in np.argwhere(hours & complete)]
    assert not conflicts, conflicts[:10]
    assert not (affected & window).any()
    daily_checks, counts = [], Counter()
    primary = BASE / 'public_source_probes/baostock/union_daily_factors/normalized'
    for column in np.flatnonzero(day_hits.any(0)):
        path = primary / (symbols[column] + '.json')
        obj = json.loads(path.read_text())
        daily = pd.DataFrame(obj['rows'], columns=obj['fields']).set_index('date').reindex(dates[day_hits[:, column]])
        missing = daily.code.isna()
        volume = pd.to_numeric(daily.volume, errors='coerce')
        conflict = (~missing) & (daily.tradestatus.ne('0') | (volume.notna() & volume.ne(0)))
        no_volume = (~missing) & (~conflict) & volume.isna()
        zero_volume = (~missing) & (~conflict) & volume.eq(0)
        classes = {'primary_missing_rows': missing, 'primary_status_or_nonzero_volume_conflicts': conflict,
                   'status_0_volume_unreported': no_volume, 'status_0_volume_explicit_0': zero_volume}
        assert sum(int(mask.sum()) for mask in classes.values()) == len(daily)
        counts.update({key: int(mask.sum()) for key, mask in classes.items()})
        daily_checks.append({'symbol': symbols[column], 'source_path': str(path), 'source_sha256': sha(path),
            'dates_by_classification': {key: daily.index[mask].tolist() for key, mask in classes.items()}})
    years = np.repeat(np.array([int(d[:4]) for d in dates]), 4)
    rows = []
    for year in [2020, 2021, 2022]:
        use = years == year
        denominator = int(members[use].sum())
        unavoidable = int((affected[use] & members[use]).sum())
        ceiling = 1 - unavoidable / denominator
        rows.append({'year': year, 'unchanged_original_member_stockhour_denominator': denominator,
            'officially_evidenced_unavoidable_incomplete_member_windows': unavoidable,
            'conservative_optimistic_coverage_ceiling': ceiling, 'original_target': .995,
            'original_target_proven_unattainable_by_this_subset': ceiling < .995,
            'actual_candidate_coverage': float((window[use] & members[use]).sum() / denominator)})
    np.save(OUT / 'szse_official_unavoidable_hour_mask.npy', szse_hours)
    np.save(OUT / 'combined_official_unavoidable_hour_mask.npy', hours)
    evidence_path = OUT / 'admitted_official_interval_evidence.json'
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    (OUT / 'unpaired_or_unclear_stop_actions.json').write_text(json.dumps(pending, ensure_ascii=False, indent=2))
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'status': 'conservative_two_exchange_subset_audit_complete',
        'source_code_sha256': sha(Path(__file__)), 'source_receipts': source_receipts,
        'calendar_sha256': sha(calendar_path), 'candidate_meta_sha256': sha(ds / 'meta.json'),
        'candidate_member_mask_sha256': sha(ds / 'members.npy'), 'candidate_complete_mask_sha256': sha(ds / 'hour_complete.npy'),
        'SSE_audit_receipt_path': str(sse_path), 'SSE_audit_receipt_sha256': sha(sse_path),
        'SZSE_unique_official_records': len(unique), 'SZSE_closed_intervals_admitted': len(evidence),
        'SZSE_officially_evidenced_stockdays': int(day_hits.sum()), 'SZSE_stocks_with_official_evidence': int(day_hits.any(0).sum()),
        'SZSE_exclusions': dict(exclusions), 'SZSE_pending_stop_actions': len(pending),
        'SZSE_primary_classification_counts': dict(counts), 'SZSE_primary_daily_checks': daily_checks,
        'combined_officially_evidenced_stockdays': int(hours.sum() // 4),
        'combined_stocks_with_official_evidence': int(hours.any(0).sum()),
        'candidate_complete_hours_conflicting_with_conservative_official_mask': conflicts,
        'independent_convolution_all_columns_passed': True, 'rows': rows,
        'evidence_path': str(evidence_path), 'evidence_sha256': sha(evidence_path),
        'combined_official_mask_sha256': sha(OUT / 'combined_official_unavoidable_hour_mask.npy'),
        'limitations': 'Conservative retrospective SSE/SZSE subset, not a nationwide exhaustive or PIT suspension dataset; ambiguous, partial-day and open-ended SZSE actions are excluded; no alias mapping inferred.',
        'original_denominators_preserved': True, 'original_targets_preserved': True,
        'retrospective_only_not_used_as_PIT_signal': True, 'prices_or_minutes_created': False,
        'candidate_changed': False, 'production_ready': False}
    (OUT / 'combined_official_suspension_window_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in result.items() if key not in ['source_receipts', 'SZSE_primary_daily_checks']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
