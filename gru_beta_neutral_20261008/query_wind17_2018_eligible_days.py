"""One bounded fresh anonymous session after a confirmed terminal login failure."""
import fcntl
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from public_baostock_probe import PublicClient
from verify_wind17_all_2023_gap_findings import ROOT, SOURCE, sha, normalized_day, FIELDS, PRICE
import pandas as pd

OUT = SOURCE / 'all_2018_minimum_gaps'


def main():
    np = OUT / 'all_native_missing_hours_receipt.json'
    dp = OUT / 'all_2018_gap_audit_receipt.json'
    failed_path = OUT / 'hour_diagnostics_receipt.json'
    path = OUT / 'bounded_coarse_eligible_days_receipt.json'
    assert not path.exists()
    native, daily, failed = [json.loads(p.read_text()) for p in [np, dp, failed_path]]
    assert failed['status'] == 'failed' and failed['error_type'] == 'TimeoutError' and not failed['queries']
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'native_audit_path': str(np), 'native_audit_sha256': sha(np), 'daily_audit_sha256': sha(dp),
               'prior_terminal_failure_path': str(failed_path), 'prior_terminal_failure_sha256': sha(failed_path),
               'public_client_source_sha256': sha(ROOT / 'gru_beta_neutral_20261008/public_baostock_probe.py'),
               'fresh_session_attempt_limit': 1, 'query_budget': 24, 'status': 'running', 'queries': [], 'results': [],
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'coarse_expanded_as_minutes': False}
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    stocks = {r['canonical_symbol']: r for r in daily['results']}
    frames = {}
    lock = (ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = None
    try:
        client = PublicClient(OUT / 'bounded_coarse_wire_responses')
        client.login()
        for item in native['results']:
            eligible = item['native_amount_pass_missing_slots']
            if not eligible:
                continue
            symbol, date = item['symbol'], item['date']
            if symbol not in frames:
                stock = stocks[symbol]
                assert sha(stock['raw_path']) == stock['raw_sha256']
                frame = pd.read_csv(stock['raw_path'], encoding='gbk', dtype=str).rename(columns=FIELDS)
                frame.index = pd.to_datetime(frame['k线结束时间'])
                frames[symbol] = frame
            assert len(receipt['queries']) < receipt['query_budget']
            q = client.query(symbol[-2:].lower() + '.' + symbol[:6], date, date, '5')
            receipt['queries'].append({k: v for k, v in q.items() if k != 'data'})
            data = q.get('data', [])
            rows = data.get('record', []) if isinstance(data, dict) else data
            rows = [dict(zip(q['fields'], r)) for r in rows] if q['error_code'] == '0' else []
            result = {'symbol': symbol, 'date': date, 'native_eligible_missing_slots': eligible,
                      'coarse_wire_path': q['wire_path'], 'coarse_wire_sha256': q['sha256']}
            if len(rows) != 48:
                result.update(status='coarse_reference_incomplete', coarse_rows=len(rows), strict_hours=[])
            else:
                _, reg, _ = normalized_day(frames[symbol], date)
                ref = pd.DataFrame(rows)
                ref.index = pd.to_datetime(ref.time, format='%Y%m%d%H%M%S%f')
                grouped = reg.groupby(pd.Grouper(freq='5min', closed='right', label='right')).agg(
                    {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).loc[ref.index]
                good = []
                for stamp, row in ref.iterrows():
                    actual = grouped.loc[stamp]
                    good.append(all(actual[f] == Decimal(row[f]) for f in PRICE) and abs(actual.volume - Decimal(row.volume)) <= 100)
                result.update(status='strict_coarse_checks_complete', coarse_pass_buckets=sum(good),
                              strict_hours=[s for s in eligible if all(good[s*12:(s+1)*12])])
            receipt['results'].append(result)
            path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            time.sleep(1)
        receipt['status'] = 'complete_one_bounded_coarse_session'
    except Exception as error:
        receipt.update(status='bounded_coarse_session_failed', error_type=type(error).__name__, error=str(error))
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                receipt['logout_error'] = str(error)
        lock.close()
        receipt.update(completed_utc=datetime.now(timezone.utc).isoformat(),
                       strict_diagnostic_hours=sum(len(r['strict_hours']) for r in receipt['results']))
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in receipt.items() if k not in ['queries', 'results']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
