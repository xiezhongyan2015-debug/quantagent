"""One explicitly source-bound stockday quote overlay; no global price repair.

Default contract is pure. Only explicit preparation reads the pinned13KB
original payload and its existing evidence. No old provider/data is written.
"""
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'gru_research'
TARGET_DATE = '2025-02-10'
TARGET_SYMBOL = 'sh600126'
NAMESPACE = 'TARGET_SOURCE_BOUND_DECIMAL_RESEARCH_V1'
SOURCE_BASE = HERE / 'trading_data/raw_twap_600126_20250210_source_probe_20261002'
FIXED_PINS = {
    'payload': (SOURCE_BASE / 'attempt1/original_20250210_600126.parquet', 13130,
        'f6ced08aae1d09651a18755e13c75b6c39fbe31183efe7d902e0d56e644aacd8'),
    'extraction': (SOURCE_BASE / 'attempt1/single_original_target_extraction_receipt_v1.json', 14138,
        'ec0e37d6c93c54fe8f8bf65da328cf3f00c855691d01f0c9457956c990bbc0ee'),
    'four_window_evidence': (SOURCE_BASE / 'root_source_bound_four_window_decimal_quotes_v1.json', 6333,
        'cc74d05c1108aa62656d03e07cf6592b4c7faeee71d96756d2abbd6610f14584'),
    'old_hourly': (HERE / 'artifacts/minute_hourly_raw/2025-02-10.parquet', 318834,
        '43d5567b73f74ff31febaa71ede3ef56a04ccaa6105f98571cbe4af3f8b35d39'),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_pin(path, expected_sha, maximum_bytes):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= maximum_bytes, 'missing/oversize pinned file')
    body = path.read_bytes()
    require(hashlib.sha256(body).hexdigest() == expected_sha, 'pinned source bytes changed: ' + str(path))
    return body


def read_contract(path, expected_sha):
    body = read_pin(path, expected_sha, 1000000)
    contract = json.loads(body)
    require(contract['namespace'] == NAMESPACE and contract['target_symbol'] == TARGET_SYMBOL
            and contract['target_date'] == TARGET_DATE, 'unregistered target namespace')
    require(set(contract['new_sources']) == {'exact_decimal_twap_v1.py', 'target_decimal_quote_overlay_v1.py',
                                          'target_decimal_research_replay_v1.py'}, 'exact new source roster required')
    for name, expected in contract['new_sources'].items():
        read_pin(HERE / name, expected, 100000)
    require(contract['execute_authorized_by_contract'] is False, 'preparation cannot claim execution authorization')
    return contract


def guard(contract, contract_path, contract_sha, overlay_path=None, overlay_sha=None):
    current = read_contract(contract_path, contract_sha)
    require(current == contract, 'research contract changed')
    for name, (path, size, expected) in FIXED_PINS.items():
        body = read_pin(path, expected, size)
        require(len(body) == size, 'fixed source size differs: ' + name)
    for item in contract['old_small_sources'].values():
        read_pin(item['path'], item['sha256'], item['bytes'])
    if overlay_path is not None:
        read_pin(overlay_path, overlay_sha, 100000)


def semantic_identity(overlay):
    content = {key: overlay[key] for key in ('namespace', 'target_symbol', 'target_date',
                                           'source_pins', 'conversion_semantics', 'quotes')}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def build_overlay(contract, contract_path, contract_sha):
    """Validate the actual240 source rows; compute only four five-open means."""
    guard(contract, contract_path, contract_sha)
    sys.path.insert(0, str(HERE / '.deps'))
    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from exact_decimal_twap_v1 import exact_five_open_twap
    bodies = {name: read_pin(path, expected, size) for name, (path, size, expected) in FIXED_PINS.items()}
    extraction, evidence = json.loads(bodies['extraction']), json.loads(bodies['four_window_evidence'])
    require(extraction['status'] == 'PASS_SINGLE_ORIGINAL_TARGET_EXTRACTED_AND_ACTUAL_FIVE_RAW_ROWS_VALIDATED'
            and extraction['actual_target']['payload']['sha256'] == FIXED_PINS['payload'][2], 'original extraction evidence differs')
    require(evidence['date'] == TARGET_DATE and evidence['symbol'] == TARGET_SYMBOL
            and evidence['payload']['sha256'] == FIXED_PINS['payload'][2], 'four-window source identity differs')
    table = pq.read_table(pa.BufferReader(bodies['payload']))
    names = ['股票代码', 'datetime', 'datetime_nano', 'open', 'high', 'low', 'close', 'volume',
             'qfq_ratio', 'qfq_open', 'qfq_high', 'qfq_low', 'qfq_close']
    require(len(table) == 240 and set(names) <= set(table.column_names), 'exact original240 schema required')
    require(str(table.schema.field('open').type) == 'double' and str(table.schema.field('volume').type) == 'int64',
            'actual FP64 opens/integer shares schema required')
    a = {name: table[name].to_numpy(zero_copy_only=False) for name in names}
    midnight = pd.Timestamp(TARGET_DATE, tz='Asia/Shanghai').tz_convert('UTC').value
    offsets = np.r_[np.arange(570, 690), np.arange(780, 900)]
    expected_clock = midnight + offsets * 60 * 10**9
    text_clock = np.asarray(a['datetime']).astype('datetime64[ns]').astype(np.int64) - 8 * 3600 * 10**9
    require(np.array_equal(a['datetime_nano'], expected_clock) and np.array_equal(text_clock, expected_clock)
            and np.all(a['股票代码'].astype(str) == '600126'), 'source dual clock/order/symbol mismatch')
    raw = np.column_stack([a[name] for name in ['open', 'high', 'low', 'close']])
    adjusted = np.column_stack([a[name] for name in ['qfq_open', 'qfq_high', 'qfq_low', 'qfq_close']])
    factor = a['qfq_ratio']
    require(np.isfinite(raw).all() and (raw > 0).all() and np.isfinite(adjusted).all() and (adjusted > 0).all(),
            'invalid original raw/adjusted prices')
    require((raw[:, 1] >= raw[:, [0, 2, 3]].max(1)).all()
            and (raw[:, 2] <= raw[:, [0, 1, 3]].min(1)).all(), 'original raw OHLC inconsistent')
    require(np.isfinite(factor).all() and (factor > 0).all() and np.array_equal(factor, np.full(240, factor[0])),
            'intraday factor mismatch')
    # Preserve the frozen source relation tolerance; never use it for price bounds.
    require(np.isclose(adjusted, raw * factor[:, None], rtol=1e-7, atol=1e-6).all(), 'raw/qfq relation differs')
    require((a['volume'] >= 0).all(), 'negative original shares')
    old = pq.read_table(pa.BufferReader(bodies['old_hourly']), filters=[('symbol', '=', '600126.SH')]).to_pandas()
    require(len(old) == 4 and set(old.slot) == {0, 1, 2, 3}, 'four sealed old windows required')
    old = old.set_index('slot')
    records = []
    starts = (571, 631, 781, 841)
    require(len(evidence['quotes']) == 4, 'four-window evidence budget changed')
    for slot, start in enumerate(starts):
        pos = np.flatnonzero((offsets >= start) & (offsets < start + 5))
        require(len(pos) == 5 and np.array_equal(offsets[pos], np.arange(start, start + 5)), 'five real minute grid required')
        strings = [str(float(value)) for value in a['open'][pos]]
        hexbits = [float(value).hex() for value in a['open'][pos]]
        volume = [int(value) for value in a['volume'][pos]]
        price = exact_five_open_twap(strings)
        first = int(expected_clock[pos[0]])
        end = first + 5 * 60 * 10**9
        row, witness = old.loc[slot], evidence['quotes'][slot]
        require(witness['slot'] == slot and witness['five_original_row_ordinals'] == pos.tolist()
                and witness['five_left_UTC_ns'] == a['datetime_nano'][pos].tolist()
                and witness['five_observed_FP64_raw_opens_shortest_roundtrip_decimals'] == strings
                and witness['five_raw_open_binary_hex'] == hexbits and witness['five_actual_volume_shares'] == volume,
                'actual five rows differ from root-bound source evidence')
        require(sum(volume) > 0 and sum(volume) == witness['window_volume_shares'] == row.window_volume,
                'actual observed volume differs')
        require(first == witness['window_start_UTC_ns'] == int(row.window_start_time)
                and end == witness['window_complete_end_UTC_ns'] == int(row.window_end_time), 'old execution clocks differ')
        require(bool(row.window_complete) and bool(row.window_tradable) and bool(row.window_all_flat)
                and (raw[pos, 1] == raw[pos, 2]).all(), 'target verified window flags differ')
        require(str(price) == witness['source_bound_exact_decimal_mean']
                and float(a['open'][pos].mean()) == float(row.window_raw_price) == witness['sealed_original_double_mean'],
                'decimal or legacy-double arithmetic differs from witness')
        records.append(dict(slot=slot, start_UTC_ns=first, end_UTC_ns=end, individual_open_decimals=strings,
            individual_open_binary_hex=hexbits, original_row_ordinals=pos.tolist(), volume_shares=sum(volume),
            exact_decimal_price=str(price), original_double_as_decimal=str(float(row.window_raw_price)),
            all_five_minutes_flat=True, complete=True, observed_positive_volume=True))
    overlay = dict(namespace=NAMESPACE, target_symbol=TARGET_SYMBOL, target_date=TARGET_DATE,
        source_pins={name:dict(path=str(path), bytes=size, sha256=expected) for name,(path,size,expected) in FIXED_PINS.items()},
        conversion_semantics='Each original FP64 individual open -> shortest roundtrip str(float) -> Decimal; exact mean of5. Not original vendor text, float aggregate rounding, cent quantization or limit clipping.',
        quotes=records, old_provider_or_rules_or_data_modified=False, quote_eligibility_fields_changed=False,
        original_arrival_assumption_retained=True, independently_verified_historical_arrival=False,
        engine_executed=False, full_PLAN_complete=False, economic_target_accepted=False)
    overlay['overlay_id'] = semantic_identity(overlay)
    return overlay


def _true_boolean(value):
    """Accept producer-native bool/np.bool_ only; never general truthy values."""
    if type(value) is bool:
        return value
    if type(value).__module__ == 'numpy':
        import numpy as np
        return type(value) is np.bool_ and bool(value)
    return False


def patch_window_fields(window, overlay):
    """Pure field replacement; surrounding factory performs actual byte guards."""
    if TARGET_SYMBOL not in window.quotes:
        return window, None
    matches = [row for row in overlay['quotes'] if row['start_UTC_ns'] == window.execution_start]
    if not matches:
        return window, None
    require(len(matches) == 1, 'ambiguous overlay execution window')
    row = matches[0]
    require(window.execution_end == row['end_UTC_ns'] and window.source == overlay['source_pins']['old_hourly']['path'],
            'matched target overlay source/end differs')
    old = window.quotes[TARGET_SYMBOL]
    require(old.symbol == TARGET_SYMBOL and _true_boolean(old.complete) and _true_boolean(old.observed_positive_volume)
            and _true_boolean(old.all_five_minutes_flat) and old.reason is None, 'old target quote eligibility must remain verified')
    require(old.raw_price == Decimal(row['original_double_as_decimal']), 'old target quote price differs')
    quotes = dict(window.quotes)
    quotes[TARGET_SYMBOL] = replace(old, raw_price=Decimal(row['exact_decimal_price']))
    updated = replace(window, quotes=quotes, price_kind=window.price_kind + '|' + NAMESPACE)
    event = dict(namespace=NAMESPACE, overlay_id=overlay['overlay_id'], symbol=TARGET_SYMBOL,
        signal_time_utc_ns=window.signal_time, execution_start=window.execution_start, execution_end=window.execution_end,
        old_quote_raw_price=str(old.raw_price), new_quote_raw_price=row['exact_decimal_price'],
        original_eligibility_fields_unchanged=True, source_payload_sha256=overlay['source_pins']['payload']['sha256'],
        source_payload_path=overlay['source_pins']['payload']['path'],
        root_four_window_evidence=overlay['source_pins']['four_window_evidence'],
        original_row_ordinals=row['original_row_ordinals'], actual_individual_open_decimals=row['individual_open_decimals'],
        actual_individual_open_binary_hex=row['individual_open_binary_hex'], volume_shares=row['volume_shares'],
        override_reason='Replace only the binary64 aggregate tail with the exact Decimal mean of source-bound individual FP64 shortest-roundtrip opens',
        old_hourly_path_SHA_is_inherited_file_reference_not_new_quote_numeric_source=True)
    return updated, event


def make_source_class(original_class, overlay, check_guard):
    class TargetBoundSource(original_class):
        def __init__(self, mapping):
            check_guard()
            super().__init__(mapping)
            self.target_overlay_events = []

        def window(self, signal_time, symbols):
            window = super().window(signal_time, symbols)
            updated, event = patch_window_fields(window, overlay)
            if event is not None:
                check_guard()
                self.target_overlay_events.append(event)
                # Retain old source-access fields, with explicit second source identity.
                self.accesses[-1]['target_decimal_quote_overlay'] = event
            return updated
    return TargetBoundSource


def contract():
    return dict(status='PREPARED_ONLY_ONE_STOCKDAY_SOURCE_BOUND_DECIMAL_OVERLAY', namespace=NAMESPACE,
        target_symbol=TARGET_SYMBOL, target_date=TARGET_DATE, window_budget=4, individual_raw_open_count=20,
        no_original_provider_source_or_data_writes=True, no_global_epsilon_round_clip_or_fill=True,
        research_execution_performed=False, company_events_and_fraction_unknown_unchanged=True,
        economic_target_accepted=False, full_PLAN_complete=False)


if __name__ == '__main__':
    print(json.dumps(contract(), sort_keys=True))
