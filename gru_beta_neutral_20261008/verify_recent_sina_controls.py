"""Independent clock grouping and decimal native-bar checks, without audit imports."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
OUT = BASE / 'original_recent_sina_control_20261009'
PRICE = ['open', 'high', 'low', 'close']


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_metrics(a, b, reported):
    assert len(a) == len(b) == reported['intervals']
    equal = np.isclose(a[PRICE], b[PRICE], rtol=0, atol=1e-8)
    vd = a.volume.astype(float).to_numpy() - b.volume.astype(float).to_numpy()
    assert {f: int(equal[:, i].sum()) for i, f in enumerate(PRICE)} == reported['exact_price_intervals_by_field']
    assert int((vd == 0).sum()) == reported['volume_exact_intervals']
    assert int((abs(vd) <= 100).sum()) == reported['volume_within_100_intervals']
    assert int((equal[:, 3] & (abs(vd) <= 100)).sum()) == reported['close_and_volume_within_100_intervals']
    assert float(vd.sum()) == reported['volume_total_difference_shares']
    assert float(abs(vd).max()) == reported['volume_max_absolute_difference_shares']
    assert float(abs(np.cumsum(vd)).max()) == reported['volume_cumulative_max_absolute_difference_shares']
    for i, field in enumerate(PRICE):
        assert abs(float(abs(a[field].astype(float).to_numpy() - b[field].astype(float).to_numpy()).max()) - reported['price_max_absolute_difference_by_field'][field]) < 1e-8


def main():
    path = OUT / 'recent_concordance_receipt.json'
    audit = json.loads(path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_recent_sina_original_controls.py') == audit['source_code_sha256']
    assert sha(audit['comparison_helper_path']) == audit['comparison_helper_sha256']
    for pkey, hkey in [('native_source_path', 'native_source_sha256'),
                       ('primary_daily_source_path', 'primary_daily_source_sha256'),
                       ('extraction_receipt_path', 'extraction_receipt_sha256'),
                       ('sina_hour_local_receipt_path', 'sina_hour_local_receipt_sha256')]:
        assert sha(audit[pkey]) == audit[hkey]
    local = json.loads(Path(audit['sina_hour_local_receipt_path']).read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/probe_sina_hour_local_concordance.py') == local['source_code_sha256']
    raw = Path(audit['native_source_path']).read_text()
    rows, _ = json.JSONDecoder().raw_decode(raw[raw.index('['):])
    native = pd.DataFrame(rows).assign(end=lambda f: pd.to_datetime(f.day)).set_index('end').sort_index()
    native[PRICE + ['volume', 'amount']] = native[PRICE + ['volume', 'amount']].astype(float)
    primary = json.loads(Path(audit['primary_daily_source_path']).read_text())
    daily = {row[0]: dict(zip(primary['fields'], row)) for row in primary['rows']}
    candidate = ROOT / 'outputs/gru_beta_neutral_20261008/backfilled_panel_candidate_ipo_prefix_v5/dataset'
    meta = json.loads((candidate / 'meta.json').read_text())
    for filename in ['members.npy', 'hour_complete.npy']:
        assert sha(candidate / filename) == meta['files_sha256'][filename]
    members = np.load(candidate / 'members.npy', mmap_mode='r')
    complete = np.load(candidate / 'hour_complete.npy', mmap_mode='r')
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    calendar = json.loads(calendar_path.read_text())
    column = calendar['union_symbols'].index('600004.SH')
    checks, passed_hours = [], 0
    for result in audit['results']:
        date = result['date']
        assert sha(result['original_payload_path']) == result['original_payload_sha256']
        original = pq.read_table(result['original_payload_path']).to_pandas()
        original = original.assign(end=pd.to_datetime(original.datetime) + pd.Timedelta(minutes=1)).set_index('end').sort_index()
        assert len(original) == 240 and original.index.is_unique
        common = native.index.intersection(original.index)
        n, o = native.loc[common], original.loc[common]
        verify_metrics(o, n, result['original_vs_native_common_minutes'])
        missing = original.index.difference(common)
        assert len(missing) == result['native_missing_minutes']
        assert float(original.loc[missing].volume.sum()) == result['original_volume_at_native_missing_labels_shares'] == 0
        assert float(n.volume.sum() - original.volume.sum()) == result['native_minus_original_day_volume_shares']
        query = next(q for q in audit['coarse_queries'] if q['start'] == date)
        assert sha(query['wire_path']) == query['sha256']
        assert json.loads(Path(query['wire_path']).with_suffix('.json').read_text()) == query
        coarse = pd.DataFrame(query['data']['record'], columns=query['fields'])
        coarse.index = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
        coarse[PRICE + ['volume']] = coarse[PRICE + ['volume']].astype(float)
        grouped = original.groupby(pd.Grouper(freq='5min', label='right', closed='right')).agg(
            {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
        verify_metrics(grouped.loc[coarse.index], coarse, result['original_vs_public_5m'])
        assert result['original_daily_check']['open_exact'] and result['original_daily_check']['close_exact']
        assert float(original.volume.sum()) == float(daily[date]['volume'])
        assert float(original.open.iloc[0]) == float(daily[date]['open'])
        assert float(original.close.iloc[-1]) == float(daily[date]['close'])
        day_index = calendar['dates'].index(date)
        assert members[day_index * 4:day_index * 4 + 4, column].all()
        assert complete[day_index * 4:day_index * 4 + 4, column].all()
        old_result = next(r for r in local['results'] if r['date'] == date)
        for slot, start in enumerate(['09:31', '10:31', '13:01', '14:01']):
            grid = pd.date_range(date + ' ' + start, periods=60, freq='min')
            local_hour = old_result['hours'][slot]
            audit_hour = result['hours'][slot]
            hm = grid.difference(common)
            assert [stamp.strftime('%H:%M') for stamp in hm] == audit_hour['missing_native_clock_labels']
            if len(hm):
                assert not local_hour['hour_strictly_passed']
                continue
            block = native.loc[grid]
            bgroup = block.groupby(pd.Grouper(freq='5min', label='right', closed='right')).agg(
                {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
            bcoarse = coarse.loc[bgroup.index]
            good = ((abs(bgroup.open - bcoarse.open) < 1e-8) & (abs(bgroup.close - bcoarse.close) < 1e-8)
                    & (bgroup.high <= bcoarse.high + 1e-8) & (bgroup.low >= bcoarse.low - 1e-8)
                    & (abs(bgroup.volume - bcoarse.volume) <= 100))
            conflicts = []
            for stamp, row in block.iterrows():
                volume, amount = Decimal(str(row.volume)), Decimal(str(row.amount))
                if not Decimal(str(row.low)) * volume - 1 <= amount <= Decimal(str(row.high)) * volume + 1:
                    conflicts.append(stamp.hour * 60 + stamp.minute)
            assert conflicts == local_hour['amount_price_volume_conflict_clocks']
            assert bool(good.all()) == local_hour['all_12_independent_5m_buckets_passed']
            opening = slot != 0 or float(block.open.iloc[0]) == float(daily[date]['open'])
            valid_range = block.high.max() <= float(daily[date]['high']) and block.low.min() >= float(daily[date]['low'])
            passed = bool(good.all() and opening and valid_range and block.volume.sum() > 0 and not conflicts)
            assert passed == local_hour['hour_strictly_passed']
            passed_hours += passed
        checks.append({'date': date, 'common_native_minutes': len(common), 'missing_native_minutes': len(missing),
            'missing_labels_have_original_zero_volume': True,
            'native_day_volume_shortfall_shares': float(original.volume.sum() - n.volume.sum()),
            'original_close_and_volume_exact_5m_buckets': int(result['original_vs_public_5m']['close_and_volume_within_100_intervals']),
            'already_complete_member_stockday_verified': True})
    assert passed_hours == local['native_hours_surviving_strict_cross_source_tests'] == 0
    assert audit['candidate_hours_admitted'] == 0 and not audit['candidate_changed']
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
        'source_code_sha256': sha(__file__), 'audited_receipt_sha256': sha(path),
        'hour_local_receipt_sha256': sha(audit['sina_hour_local_receipt_path']),
        'method': 'Independent pandas clock grouping, decimal amount bounds and fresh retained-source hashes; no audit modules imported',
        'candidate_meta_sha256': sha(candidate / 'meta.json'), 'calendar_sha256': sha(calendar_path),
        'positive_control_members_and_completeness_verified': True,
        'strict_native_hours_passed': passed_hours, 'checks': checks,
        'candidate_changed': False, 'production_ready': False}
    (OUT / 'independent_recent_findings_verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
