"""Isolate uniquely invertible adjusted observations; never choose ambiguous ticks.

Research donor only. Pure multiplicative adjustment and nearest rounding are
explicit assumptions, independently checked against original raw control bars.
Do not repair the disputed opening hour or modify any existing panel.
"""
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
SOURCE = BASE / 'huggingface/joinquant_targeted_concordance_20261009'
OUT = SOURCE / 'singleton_partial_recovery'
FIELDS = ['open', 'high', 'low', 'close']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def inverse(value, factor, tick):
    # The entire inverse image of the observed rounded value, with no raw
    # reference used to select a candidate inside that image.
    unit = Decimal(tick)
    value, factor = Decimal(value), Decimal(factor)
    lower = (value - unit / 2) / factor
    upper = (value + unit / 2) / factor
    lo = int((lower / unit).to_integral_value(rounding=ROUND_CEILING))
    hi = int((upper / unit).to_integral_value(rounding=ROUND_CEILING)) - 1
    return None if lo != hi else Decimal(lo) * unit


def inverse_volume(value, factor):
    value, factor = Decimal(value), Decimal(factor)
    lower, upper = (value - Decimal('.5')) * factor, (value + Decimal('.5')) * factor
    lo = int(lower.to_integral_value(rounding=ROUND_CEILING))
    hi = int(upper.to_integral_value(rounding=ROUND_CEILING)) - 1
    return None if lo != hi else lo


def rows(path, date):
    with path.open(newline='') as stream:
        return [row for row in csv.DictReader(stream) if row['Unnamed: 0'].startswith(date)]


def main():
    OUT.mkdir(exist_ok=True)
    audit_path = SOURCE / 'targeted_concordance_receipt.json'
    audit = json.loads(audit_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_targeted_adjusted_minute_archive.py') == audit['source_code_sha256']
    for source in audit['sources']:
        assert sha(source['full_path']) == source['full_sha256']
        assert sha(source['selected_path']) == source['selected_sha256']
    control = next(r for r in audit['samples'] if r['date'] == '2022-06-06')
    assert sha(control['original_raw_minute_path']) == control['original_raw_minute_sha256']
    original = pq.read_table(control['original_raw_minute_path']).to_pandas().sort_values('datetime_nano')
    adjusted = rows(SOURCE / '002831_target_stockdays.csv', '2022-06-06')
    assert len(adjusted) == len(original) == 240
    for i, row in enumerate(adjusted):
        factor = Decimal(row['factor'])
        projected_close = (Decimal(str(original.close.iloc[i])) * factor).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        assert projected_close == Decimal(row['close'])
        recovered_volume = inverse_volume(row['volume'], row['factor'])
        assert recovered_volume == int(original.volume.iloc[i])
    source = next(s for s in audit['sources'] if s['symbol'] == '000099.SZ')
    group = rows(Path(source['selected_path']), '2018-06-27')
    assert len(group) == 240
    daily = json.loads(Path(source['primary_daily_path']).read_text())
    reference = next(dict(zip(daily['fields'], row)) for row in daily['rows'] if row[0] == '2018-06-27')
    full_volume = [inverse_volume(row['volume'], row['factor']) for row in group]
    assert all(value is not None and value >= 0 for value in full_volume)
    assert sum(full_volume) == int(reference['volume']) and reference['tradestatus'] == '1'
    assert inverse(group[-1]['close'], group[-1]['factor'], '.01') == Decimal(reference['close'])
    recovered, rejected_hours, amount_conflicts = [], [], []
    for slot in range(4):
        hour = group[slot * 60: (slot + 1) * 60]
        reasons = []
        if slot == 0:
            reasons.append('first_hour_opening_auction_or_convention_disagreement_unresolved')
        decoded = []
        for row in hour:
            price = {field: inverse(row[field], row['factor'], '.01') for field in FIELDS}
            if any(value is None for value in price.values()):
                reasons.append('at_least_one_price_has_multiple_possible_raw_cent_ticks')
                continue
            assert Decimal(reference['low']) <= min(price.values()) <= max(price.values()) <= Decimal(reference['high'])
            assert price['low'] <= min(price['open'], price['close']) <= max(price['open'], price['close']) <= price['high']
            volume = inverse_volume(row['volume'], row['factor'])
            assert volume is not None and volume >= 0
            assert row['paused'] == '0.0'
            # Retained integer amounts may be nearest-rounded or truncated.
            amount = Decimal(row['money'])
            if not price['low'] * volume - Decimal('1') <= amount <= price['high'] * volume + Decimal('1'):
                reasons.append('observed_amount_outside_inferred_raw_price_range_times_volume')
                amount_conflicts.append({'timestamp': row['Unnamed: 0'], 'slot_zero_based': slot,
                    'singleton_raw_prices_under_assumed_transform': {field: str(value) for field, value in price.items()},
                    'singleton_volume_under_assumed_transform': volume, 'observed_amount': str(amount),
                    'implied_amount_lower_bound': str(price['low'] * volume),
                    'implied_amount_upper_bound': str(price['high'] * volume), 'source_row': row})
            decoded.append({'timestamp': row['Unnamed: 0'], 'symbol': '000099.SZ',
                **{field: format(value, '.2f') for field, value in price.items()},
                'volume': str(volume), 'observed_money': row['money'], 'source_factor': row['factor'], 'slot_zero_based': str(slot)})
        if reasons:
            rejected_hours.append({'slot_zero_based': slot, 'reasons': sorted(set(reasons))})
        else:
            assert len(decoded) == 60 and sum(int(row['volume']) for row in decoded) > 0
            recovered.extend(decoded)
    assert not recovered and {r['slot_zero_based'] for r in rejected_hours} == {0, 1, 2, 3}
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'status': 'unique_inverse_hypothesis_did_not_pass_amount_admission',
        'source_code_sha256': sha(Path(__file__)), 'source_audit_path': str(audit_path), 'source_audit_sha256': sha(audit_path),
        'symbol': '000099.SZ', 'date': '2018-06-27',
        'minutes_admitted': len(recovered), 'research_donor_hours': 0,
        'first_hour_and_ambiguous_second_hour_excluded': rejected_hours,
        'additional_amount_conflicts': amount_conflicts,
        'amount_rounding_or_truncation_tolerance_currency_units': 1,
        'control_stockday': '002831.SZ 2022-06-06', 'control_raw_close_round_trip_matches': 240,
        'control_raw_volume_singleton_inverse_matches': 240,
        'full_day_singleton_recovered_volume_equals_primary': True,
        'last_singleton_recovered_close_equals_primary': True,
        'assumptions': ['Source prices equal raw prices multiplied by its supplied factor then rounded to the nearest cent',
                        'Source volume equals raw shares divided by its supplied factor then rounded to the nearest share'],
        'transformation_applied': 'Singleton inverse images evaluated for diagnosis only; no output prices or minutes created because quality admission failed',
        'third_party_original_provider_identity_not_certified': True,
        'first_hour_daily_open_gate_not_relaxed': True, 'retrospective_only_not_used_as_PIT_factor': True,
        'candidate_hours_admitted': 0, 'candidate_changed': False, 'production_ready': False,
        'integration_pending': False, 'source_or_transform_disagreement_requires_resolution': True}
    (OUT / 'isolated_recovery_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
