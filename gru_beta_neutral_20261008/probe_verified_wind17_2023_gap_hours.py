"""Scoped per-stockday public 5m QA of every newly available 2023 gap hour."""
import fcntl
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from public_baostock_probe import PublicClient
from audit_wind17_target_gaps import ROOT, SOURCE, FIELDS, END, PRICE, sha
import pandas as pd
import numpy as np

OUT = SOURCE / 'all_2023_hour_concordance'


def main():
    OUT.mkdir(exist_ok=True)
    path = OUT / 'hour_concordance_receipt.json'
    assert not path.exists()
    audit_path = SOURCE / 'all_2023_minimum_gaps/all_2023_gap_audit_receipt.json'
    audit = json.loads(audit_path.read_text())
    prior = SOURCE / 'target_300114_2023_coarse_concordance/coarse_concordance_receipt.json'
    old_cross = json.loads(prior.read_text())
    raw_302132 = []
    for query in old_cross['queries']:
        assert sha(query['wire_path']) == query['sha256']
        data = json.loads(Path(query['wire_path']).with_suffix('.json').read_text())['data']
        rows = data.get('record', []) if isinstance(data, dict) else data
        raw_302132.extend(dict(zip(query['fields'], r)) for r in rows)
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'status': 'running', 'source_code_sha256': sha(__file__),
        'all_2023_gap_audit_path': str(audit_path), 'all_2023_gap_audit_sha256': sha(audit_path),
        'prior_300114_coarse_path': str(prior), 'prior_300114_coarse_sha256': sha(prior),
        'public_client_source_sha256': sha(ROOT / 'gru_beta_neutral_20261008/public_baostock_probe.py'),
        'results': [], 'queries': [], 'new_query_budget': 98, 'coarse_expanded_as_minutes': False,
        'candidate_hours_admitted': 0, 'candidate_changed': False, 'production_ready': False,
        'minute_producer_semantics_and_volume_precision_certified': False}
    lock = (ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = None
    try:
        for stock in audit['results']:
            symbol = stock['canonical_symbol']
            assert sha(stock['raw_path']) == stock['raw_sha256']
            native = pd.read_csv(stock['raw_path'], encoding='gbk').rename(columns=FIELDS)
            native['time'] = pd.to_datetime(native['k线结束时间'])
            for day_info in stock['target_stockday_checks']:
                date = day_info['date']
                day_result = {'canonical_symbol': symbol, 'date': date, 'current_missing_hours': day_info['existing_missing_hours']}
                if not day_info['existing_daily_and_hour_gates_passed']:
                    day_result.update(status='source_failed_prior_daily_gate', strict_hours=[])
                    receipt['results'].append(day_result)
                    continue
                day = native[native.time.dt.strftime('%Y-%m-%d').eq(date)].sort_values('time')
                clocks = day.time.dt.hour * 60 + day.time.dt.minute
                regular = day[clocks.isin(END)]
                assert len(regular) == 240
                q, v, amount = regular[PRICE].to_numpy(float), regular.volume_lots.to_numpy(float) * 100, regular.amount.to_numpy(float)
                auction = day[(clocks == 570) & day.volume_lots.gt(0)]
                if len(auction):
                    a = auction.iloc[0]
                    q[0, 0], q[0, 1], q[0, 2] = a.open, max(q[0, 1], a.high), min(q[0, 2], a.low)
                    v[0] += a.volume_lots * 100
                    amount[0] += a.amount
                minute_good = np.array([Decimal(str(p[2])) * Decimal(str(vol)) - 1 <= Decimal(str(money)) <= Decimal(str(p[1])) * Decimal(str(vol)) + 1 for p, vol, money in zip(q, v, amount)])
                eligible = [slot for slot in range(4) if minute_good[slot * 60:(slot + 1) * 60].all()]
                day_result['native_amount_pass_slots'] = eligible
                if not eligible:
                    day_result.update(status='all_hours_failed_native_minute_amount_bounds', strict_hours=[])
                    receipt['results'].append(day_result)
                    continue
                if symbol == '302132.SZ':
                    rows = [r for r in raw_302132 if r['date'] == date]
                    day_result['coarse_reference_role'] = 'retained_old_300114_5m_wire'
                else:
                    assert len(receipt['queries']) < receipt['new_query_budget']
                    if client is None:
                        client = PublicClient(OUT / 'public_coarse_responses')
                        client.login()
                    query = client.query(symbol[-2:].lower() + '.' + symbol[:6], date, date, '5')
                    receipt['queries'].append({k: val for k, val in query.items() if k != 'data'})
                    data = query.get('data', [])
                    raw = data.get('record', []) if isinstance(data, dict) else data
                    rows = [dict(zip(query['fields'], r)) for r in raw] if query['error_code'] == '0' else []
                    day_result['coarse_wire_path'] = query['wire_path']
                    day_result['coarse_wire_sha256'] = query['sha256']
                    time.sleep(1)
                if len(rows) != 48:
                    day_result.update(status='public_coarse_reference_absent_or_incomplete', coarse_rows=len(rows), strict_hours=[])
                    receipt['results'].append(day_result)
                    continue
                coarse = pd.DataFrame(rows)
                t = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
                np.testing.assert_array_equal(t.dt.hour.to_numpy() * 60 + t.dt.minute.to_numpy(), END[4::5])
                grouped = q.reshape(48, 5, 4)
                qp = np.column_stack([grouped[:, 0, 0], grouped[:, :, 1].max(1), grouped[:, :, 2].min(1), grouped[:, -1, 3]])
                cp = coarse[PRICE].to_numpy(float)
                dv = v.reshape(48, 5).sum(1) - coarse.volume.to_numpy(float)
                coarse_good = (abs(qp - cp).max(1) <= 1e-8) & (abs(dv) <= 100)
                strict = [slot for slot in eligible if coarse_good[slot * 12:(slot + 1) * 12].all()]
                day_result.update(status='strict_hour_checks_completed', strict_hours=strict,
                    exact_5m_OHLC_volume_buckets=int(coarse_good.sum()),
                    volume_max_difference_shares=float(abs(dv).max()),
                    candidate_hours_admitted=0)
                receipt['results'].append(day_result)
            path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print(json.dumps({'symbol': symbol, 'stockdays_examined': len(stock['target_stockday_checks']),
                'strict_hour_diagnostic_count': sum(len(r['strict_hours']) for r in receipt['results']),
                'new_queries_completed': len(receipt['queries'])}, ensure_ascii=False), flush=True)
        receipt['status'] = 'complete_all_2023_gap_hour_diagnostics'
    except Exception as error:
        receipt.update(status='hour_diagnostic_failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                receipt['logout_error'] = str(error)
        lock.close()
        receipt.update(completed_utc=datetime.now(timezone.utc).isoformat(),
            stockdays_examined=len(receipt['results']),
            strict_hour_diagnostic_count=sum(len(r['strict_hours']) for r in receipt['results']))
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
