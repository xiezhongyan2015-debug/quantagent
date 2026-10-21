"""Read-only QA of pinned Sina sample; no missing-minute synthesis or admission."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
OUT = BASE / 'sina_latest_minutes_20261009'


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    receipt = json.loads((OUT / 'sample_probe_receipt.json').read_text())
    source = OUT / 'sh600004_m1_1970_raw.txt'
    assert sha(source) == receipt['source_sha256']
    raw = source.read_text()
    rows, _ = json.JSONDecoder().raw_decode(raw[raw.index('['):])
    assert isinstance(rows, list) and len(rows) == receipt['rows']
    primary = BASE / 'baostock/union_daily_factors/normalized/600004.SH.json'
    obj = json.loads(primary.read_text())
    daily = {r[0]: dict(zip(obj['fields'], r)) for r in obj['rows']}
    grids = [list(range(571, 631)), list(range(631, 691)), list(range(781, 841)), list(range(841, 901))]
    days = {}
    for row in rows:
        day, clock = row['day'].split(' ')
        hour, minute, second = map(int, clock.split(':'))
        assert second == 0
        values = [float(row[k]) for k in ['open', 'high', 'low', 'close']]
        assert all(x > 0 for x in values)
        assert values[1] >= max(values) and values[2] <= min(values)
        assert float(row['volume']) >= 0 and float(row['amount']) >= 0
        days.setdefault(day, []).append((hour*60+minute, row))
    checks = []
    for day, entries in sorted(days.items()):
        if not '2026-08-07' <= day <= '2026-09-23':
            continue
        entries.sort()
        clocks = [x[0] for x in entries]
        assert len(clocks) == len(set(clocks))
        p = daily[day]
        assert p['adjustflag'] == '3'
        prices = [x[1] for x in entries]
        volume = sum(float(x['volume']) for x in prices)
        amount = sum(float(x['amount']) for x in prices)
        tests = {
            'observed_open_equals_primary_daily': float(prices[0]['open']) == float(p['open']),
            'observed_close_equals_primary_daily': float(prices[-1]['close']) == float(p['close']),
            'observed_volume_within_100_shares_of_daily': abs(volume-float(p['volume'])) <= 100,
            'observed_prices_inside_primary_daily_range': all(float(x['high']) <= float(p['high'])+1e-8 and float(x['low']) >= float(p['low'])-1e-8 for x in prices),
        }
        complete = [all(m in clocks for m in grid) for grid in grids]
        checks.append({'date': day, 'observed_rows': len(entries),
                       'missing_expected_end_label_minutes': [f'{m//60:02d}:{m%60:02d}' for m in sum(grids, []) if m not in clocks],
                       'unexpected_minutes': [f'{m//60:02d}:{m%60:02d}' for m in clocks if m not in sum(grids, [])],
                       'first_observed_clock': prices[0]['day'], 'last_observed_clock': prices[-1]['day'],
                       'observed_day_volume': volume, 'primary_day_volume': float(p['volume']),
                       'observed_day_amount': amount, 'primary_day_amount': float(p['amount']),
                       **tests, 'complete_60_observed_minute_hours': complete,
                       'potential_complete_hours_if_timestamp_semantics_verified': sum(complete) if all(tests.values()) else 0,
                       'end_label_timestamp_semantics_certified': False, 'admitted_to_candidate': False})
    extended = json.loads((OUT/'extended_sample_probe_receipt.json').read_text())
    assert sha(OUT/'sh600004_m1_12000_raw.txt') == extended['source_sha256']
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(Path(__file__)),
              'source_sample_sha256': sha(source), 'independent_daily_source_sha256': sha(primary),
              'symbol': '600004.SH', 'checks': checks,
              'extended_12000_row_request_observed_rows': extended['observed_rows'],
              'extended_12000_row_request_sha256': extended['source_sha256'],
              'full_tail_recovered': False, 'timestamp_semantics_certified': False,
              'source_admitted_to_candidate': False, 'missing_minutes_created': False,
              'credentials_used': False, 'production_ready': False}
    (OUT/'independent_sample_quality_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
