"""Recompute two original-source controls using exact CSV string arithmetic."""
import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_public_archives_20261009/wind17'
OUT = BASE / 'original_300114_controls'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    ap = OUT / 'original_control_receipt.json'
    output = OUT / 'independent_original_control_verification.json'
    assert not output.exists()
    audit = json.loads(ap.read_text())
    assert sha(audit['native_source_path']) == audit['native_source_sha256']
    assert sha(audit['original_audit_path']) == audit['original_audit_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_original_controls.py') == audit['source_code_sha256']
    with Path(audit['native_source_path']).open(encoding='gbk', newline='') as stream:
        native = {r['k线结束时间']: r for r in csv.DictReader(stream)}
    verified = []
    for expected in audit['results']:
        assert sha(expected['original_payload_path']) == expected['original_payload_sha256']
        rows = sorted(pq.read_table(expected['original_payload_path']).to_pylist(), key=lambda r: r['datetime_nano'])
        assert len(rows) == 240
        pairs = []
        for original in rows:
            stamp = datetime.fromisoformat(str(original['datetime']))
            end = stamp + timedelta(minutes=1)
            r = native[end.strftime('%Y-%m-%d %H:%M:%S')]
            prices = {f: Decimal(r[k]) for f, k in [('open', '开盘价'), ('high', '最高价'), ('low', '最低价'), ('close', '收盘价')]}
            v, money = Decimal(r['成交量']) * 100, Decimal(r['成交额'])
            if end.strftime('%H:%M') == '09:31':
                auction = native[expected['date'] + ' 09:30:00']
                if Decimal(auction['成交量']) > 0:
                    prices['open'] = Decimal(auction['开盘价'])
                    prices['high'] = max(prices['high'], Decimal(auction['最高价']))
                    prices['low'] = min(prices['low'], Decimal(auction['最低价']))
                    v += Decimal(auction['成交量']) * 100
                    money += Decimal(auction['成交额'])
            pairs.append((original, prices, v, money, end))
        exact = {f: sum(Decimal(str(o[f])) == p[f] for o, p, v, m, t in pairs) for f in ['open', 'high', 'low', 'close']}
        differences = [v - Decimal(str(o['volume'])) for o, p, v, m, t in pairs]
        original_cum = native_cum = Decimal(0)
        cumulative, rounded, truncated = [], 0, 0
        for o, p, v, money, stamp in pairs:
            original_cum += Decimal(str(o['volume']))
            native_cum += v
            cumulative.append(native_cum - original_cum)
            rounded += native_cum / 100 == (original_cum / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            truncated += native_cum / 100 == (original_cum / 100).quantize(Decimal('1'), rounding=ROUND_DOWN)
        conflicts = [t.strftime('%H:%M') for o, p, v, m, t in pairs if not p['low'] * v - 1 <= m <= p['high'] * v + 1]
        values = {'exact_minute_prices_by_field': exact, 'exact_minute_volumes': differences.count(0),
                  'minute_volumes_within_100_shares': sum(abs(d) <= 100 for d in differences),
                  'maximum_minute_volume_difference_shares': float(max(map(abs, differences))),
                  'native_minus_original_day_volume_shares': float(sum(differences)),
                  'cumulative_volume_times_within_100_shares': sum(abs(d) <= 100 for d in cumulative),
                  'maximum_cumulative_volume_difference_shares': float(max(map(abs, cumulative))),
                  'cumulative_native_lots_equal_original_cumulative_lots_rounded_half_up': rounded,
                  'cumulative_native_lots_equal_original_cumulative_lots_truncated': truncated,
                  'native_amount_price_volume_conflict_count': len(conflicts),
                  'native_amount_price_volume_conflict_clocks': conflicts}
        assert all(expected[k] == v for k, v in values.items()), expected['date']
        verified.append({'date': expected['date'], **values})
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
               'source_code_sha256': sha(__file__), 'audited_receipt_sha256': sha(ap),
               'method': 'Independent csv.DictReader string Decimals and native original Arrow rows; no audit code imported',
               'results': verified, 'candidate_changed': False, 'candidate_hours_admitted': 0,
               'precision_reconstructed': False, 'whole_market_or_tick_source_certified': False}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({'passed': True, 'dates_verified': [r['date'] for r in verified]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
