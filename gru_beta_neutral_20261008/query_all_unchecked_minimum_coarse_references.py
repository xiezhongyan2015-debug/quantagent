"""One fresh bounded public session for all 40 previously unchecked hours.

Preserve the original immutable scope and price/quantity gate. Five-minute
observations only validate native minutes; never expand them into minutes.
"""
import csv
import fcntl
import hashlib
import json
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from public_baostock_probe import PublicClient

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_public_archives_20261009/wind17/all_minimum_gap_consolidation'
OUT = SOURCE / 'all_unchecked_coarse_followup'
GRID = list(range(571, 691)) + list(range(781, 901))
PRICE = ['open', 'high', 'low', 'close']
FIELDS = dict(zip(PRICE, ['开盘价', '最高价', '最低价', '收盘价']))


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def normalize(path, date):
    rows = [r for r in csv.DictReader(path.read_text(encoding='gbk').splitlines()) if r['k线结束时间'][:10] == date]
    bars, auction = {}, None
    for raw in rows:
        stamp = datetime.fromisoformat(raw['k线结束时间'])
        minute = stamp.hour * 60 + stamp.minute
        bar = {f: D(raw[key]) for f, key in FIELDS.items()}
        bar.update(volume=D(raw['成交量']) * 100, amount=D(raw['成交额']))
        if minute in GRID:
            assert minute not in bars and stamp.second == 0
            bars[minute] = bar
        elif minute == 570 and bar['volume'] > 0:
            assert auction is None
            auction = bar
    assert sorted(bars) == GRID
    if auction is not None:
        first = bars[571]
        first.update(open=auction['open'], high=max(first['high'], auction['high']), low=min(first['low'], auction['low']),
                     volume=first['volume'] + auction['volume'], amount=first['amount'] + auction['amount'])
    return [bars[i] for i in GRID]


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'all_unchecked_native_eligible_hour_coarse_acquisition.json'
    assert not output.exists()
    scope_path = SOURCE / 'every_actual_missing_hour_diagnostics.csv'
    scope_sha = sha(scope_path)
    scope_proof_path = SOURCE / 'verified_minimum_gap_source_scope.json'
    scope_proof = json.loads(scope_proof_path.read_text())
    assert sha(scope_proof_path) == 'dcacbf380216e03560bd9435c9b9a5724c7f05803eafd4d1aa989f43c5d05ece'
    assert scope_proof['passed'] and scope_sha == scope_proof['hour_diagnostics_csv_sha256']
    rows = list(csv.DictReader(scope_path.open()))
    selected = [r for r in rows if r['native_amount_pass'] == 'True' and r['independent_coarse_checked'] == 'False']
    days = {}
    for r in selected:
        key = r['symbol'], r['date']
        info = days.setdefault(key, {'slots': [], 'raw_path': r['source_raw_path'], 'raw_sha256': r['source_raw_sha256']})
        assert info['raw_path'] == r['source_raw_path'] and info['raw_sha256'] == r['source_raw_sha256']
        info['slots'].append(int(r['slot']))
    assert len(rows) == 3042 and len(selected) == 40 and len(days) == 31
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(meta_path) == '6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    assert sha(meta_path) == scope_proof['candidate_meta_sha256']
    assert sha(meta_path.parent / 'hour_complete.npy') == scope_proof['current_actual_missing_mask_sha256']
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'original_all_3042_scope_csv_sha256': scope_sha, 'current_candidate_meta_sha256': sha(meta_path),
               'verified_original_scope_receipt_sha256': sha(scope_proof_path),
               'actual_unchecked_native_eligible_hours_requested': 40, 'actual_distinct_stockdays_requested': 31,
               'requested_stockdays': [{'symbol': k[0], 'date': k[1], **v} for k, v in sorted(days.items())],
               'public_client_source_sha256': sha(ROOT / 'gru_beta_neutral_20261008/public_baostock_probe.py'),
               'query_budget': 31, 'single_fresh_session_attempt_limit': 1, 'query_frequency': '5',
               'coarse_rows_expanded_as_minutes': False, 'personal_credentials_used': False,
               'status': 'running_one_bounded_public_session', 'queries': [], 'results': [],
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    def persist():
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    persist()
    client = None
    lock = (ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    started = time.monotonic()
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = PublicClient(OUT / 'retained_wire_responses')
        client.login()
        for (symbol, date), request in sorted(days.items()):
            assert len(receipt['queries']) < 31 and time.monotonic() - started < 360
            path = Path(request['raw_path']); assert sha(path) == request['raw_sha256']
            native = normalize(path, date)
            for slot in request['slots']:
                assert all(r['low'] * r['volume'] - 1 <= r['amount'] <= r['high'] * r['volume'] + 1 for r in native[slot * 60:(slot + 1) * 60])
            q = client.query(symbol[-2:].lower() + '.' + symbol[:6], date, date, '5')
            receipt['queries'].append({k: v for k, v in q.items() if k != 'data'})
            payload = q.get('data', [])
            raw_reference = payload.get('record', []) if isinstance(payload, dict) else payload
            ref = [dict(zip(q['fields'], r)) for r in raw_reference] if q['error_code'] == '0' else []
            result = {'symbol': symbol, 'date': date, 'actual_requested_missing_slots': request['slots'],
                      'native_source_path': str(path), 'native_source_sha256': request['raw_sha256'],
                      'coarse_wire_path': q['wire_path'], 'coarse_wire_sha256': q['sha256'], 'coarse_rows': len(ref)}
            if len(ref) != 48:
                result.update(status='coarse_source_no_complete_actual_reference', strict_diagnostic_slots=[])
            else:
                assert [int(r['time'][8:10]) * 60 + int(r['time'][10:12]) for r in ref] == GRID[4::5]
                comparisons = []
                for i, row in enumerate(ref):
                    block = native[i * 5:(i + 1) * 5]
                    bar = {'open': block[0]['open'], 'high': max(r['high'] for r in block), 'low': min(r['low'] for r in block),
                           'close': block[-1]['close'], 'volume': sum(r['volume'] for r in block), 'amount': sum(r['amount'] for r in block)}
                    exact = {f: bar[f] == D(row[f]) for f in PRICE + ['volume', 'amount']}
                    gate = all(exact[f] for f in PRICE) and abs(bar['volume'] - D(row['volume'])) <= 100
                    comparisons.append({'end_minute': GRID[i * 5 + 4], 'native_aggregate': {f: str(v) for f, v in bar.items()},
                                        'reference_native_fields': {f: row[f] for f in PRICE + ['volume', 'amount']}, 'exact_fields': exact,
                                        'original_all_four_prices_exact_and_volume_within_100_shares_gate': gate})
                result.update(status='all_requested_strict_coarse_diagnostics_completed', comparisons=comparisons,
                              strict_diagnostic_slots=[s for s in request['slots'] if all(r['original_all_four_prices_exact_and_volume_within_100_shares_gate'] for r in comparisons[s * 12:(s + 1) * 12])])
            receipt['results'].append(result)
            persist()
            print(json.dumps({k: v for k, v in result.items() if k not in ['comparisons', 'native_source_path', 'coarse_wire_path']}, ensure_ascii=False), flush=True)
            time.sleep(0.5)
        receipt['status'] = 'complete_all_31_stockday_reference_queries'
    except Exception as error:
        receipt.update(status='terminal_bounded_session_failure_preserved', error_type=type(error).__name__, error=str(error))
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                receipt['logout_error_type'] = type(error).__name__
        lock.close()
        completed = {(r['symbol'], r['date']) for r in receipt['results']}
        receipt.update(completed_utc=datetime.now(timezone.utc).isoformat(),
                       requested_missing_hours_with_complete_reference=sum(len(r['actual_requested_missing_slots']) for r in receipt['results'] if r['status'] == 'all_requested_strict_coarse_diagnostics_completed'),
                       requested_missing_hours_without_complete_reference=sum(len(r['slots']) for k, r in days.items() if k not in completed) + sum(len(r['actual_requested_missing_slots']) for r in receipt['results'] if r['status'] != 'all_requested_strict_coarse_diagnostics_completed'),
                       strict_diagnostic_hours=sum(len(r['strict_diagnostic_slots']) for r in receipt['results']),
                       original_immutable_scope_csv_unchanged=sha(scope_path) == scope_sha,
                       remaining_minimum_gap_hours=3042, remaining_full_union_tail_gap_hours=84860)
        assert sha(meta_path) == receipt['current_candidate_meta_sha256']
        persist()
        print(json.dumps({k: v for k, v in receipt.items() if k not in ['queries', 'results', 'requested_stockdays']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
