"""Compare original raw minutes, an external minute source, and raw 5m quotes.

Diagnostic only; aggregate observed minutes and preserve every source byte.
Original left labels are paired with external end labels one minute later.
No provider is deemed authoritative about individual trades by this audit.
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
from public_baostock_probe import PublicClient

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
OUT = BASE / 'original_intraday_concordance_20261009'
PRICE = ['open', 'high', 'low', 'close']
END = np.r_[np.arange(571, 691), np.arange(781, 901)]


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def aggregate(price, volume, size):
    p = price.reshape(-1, size, 4)
    return np.column_stack([p[:, 0, 0], p[:, :, 1].max(1),
                            p[:, :, 2].min(1), p[:, -1, 3]]), volume.reshape(-1, size).sum(1)


def compare(a, av, b, bv):
    difference = a - b
    equal = np.abs(difference) <= 1e-8
    vequal = np.abs(av - bv) <= 100
    return {'intervals': len(a), 'exact_price_intervals_by_field':
            {name: int(equal[:, i].sum()) for i, name in enumerate(PRICE)},
            'volume_exact_intervals': int((av == bv).sum()),
            'volume_within_100_intervals': int(vequal.sum()),
            'close_and_volume_within_100_intervals': int((equal[:, 3] & vequal).sum()),
            'price_max_absolute_difference_by_field':
            {name: float(np.abs(difference[:, i]).max()) for i, name in enumerate(PRICE)},
            'volume_max_absolute_difference_shares': float(np.abs(av - bv).max()),
            'volume_total_difference_shares': float(av.sum() - bv.sum()),
            'volume_cumulative_max_absolute_difference_shares': float(np.abs(np.cumsum(av - bv)).max())}


def daily_check(a, av, daily):
    p, v = aggregate(a, av, 240)
    return {'observed_raw_ohlc': p[0].tolist(), 'primary_raw_ohlc': [float(daily[x]) for x in PRICE],
            'open_exact': bool(abs(p[0, 0] - float(daily.open)) <= 1e-8),
            'close_exact': bool(abs(p[0, 3] - float(daily.close)) <= 1e-8),
            'all_quotes_inside_daily_range': bool(p[0, 1] <= float(daily.high) + 1e-8 and
                                                  p[0, 2] >= float(daily.low) - 1e-8),
            'volume_difference_shares': float(v[0] - float(daily.volume))}


def check(record, query):
    day = pd.Timestamp(record['date'])
    symbol = record['code'] + ('.SH' if record['code'].startswith('6') else '.SZ')
    original = Path(record['path'])
    assert sha(original) == record['sha256']
    o = pq.read_table(original).to_pandas().sort_values('datetime_nano')
    assert len(o) == 240 and not o.datetime_nano.duplicated().any()
    assert o['股票代码'].eq(record['code']).all()
    labels = pd.to_datetime(o.datetime)
    nanos = pd.to_datetime(o.datetime_nano, utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None)
    assert (labels.to_numpy() == nanos.to_numpy()).all()
    assert (labels.dt.normalize() == day).all()
    np.testing.assert_array_equal(labels.dt.hour.to_numpy() * 60 + labels.dt.minute.to_numpy() + 1, END)
    op = o[PRICE].to_numpy(float)
    np.testing.assert_allclose(op, op.round(2), rtol=0, atol=1e-8)
    ov = o.volume.to_numpy(float)
    np.testing.assert_allclose(o[['qfq_' + x for x in PRICE]].to_numpy(float),
                               op * o.qfq_ratio.to_numpy(float)[:, None], rtol=1e-7, atol=1e-6)
    hfpath = BASE / 'huggingface/snapshot/data/stock_1m' / symbol[-2:] / (symbol[:6] + '.parquet')
    h = pq.read_table(hfpath, filters=[('timestamp', '>=', day),
                                     ('timestamp', '<', day + pd.Timedelta(days=1))]).to_pandas().sort_values('timestamp')
    clock = h.timestamp.dt.hour.to_numpy() * 60 + h.timestamp.dt.minute.to_numpy()
    assert not h.timestamp.duplicated().any() and (h.timestamp.dt.normalize() == day).all()
    regular = h[np.isin(clock, END)]
    np.testing.assert_array_equal(regular.timestamp.dt.hour.to_numpy() * 60 + regular.timestamp.dt.minute.to_numpy(), END)
    hp = regular[PRICE].to_numpy(float).round(2)
    hv = regular.volume.to_numpy(float).copy()
    boundary = h[(clock == 570) & h.volume.gt(0)]
    assert len(boundary) <= 1 and not ((clock == 780) & h.volume.gt(0)).any()
    if len(boundary):
        b = boundary.iloc[0]
        hp[0, 0] = round(float(b.open), 2)
        hp[0, 1] = max(hp[0, 1], round(float(b.high), 2))
        hp[0, 2] = min(hp[0, 2], round(float(b.low), 2))
        hv[0] += float(b.volume)
    daily_path = BASE / 'baostock/union_daily_factors/normalized' / (symbol + '.json')
    data = json.loads(daily_path.read_text())
    daily = pd.DataFrame(data['rows'], columns=data['fields']).set_index('date').loc[day.strftime('%Y-%m-%d')]
    rows = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
    coarse = pd.DataFrame(rows, columns=query['fields'])
    assert len(coarse) == 48 and coarse.adjustflag.eq('3').all()
    assert coarse.code.eq(symbol[-2:].lower() + '.' + symbol[:6]).all()
    times = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
    np.testing.assert_array_equal(times.dt.hour.to_numpy() * 60 + times.dt.minute.to_numpy(), END[4::5])
    assert sha(query['wire_path']) == query['sha256']
    cp, cv = coarse[PRICE].to_numpy(float), coarse.volume.to_numpy(float)
    op5, ov5 = aggregate(op, ov, 5)
    hp5, hv5 = aggregate(hp, hv, 5)
    details = []
    for i in range(240):
        if np.any(np.abs(op[i] - hp[i]) > 1e-8) or ov[i] != hv[i]:
            details.append({'end_clock': '%02d:%02d' % divmod(END[i], 60),
                            'original_raw_ohlc': op[i].tolist(), 'external_raw_ohlc': hp[i].tolist(),
                            'original_volume_shares': float(ov[i]), 'external_volume_shares': float(hv[i])})
    shifts = []
    for shift in [-1, 0, 1]:
        index = {int(clock): i for i, clock in enumerate(END + shift)}
        oi = [i for i, clock in enumerate(END) if int(clock) in index]
        hi = [index[int(END[i])] for i in oi]
        shifts.append({'external_clock_shift_minutes': shift, **compare(op[oi], ov[oi], hp[hi], hv[hi])})
    oh, ovh = aggregate(op, ov, 60)
    hh, hvh = aggregate(hp, hv, 60)
    return {'symbol': symbol, 'date': day.strftime('%Y-%m-%d'),
            'sources': [{'role': 'original_raw_minute_payload', 'path': str(original), 'sha256': sha(original)},
                        {'role': 'external_raw_minute_file', 'path': str(hfpath), 'sha256': sha(hfpath)},
                        {'role': 'primary_daily', 'path': str(daily_path), 'sha256': sha(daily_path)},
                        {'role': 'public_raw_5m_wire', 'path': query['wire_path'], 'sha256': query['sha256']}],
            'original_local_text_and_UTC_nanos_consistent': True,
            'boundary_0930_positive_volume_merged_into_first_external_interval': len(boundary) == 1,
            'original_vs_external_1m': compare(op, ov, hp, hv),
            'clock_shift_diagnostics': shifts,
            'original_vs_public_5m': compare(op5, ov5, cp, cv),
            'external_vs_public_5m': compare(hp5, hv5, cp, cv),
            'original_daily_check': daily_check(op, ov, daily),
            'external_daily_check': daily_check(hp, hv, daily),
            'original_open_equals_previous_close_intervals': int((np.abs(op[1:, 0] - op[:-1, 3]) <= 1e-8).sum()),
            'external_open_equals_previous_close_intervals': int((np.abs(hp[1:, 0] - hp[:-1, 3]) <= 1e-8).sum()),
            'hour_volume_difference_shares': (ovh - hvh).tolist(),
            'hour_ohlc_difference': (oh - hh).tolist(),
            'minute_disagreements': details, 'prices_modified': False,
            'coarse_bars_expanded_as_1m': False, 'panel_modified': False}


def main():
    extraction = json.loads((OUT / 'extraction_receipt.json').read_text())
    assert extraction['full_split_archive_sha256_verified'] and extraction['gzip_footer_checked']
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_intraday_concordance_samples.py') == extraction['source_code_sha256']
    client = None
    results = []
    try:
        for r in extraction['payloads']:
            day = pd.Timestamp(r['date']).strftime('%Y-%m-%d')
            market = 'sh' if r['code'].startswith('6') else 'sz'
            filename = market + '.' + r['code'] + '_' + day + '_' + day + '_5_p1.json'
            paths = [BASE / 'hour_local_coarse_concordance_20261009/supplemental' / filename,
                     OUT / 'coarse_reference' / filename]
            found = next((p for p in paths if p.exists()), None)
            if found is not None:
                query = json.loads(found.read_text())
            else:
                if client is None:
                    client = PublicClient(OUT / 'coarse_reference')
                    client.login()
                query = client.query(market + '.' + r['code'], day, day, '5')
            assert query['error_code'] == '0' and query['frequency'] == '5'
            result = check(r, query)
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k not in ('minute_disagreements', 'clock_shift_diagnostics', 'sources')}), flush=True)
    finally:
        if client is not None:
            client.close()
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(),
               'status': 'original_raw_minute_concordance_audit_complete',
               'extraction_receipt_sha256': sha(OUT / 'extraction_receipt.json'),
               'source_code_sha256': sha(Path(__file__)),
               'public_query_client_source_sha256': sha(ROOT / 'gru_beta_neutral_20261008/public_baostock_probe.py'),
               'results': results, 'minutes_created': 0, 'candidate_hours_admitted': 0,
               'candidate_changed': False, 'exact_tick_time_semantics_certified': False, 'production_ready': False}
    (OUT / 'concordance_audit_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
