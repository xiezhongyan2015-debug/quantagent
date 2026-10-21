"""Compare native Sina, immutable original minutes and observed public 5m bars.

This is a positive-control diagnostic, not recovery of a missing stockday.
No source price or timestamp is rewritten, and no candidate hours are admitted.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import pandas as pd
import numpy as np

from audit_original_intraday_concordance import aggregate, compare, daily_check

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
OUT = BASE / 'original_recent_sina_control_20261009'
PRICE = ['open', 'high', 'low', 'close']
END = np.r_[np.arange(571, 691), np.arange(781, 901)]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    extraction_path = OUT / 'extraction_receipt.json'
    extraction = json.loads(extraction_path.read_text())
    assert extraction['full_split_archive_sha256_verified'] and extraction['gzip_footer_checked']
    assert not extraction['not_present_in_original'] and len(extraction['payloads']) == 2
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_intraday_concordance_samples.py') == extraction['source_code_sha256']
    assert sha(extraction['wrapper_path']) == extraction['wrapper_sha256']
    assert sha(OUT / 'invocation_basis.json') == extraction['invocation_basis_sha256']
    sina_receipt_path = BASE / 'sina_hour_local_concordance_20261009/hour_local_concordance_receipt.json'
    sina_receipt = json.loads(sina_receipt_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/probe_sina_hour_local_concordance.py') == sina_receipt['source_code_sha256']
    source_path = Path(sina_receipt['native_minute_source_path'])
    assert sha(source_path) == sina_receipt['native_minute_source_sha256']
    assert sha(sina_receipt['primary_daily_source_path']) == sina_receipt['primary_daily_source_sha256']
    raw = source_path.read_text()
    rows, _ = json.JSONDecoder().raw_decode(raw[raw.index('['):])
    native = pd.DataFrame(rows)
    native['timestamp'] = pd.to_datetime(native.day)
    native['clock'] = native.timestamp.dt.hour * 60 + native.timestamp.dt.minute
    assert not native.timestamp.duplicated().any()
    daily_obj = json.loads(Path(sina_receipt['primary_daily_source_path']).read_text())
    daily = pd.DataFrame(daily_obj['rows'], columns=daily_obj['fields']).set_index('date')
    results = []
    for record in extraction['payloads']:
        original_path = Path(record['path'])
        assert sha(original_path) == record['sha256']
        day = pd.Timestamp(record['date'])
        original = pq.read_table(original_path).to_pandas().sort_values('datetime_nano')
        assert len(original) == 240 and original['股票代码'].eq('600004').all()
        assert not original.datetime_nano.duplicated().any()
        times = pd.to_datetime(original.datetime)
        nanos = pd.to_datetime(original.datetime_nano, utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None)
        np.testing.assert_array_equal(times.to_numpy(), nanos.to_numpy())
        assert (times.dt.normalize() == day).all()
        np.testing.assert_array_equal(times.dt.hour.to_numpy() * 60 + times.dt.minute.to_numpy() + 1, END)
        op, ov = original[PRICE].to_numpy(float), original.volume.to_numpy(float)
        assert (op > 0).all() and (ov >= 0).all() and np.isfinite(op).all() and np.isfinite(ov).all()
        np.testing.assert_allclose(op, op.round(2), rtol=0, atol=1e-8)
        observed = native[native.timestamp.dt.normalize().eq(day)].sort_values('clock').set_index('clock')
        assert observed.index.isin(END).all()
        common = np.isin(END, observed.index)
        common_clocks = END[common]
        sp, sv = observed.loc[common_clocks, PRICE].to_numpy(float), observed.loc[common_clocks, 'volume'].to_numpy(float)
        query = next(q for q in sina_receipt['independent_coarse_queries'] if q['start'] == day.strftime('%Y-%m-%d'))
        assert sha(query['wire_path']) == query['sha256'] and query['frequency'] == '5'
        source_rows = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
        coarse = pd.DataFrame(source_rows, columns=query['fields'])
        assert len(coarse) == 48 and coarse.adjustflag.eq('3').all() and coarse.code.eq('sh.600004').all()
        ct = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
        np.testing.assert_array_equal(ct.dt.hour.to_numpy() * 60 + ct.dt.minute.to_numpy(), END[4::5])
        op5, ov5 = aggregate(op, ov, 5)
        cp, cv = coarse[PRICE].to_numpy(float), coarse.volume.to_numpy(float)
        hours = []
        for slot in range(4):
            clocks = END[slot * 60:(slot + 1) * 60]
            mask = np.isin(clocks, observed.index)
            missing = clocks[~mask]
            original_missing_volume = ov[slot * 60:(slot + 1) * 60][~mask]
            n = observed.loc[clocks[mask]]
            hours.append({'slot_zero_based': slot, 'native_minutes': len(n),
                'missing_native_clock_labels': ['%02d:%02d' % divmod(int(c), 60) for c in missing],
                'original_volume_at_missing_labels_shares': original_missing_volume.tolist(),
                'native_minus_original_total_volume_shares': float(n.volume.astype(float).sum() - ov[slot * 60:(slot + 1) * 60].sum()),
                'original_vs_native_common_minutes': compare(op[slot * 60:(slot + 1) * 60][mask], ov[slot * 60:(slot + 1) * 60][mask], n[PRICE].to_numpy(float), n.volume.to_numpy(float))})
        disagreements = []
        for oi, clock in enumerate(END):
            if clock not in observed.index:
                disagreements.append({'end_clock': '%02d:%02d' % divmod(int(clock), 60), 'native_missing': True,
                    'original_raw_ohlc': op[oi].tolist(), 'original_volume_shares': float(ov[oi])})
                continue
            n = observed.loc[clock]
            if np.any(np.abs(op[oi] - n[PRICE].to_numpy(float)) > 1e-8) or ov[oi] != float(n.volume):
                disagreements.append({'end_clock': '%02d:%02d' % divmod(int(clock), 60), 'native_missing': False,
                    'original_raw_ohlc': op[oi].tolist(), 'native_raw_ohlc': n[PRICE].to_numpy(float).tolist(),
                    'original_volume_shares': float(ov[oi]), 'native_volume_shares': float(n.volume)})
        results.append({'symbol': '600004.SH', 'date': day.strftime('%Y-%m-%d'),
            'positive_control_existing_candidate_stockday_complete': True,
            'original_payload_path': str(original_path), 'original_payload_sha256': sha(original_path),
            'original_text_and_UTC_nanos_consistent': True,
            'original_vs_native_common_minutes': compare(op[common], ov[common], sp, sv),
            'original_vs_public_5m': compare(op5, ov5, cp, cv),
            'original_daily_check': daily_check(op, ov, daily.loc[day.strftime('%Y-%m-%d')]),
            'native_missing_minutes': int((~common).sum()),
            'original_volume_at_native_missing_labels_shares': float(ov[~common].sum()),
            'native_minus_original_day_volume_shares': float(sv.sum() - ov.sum()),
            'original_open_equals_previous_close_intervals': int((np.abs(op[1:, 0] - op[:-1, 3]) <= 1e-8).sum()),
            'hours': hours, 'minute_disagreements': disagreements})
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'recent_native_vs_original_positive_controls_compared',
        'source_code_sha256': sha(__file__),
        'comparison_helper_path': str(ROOT / 'gru_beta_neutral_20261008/audit_original_intraday_concordance.py'),
        'comparison_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/audit_original_intraday_concordance.py'),
        'extraction_receipt_path': str(extraction_path), 'extraction_receipt_sha256': sha(extraction_path),
        'sina_hour_local_receipt_path': str(sina_receipt_path), 'sina_hour_local_receipt_sha256': sha(sina_receipt_path),
        'native_source_path': str(source_path), 'native_source_sha256': sha(source_path),
        'primary_daily_source_path': sina_receipt['primary_daily_source_path'], 'primary_daily_source_sha256': sina_receipt['primary_daily_source_sha256'],
        'coarse_queries': sina_receipt['independent_coarse_queries'], 'results': results,
        'source_bytes_changed': False, 'candidate_changed': False, 'candidate_hours_admitted': 0,
        'minutes_created': 0, 'price_fields_overwritten': False,
        'timestamp_pairing': 'Original observed left labels are paired with observed native end labels one minute later; no native clock shift or missing-minute fill',
        'scope': 'Two already complete stockdays of one SH equity; differences alone do not establish trade-level correctness of either provider',
        'production_ready': False}
    (OUT / 'recent_concordance_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps([{k: v for k, v in r.items() if k not in ('hours', 'minute_disagreements')} for r in results], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
