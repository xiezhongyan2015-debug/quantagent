"""Acquire and compare raw public 5m bars for all 120 missing stockdays."""
import fcntl
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from public_baostock_probe import PublicClient
from audit_wind17_target_gaps import ROOT, BASE, SOURCE, PRICE, END, sha

sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow
import numpy as np
import pandas as pd

OUT = SOURCE / 'target_300114_2023_coarse_concordance'


def main():
    OUT.mkdir(exist_ok=True)
    receipt_path = OUT / 'coarse_concordance_receipt.json'
    assert not receipt_path.exists()
    audit_path = SOURCE / 'target_gap_audit/target_gap_audit_receipt.json'
    audit = json.loads(audit_path.read_text())
    result = next(r for r in audit['results'] if r['source_symbol'] == 'sz300114' and r['source_year'] == '2023')
    assert result['target_days_surviving_existing_daily_and_hour_gates'] == result['minimum_target_stockdays'] == 120
    assert sha(result['path']) == result['sha256']
    frame = pd.read_csv(result['path'], encoding='gbk').rename(columns={'开盘价': 'open', '收盘价': 'close', '最高价': 'high', '最低价': 'low', '成交量': 'lots', '成交额': 'amount'})
    frame['time'] = pd.to_datetime(frame['k线结束时间'])
    targets = sorted(d['date'] for d in result['target_stockday_checks'])
    first, last = targets[0], targets[-1]
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'status': 'querying', 'source_code_sha256': sha(__file__),
               'public_client_source_sha256': sha(ROOT / 'gru_beta_neutral_20261008/public_baostock_probe.py'),
               'target_gap_audit_receipt_sha256': sha(audit_path), 'queries': [], 'results': [],
               'native_source_path': result['path'], 'native_source_sha256': result['sha256'],
               'primary_daily_reference_path': result['primary_daily_path'], 'primary_daily_reference_sha256': result['primary_daily_sha256'],
               'coarse_bars_expanded_as_minutes': False, 'source_prices_overwritten': False,
               'authentication_uses_personal_credentials': False,
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    lock = (ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = None
    records = []
    try:
        client = PublicClient(OUT / 'coarse_responses')
        client.login()
        for code in ['sz.302132', 'sz.300114']:
            for page in range(1, 5):
                query = client.query(code, first, last, '5', page=page)
                assert query['error_code'] == '0' and query['adjustflag'] == '3'
                receipt['queries'].append({k: v for k, v in query.items() if k != 'data'})
                raw_rows = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
                if raw_rows:
                    records.extend(dict(zip(query['fields'], row)) for row in raw_rows)
                print(json.dumps({'code': code, 'page': page, 'rows': len(raw_rows)}, ensure_ascii=False), flush=True)
                if len(raw_rows) < 2000:
                    break
                assert page < 4, 'Bounded 8000-row cap reached without terminal page'
                time.sleep(1)
            if records:
                receipt['observed_coarse_source_code'] = code
                break
    except Exception as error:
        receipt.update(status='public_coarse_query_failed', error_type=type(error).__name__, error=str(error))
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                receipt['logout_error'] = str(error)
        lock.close()
    if receipt['status'] == 'public_coarse_query_failed' or not records:
        if not records:
            receipt['status'] = 'no_public_coarse_history_observed'
        receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        return
    coarse = pd.DataFrame(records)
    coarse['timestamp'] = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
    assert not coarse.timestamp.duplicated().any()
    coarse['date'] = coarse.timestamp.dt.strftime('%Y-%m-%d')
    assert set(coarse.date) == set(targets)
    assert len(coarse) == 120 * 48
    coarse[PRICE + ['volume', 'amount']] = coarse[PRICE + ['volume', 'amount']].astype(float)
    for date in targets:
        day = frame[frame.time.dt.strftime('%Y-%m-%d').eq(date)].sort_values('time')
        clock = day.time.dt.hour * 60 + day.time.dt.minute
        regular = day[np.isin(clock, END)].copy()
        assert len(regular) == 240
        q = regular[PRICE].to_numpy(float)
        volume = regular.lots.to_numpy(float) * 100
        auction = day[(clock == 570) & day.lots.gt(0)]
        if len(auction):
            assert len(auction) == 1
            row = auction.iloc[0]
            q[0, 0] = row.open
            q[0, 1] = max(q[0, 1], row.high)
            q[0, 2] = min(q[0, 2], row.low)
            volume[0] += row.lots * 100
        bars = q.reshape(48, 5, 4)
        qp = np.column_stack([bars[:, 0, 0], bars[:, :, 1].max(1), bars[:, :, 2].min(1), bars[:, -1, 3]])
        qv = volume.reshape(48, 5).sum(1)
        c = coarse[coarse.date.eq(date)].sort_values('timestamp')
        clocks = c.timestamp.dt.hour.to_numpy() * 60 + c.timestamp.dt.minute.to_numpy()
        np.testing.assert_array_equal(clocks, END[4::5])
        cp, cv = c[PRICE].to_numpy(float), c.volume.to_numpy(float)
        equal = abs(qp - cp) <= 1e-8
        within = abs(qv - cv) <= 100
        conflicts = []
        for i in range(48):
            if not (equal[i].all() and within[i]):
                conflicts.append({'ending_clock': '%02d:%02d' % divmod(int(clocks[i]), 60),
                    'native_aggregate_ohlc': qp[i].tolist(), 'public_raw_5m_ohlc': cp[i].tolist(),
                    'volume_difference_shares': float(qv[i] - cv[i])})
        receipt['results'].append({'date': date, '5m_buckets': 48,
            'exact_price_buckets_by_field': {f: int(equal[:, i].sum()) for i, f in enumerate(PRICE)},
            'volume_exact_buckets': int((qv == cv).sum()), 'volume_within_100_buckets': int(within.sum()),
            'close_and_volume_within_100_buckets': int((equal[:, 3] & within).sum()),
            'native_high_low_inside_coarse_range_buckets': int(((qp[:, 1] <= cp[:, 1] + 1e-8) & (qp[:, 2] >= cp[:, 2] - 1e-8)).sum()),
            'volume_max_absolute_difference_shares': float(abs(qv - cv).max()),
            'all_5m_close_and_volume_within_100_passed': bool((equal[:, 3] & within).all()),
            'all_5m_OHLC_and_volume_within_100_passed': bool((equal.all(1) & within).all()),
            'conflicts': conflicts})
    receipt.update({'status': 'complete_target_coarse_concordance_diagnostic', 'completed_utc': datetime.now(timezone.utc).isoformat(),
        'target_stockdays_checked': len(targets), 'coarse_rows_acquired': len(coarse),
        'days_all_5m_close_and_volume_within_100_passed': sum(r['all_5m_close_and_volume_within_100_passed'] for r in receipt['results']),
        'days_all_5m_OHLC_and_volume_within_100_passed': sum(r['all_5m_OHLC_and_volume_within_100_passed'] for r in receipt['results']),
        'individual_native_price_and_quantity_correctness_certified': False})
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k not in ('queries', 'results')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
