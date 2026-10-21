"""Examine only actually missing 2018 hours; each traded hour needs real volume."""
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
DAY_FLAGS = ['source_rows_valid', 'grid_valid', 'primary_day_trading', 'open_agrees',
             'close_agrees', 'volume_difference_within_100_shares', 'quotes_inside_primary_range']


def main():
    ap = OUT / 'all_2018_gap_audit_receipt.json'
    path = OUT / 'hour_diagnostics_receipt.json'
    assert not path.exists()
    audit = json.loads(ap.read_text())
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'daily_audit_path': str(ap), 'daily_audit_sha256': sha(ap),
               'normalization_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/verify_wind17_all_2023_gap_findings.py'),
               'public_client_source_sha256': sha(ROOT / 'gru_beta_neutral_20261008/public_baostock_probe.py'),
               'results': [], 'queries': [], 'query_budget': 64, 'status': 'running',
               'selection_scope': 'Actual current missing slots only; day O/C/grid/quantity/price-range gates and each selected hour positive volume; all-day positivity is recorded separately',
               'original_donor_gate_changed': False, 'candidate_changed': False, 'candidate_hours_admitted': 0,
               'coarse_expanded_as_minutes': False, 'reported_minute_quantity_precision_certified': False}
    lock = (ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = None
    try:
        for item in audit['results']:
            symbol = item['canonical_symbol']
            assert item['status'] == 'all_2018_target_stockday_diagnostics_complete'
            assert sha(item['raw_path']) == item['raw_sha256']
            frame = pd.read_csv(item['raw_path'], encoding='gbk', dtype=str).rename(columns=FIELDS)
            frame.index = pd.to_datetime(frame['k线结束时间'])
            for info in item['target_stockday_checks']:
                date, missing = info['date'], info['actual_missing_slots']
                result = {'symbol': symbol, 'date': date, 'actual_missing_slots': missing}
                if not all(info.get(k) for k in DAY_FLAGS):
                    result.update(status='failed_daily_source_gate', strict_hours=[])
                    receipt['results'].append(result)
                    continue
                _, reg, _ = normalized_day(frame, date)
                good = [r.low*r.volume - 1 <= r.amount <= r.high*r.volume + 1 for r in reg.itertuples()]
                positive = [s for s in missing if sum(reg.iloc[s*60:(s+1)*60].volume) > 0]
                eligible = [s for s in positive if all(good[s*60:(s+1)*60])]
                result.update(positive_volume_missing_slots=positive, native_amount_pass_missing_slots=eligible)
                if not eligible:
                    result.update(status='no_missing_hour_passes_positive_volume_and_amount', strict_hours=[])
                    receipt['results'].append(result)
                    continue
                assert len(receipt['queries']) < receipt['query_budget']
                if client is None:
                    client = PublicClient(OUT / 'coarse_wire_responses')
                    client.login()
                q = client.query(symbol[-2:].lower() + '.' + symbol[:6], date, date, '5')
                receipt['queries'].append({k: v for k, v in q.items() if k != 'data'})
                result.update(coarse_wire_path=q['wire_path'], coarse_wire_sha256=q['sha256'])
                data = q.get('data', [])
                rows = data.get('record', []) if isinstance(data, dict) else data
                rows = [dict(zip(q['fields'], r)) for r in rows] if q['error_code'] == '0' else []
                time.sleep(1)
                if len(rows) != 48:
                    result.update(status='coarse_reference_absent_or_incomplete', coarse_rows=len(rows), strict_hours=[])
                    receipt['results'].append(result)
                    continue
                flags = []
                for i, reference in enumerate(rows):
                    block = reg.iloc[i*5:(i+1)*5]
                    assert block.index[-1] == pd.to_datetime(reference['time'], format='%Y%m%d%H%M%S%f')
                    raw = {'open': block.open.iloc[0], 'high': max(block.high), 'low': min(block.low), 'close': block.close.iloc[-1]}
                    flags.append(all(raw[f] == Decimal(reference[f]) for f in PRICE) and abs(sum(block.volume)-Decimal(reference['volume'])) <= 100)
                strict = [s for s in eligible if all(flags[s*12:(s+1)*12])]
                result.update(status='strict_missing_hour_diagnostics_complete', strict_hours=strict,
                              coarse_OHLC_volume_pass_buckets=sum(flags))
                receipt['results'].append(result)
            path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        receipt['status'] = 'complete_all_2018_missing_hour_diagnostics'
    except Exception as error:
        receipt.update(status='failed', error_type=type(error).__name__, error=str(error))
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
        print(json.dumps({k: v for k, v in receipt.items() if k not in ['results', 'queries']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
