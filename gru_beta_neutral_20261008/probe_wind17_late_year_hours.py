"""Check exact native later-year minute quantities before any candidate use."""
import fcntl
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from public_baostock_probe import PublicClient
from audit_wind17_target_gaps import ROOT, SOURCE, FIELDS, PRICE, END, sha
import pandas as pd
import numpy as np

OUT = SOURCE / 'remaining_late_years'


def main():
    path = OUT / 'late_year_hour_diagnostics_receipt.json'
    assert not path.exists()
    ap = OUT / 'acquisition_and_daily_diagnostics_receipt.json'
    audit = json.loads(ap.read_text())
    assert audit['completed_utc']
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'acquisition_daily_receipt_path': str(ap), 'acquisition_daily_receipt_sha256': sha(ap),
               'public_client_source_sha256': sha(ROOT / 'gru_beta_neutral_20261008/public_baostock_probe.py'),
               'status': 'running', 'results': [], 'queries': [], 'new_query_budget': 32,
               'coarse_expanded_as_minutes': False, 'candidate_changed': False,
               'candidate_hours_admitted': 0, 'production_ready': False,
               'minute_producer_semantics_and_volume_precision_certified': False}
    lock = (ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = None
    try:
        for item in audit['results']:
            symbol = item['canonical_symbol']
            if item['status'] != 'exact_CSV_acquired_and_daily_diagnostics_complete':
                receipt['results'].append({'symbol': symbol, 'year': item['year'], 'status': 'no_verified_native_stockyear', 'hours': []})
                continue
            assert sha(item['raw_path']) == item['raw_sha256']
            frame = pd.read_csv(item['raw_path'], encoding='gbk', dtype=str).rename(columns=FIELDS)
            frame.index = pd.to_datetime(frame['k线结束时间'])
            for day_info in item['target_stockday_checks']:
                date = day_info['date']
                result = {'symbol': symbol, 'date': date, 'existing_missing_hours': day_info['existing_missing_hours']}
                if not day_info['existing_daily_and_hour_gates_passed']:
                    result.update(status='failed_prior_daily_gate', hours=[])
                    receipt['results'].append(result)
                    continue
                day = frame.loc[date].copy().sort_index()
                clocks = day.index.hour * 60 + day.index.minute
                reg = day[np.isin(clocks, END)].copy()
                assert len(reg) == 240
                for field in PRICE + ['amount']:
                    reg[field] = reg[field].map(Decimal)
                reg['volume'] = reg.volume_lots.map(Decimal) * 100
                auction = day[(clocks == 570) & day.volume_lots.map(Decimal).gt(0)]
                if len(auction):
                    a, first = auction.iloc[0], reg.index[0]
                    reg.loc[first, 'open'] = Decimal(a.open)
                    reg.loc[first, 'high'] = max(reg.high.iloc[0], Decimal(a.high))
                    reg.loc[first, 'low'] = min(reg.low.iloc[0], Decimal(a.low))
                    reg.loc[first, 'volume'] += Decimal(a.volume_lots) * 100
                    reg.loc[first, 'amount'] += Decimal(a.amount)
                flags = [r.low * r.volume - 1 <= r.amount <= r.high * r.volume + 1 for r in reg.itertuples()]
                eligible = [s for s in range(4) if all(flags[s*60:(s+1)*60])]
                result.update(native_amount_pass_slots=eligible, minute_amount_conflicts=flags.count(False))
                if not eligible:
                    result.update(status='all_hours_failed_native_amount_bounds', hours=[])
                    receipt['results'].append(result)
                    continue
                assert len(receipt['queries']) < receipt['new_query_budget']
                if client is None:
                    client = PublicClient(OUT / 'coarse_wire_responses')
                    client.login()
                query = client.query(symbol[-2:].lower() + '.' + symbol[:6], date, date, '5')
                receipt['queries'].append({k: v for k, v in query.items() if k != 'data'})
                result.update(coarse_wire_path=query['wire_path'], coarse_wire_sha256=query['sha256'])
                data = query.get('data', [])
                rows = data.get('record', []) if isinstance(data, dict) else data
                rows = [dict(zip(query['fields'], r)) for r in rows] if query['error_code'] == '0' else []
                time.sleep(1)
                if len(rows) != 48:
                    result.update(status='coarse_reference_absent_or_incomplete', coarse_rows=len(rows), hours=[])
                    receipt['results'].append(result)
                    continue
                coarse = pd.DataFrame(rows)
                coarse.index = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
                assert list(coarse.index.hour * 60 + coarse.index.minute) == END[4::5].tolist()
                grouped = reg.groupby(pd.Grouper(freq='5min', label='right', closed='right')).agg(
                    {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).loc[coarse.index]
                good = []
                for stamp, row in coarse.iterrows():
                    native = grouped.loc[stamp]
                    good.append(all(native[f] == Decimal(row[f]) for f in PRICE) and abs(native.volume - Decimal(row.volume)) <= 100)
                survived = [s for s in eligible if all(good[s*12:(s+1)*12])]
                result.update(status='strict_hour_diagnostics_complete', hours=survived, exact_5m_OHLC_volume_buckets=sum(good))
                receipt['results'].append(result)
            path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print(json.dumps({'symbol': symbol, 'year': item['year'], 'diagnostic_strict_hours_so_far': sum(len(r['hours']) for r in receipt['results']), 'queries': len(receipt['queries'])}, ensure_ascii=False), flush=True)
        receipt['status'] = 'complete_late_year_minimum_gap_hour_diagnostics'
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
        receipt.update(completed_utc=datetime.now(timezone.utc).isoformat(), strict_diagnostic_hours=sum(len(r['hours']) for r in receipt['results']))
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
