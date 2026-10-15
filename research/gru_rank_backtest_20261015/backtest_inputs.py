"""Export frozen scores or form PCF targets; never trade or calculate returns.

The packaged OLD_RANK scores are already processed head4 scores. Do not rank,
standardize or neutralize them a second time. Dates are local exchange dates;
signal_time_ns is the original UTC nanosecond timestamp, exactly 15:00 Shanghai.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

OLD_RANK_SHA256 = 'e402d5a3c48ad45bd3d4c5e8c4fde068f5a9d37933a12295519f035e5ee63b1f'
SIGNAL_FIELDS = {'scores', 'dates', 'symbols', 'signal_time_ns', 'membership', 'roles'}
EXPORT_FIELDS = ('signal_date', 'symbol', 'score', 'signal_time_ns', 'role')
TARGET_FIELDS = ('signal_date', 'execution_date', 'signal_time_ns', 'weight_date', 'symbol',
                 'reference_weight', 'score', 'score_status', 'multiplier', 'target_weight')
DAY_NS = 86_400_000_000_000
CHINA_OFFSET_NS = 28_800_000_000_000


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def date_text(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'Use canonical YYYY-MM-DD dates')
    require(dt.date.fromisoformat(value).isoformat() == value, 'Invalid exchange date')
    return value


def validate_signal_pack(signal):
    """Validate the six-field format without opening labels or model objects."""
    require(set(signal) == SIGNAL_FIELDS, 'Exactly six signal fields are required')
    require(all(isinstance(v, np.ndarray) and v.dtype.kind != 'O' for v in signal.values()), 'Object/pickle arrays are forbidden')
    dates, symbols, scores = signal['dates'], signal['symbols'], signal['scores']
    times, member, roles = signal['signal_time_ns'], signal['membership'], signal['roles']
    require(dates.ndim == symbols.ndim == 1 and len(dates) > 0 and len(symbols) > 0 and
            dates.dtype.kind == symbols.dtype.kind == 'U', 'Unique Unicode date/symbol axes are required')
    require(np.all(dates[1:] > dates[:-1]) and np.all(symbols[1:] > symbols[:-1]) and
            all(str(s) and str(s) == str(s).strip() for s in symbols), 'Date/symbol axes must be sorted, unique and nonempty')
    for d in dates:
        date_text(str(d))
    require(scores.dtype == np.float64 and scores.shape == member.shape == (len(dates), len(symbols)) and
            member.dtype == np.bool_, 'Scores must be float64 and membership must be aligned bool')
    require(times.dtype == np.int64 and times.shape == roles.shape == (len(dates),) and roles.dtype.kind == 'U', 'One int64 UTC time and role are required per date')
    local = times + CHINA_OFFSET_NS
    require(np.all(np.diff(times) > 0) and np.array_equal(local // DAY_NS, dates.astype('datetime64[D]').astype(np.int64)) and
            np.all(local % DAY_NS == 15 * 3_600_000_000_000), 'Signals must be exactly 15:00 Shanghai on their stated dates')
    role_by_year = {'2024': 'warmup2024', '2025': 'primary2025', '2026': 'carry2026'}
    require(all(str(d)[:4] in role_by_year for d in dates), 'The frozen signal roles cover 2024, 2025 and 2026')
    expected = np.asarray([role_by_year[str(d)[:4]] for d in dates])
    require(np.array_equal(roles, expected), 'Signal roles do not match their dates')
    require(not np.isinf(scores).any() and not np.isfinite(scores[~member]).any(), 'Scores may be finite only for signal-date members')
    return signal


def load_signals(path, expected_sha256=OLD_RANK_SHA256):
    require(isinstance(expected_sha256, str) and re.fullmatch('[0-9a-f]{64}', expected_sha256), 'An explicit SHA256 is required')
    actual = sha256(path)
    require(actual == expected_sha256, 'Signal file SHA256 differs; use the packaged OLD_RANK file or an explicit reviewed digest')
    with np.load(path, allow_pickle=False) as z:
        require(set(z.files) == SIGNAL_FIELDS, 'Exactly six signal fields are required')
        signal = {k: z[k] for k in z.files}
    validate_signal_pack(signal)
    for a in signal.values():
        a.flags.writeable = False
    return signal, actual


def export_rows(signal):
    validate_signal_pack(signal)
    for i, date in enumerate(signal['dates']):
        known = signal['membership'][i] & np.isfinite(signal['scores'][i])
        for j in np.flatnonzero(known):
            yield {'signal_date': str(date), 'symbol': str(signal['symbols'][j]),
                   'score': float(signal['scores'][i, j]), 'signal_time_ns': int(signal['signal_time_ns'][i]),
                   'role': str(signal['roles'][i])}


def read_csv_rows(path, fields):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        require(reader.fieldnames is not None and len(reader.fieldnames) == len(fields) and
                set(reader.fieldnames) == set(fields), 'CSV columns must be exactly: ' + ','.join(fields))
        rows = list(reader)
    require(rows and all(set(row) == set(fields) and all(v is not None for v in row.values()) for row in rows), 'CSV must contain complete data rows')
    return rows


def read_pcf(path):
    rows = read_csv_rows(path, ('date', 'symbol', 'weight'))
    seen = set()
    for row in rows:
        date_text(row['date'])
        require(row['symbol'] and row['symbol'] == row['symbol'].strip(), 'Use exact model-style symbol strings; no whitespace')
        key = (row['date'], row['symbol'])
        require(key not in seen, 'Duplicate PCF date/symbol'); seen.add(key)
        row['weight'] = float(row['weight'])
        require(math.isfinite(row['weight']) and row['weight'] >= 0, 'PCF weights must be finite and nonnegative')
    return rows


def read_mapping(path):
    rows = read_csv_rows(path, ('signal_date', 'execution_date'))
    signals, executions = set(), set()
    for row in rows:
        s, e = date_text(row['signal_date']), date_text(row['execution_date'])
        require(e > s, 'Execution must follow the 15:00 signal date; same-day execution is forbidden')
        require(s not in signals and e not in executions, 'Signal/execution mapping must be one-to-one')
        signals.add(s); executions.add(e)
    return rows


def tilt_weights(reference, scores, k=.5):
    """Exact PCF budget normalization; missing alpha is neutral, never vetoed."""
    w, z = np.asarray(reference, dtype=np.float64), np.asarray(scores, dtype=np.float64)
    require(w.ndim == 1 and w.shape == z.shape and len(w) > 0, 'One score or NaN is required for every PCF stock')
    require(math.isfinite(k) and k >= 0, 'K must be finite and nonnegative')
    require(np.isfinite(w).all() and np.all(w >= 0) and float(w.sum()) <= 1. + 1e-10 and not np.isinf(z).any(), 'Invalid long-only PCF weights/scores')
    mult = np.ones(len(w), dtype=np.float64)
    known = np.isfinite(z)
    mult[known] += k * np.clip(z[known], -2., 2.)
    require(np.all(mult >= 0), 'K creates negative stock multipliers; reduce K for this long-only tilt')
    budget = float(w.sum()); tilted = w * mult; total = float(tilted.sum())
    require(total > 0 or budget == 0, 'All stock multipliers are zero; reduce K to preserve the original stock budget')
    target = tilted / total * budget if total > 0 else tilted
    require(np.isfinite(target).all() and np.all(target >= 0) and abs(float(target.sum()) - budget) <= 1e-12, 'Target stock budget changed')
    return target, mult


def build_targets(signal, pcf_rows, mappings, weight_date, k=.5):
    validate_signal_pack(signal)
    require(weight_date == 'signal_date', 'PCF dates must mean the signal date; execution-day reference weights are not accepted')
    date_index = {str(d): i for i, d in enumerate(signal['dates'])}
    symbol_index = {str(s): j for j, s in enumerate(signal['symbols'])}
    mapping = {r[weight_date]: r for r in mappings}
    require(len(mapping) == len(mappings), 'Ambiguous date mapping')
    groups = {}
    for row in pcf_rows:
        groups.setdefault(row['date'], []).append(row)
    targets, calendars = [], []
    for date, rows in sorted(groups.items()):
        require(date in mapping, 'PCF date is missing from explicit mapping: ' + date)
        mapped = mapping[date]; s, e = mapped['signal_date'], mapped['execution_date']
        require(s in date_index and e > s, 'No available prior 15:00 signal for PCF date: ' + date)
        i = date_index[s]
        verified = i + 1 < len(signal['dates'])
        if verified:
            require(str(signal['dates'][i + 1]) == e, 'Execution is not the next session in the frozen signal calendar')
        else:
            require(e > str(signal['dates'][-1]), 'Invalid boundary execution date')
        # Only this mapped signal row supplies alpha; execution-day scores,
        # membership, prices and returns are never consulted.
        scores, statuses = [], []
        for row in rows:
            j = symbol_index.get(row['symbol'])
            if j is None:
                scores.append(np.nan); statuses.append('outside_model_universe_neutral')
            elif not bool(signal['membership'][i, j]):
                scores.append(np.nan); statuses.append('not_signal_member_neutral')
            elif not np.isfinite(signal['scores'][i, j]):
                scores.append(np.nan); statuses.append('missing_score_neutral')
            else:
                scores.append(float(signal['scores'][i, j])); statuses.append('scored')
        weights = np.asarray([r['weight'] for r in rows], dtype=np.float64)
        target, mult = tilt_weights(weights, np.asarray(scores), k)
        for row, z, status, m, t in zip(rows, scores, statuses, mult, target):
            targets.append({'signal_date': s, 'execution_date': e, 'signal_time_ns': int(signal['signal_time_ns'][i]),
                'weight_date': date, 'symbol': row['symbol'], 'reference_weight': float(row['weight']),
                'score': float(z) if np.isfinite(z) else '', 'score_status': status, 'multiplier': float(m), 'target_weight': float(t)})
        calendars.append({'signal_date': s, 'execution_date': e, 'next_session_checked_against_signal_calendar': verified,
                          'pcf_stock_rows_retained': len(rows), 'reference_stock_budget': float(weights.sum()),
                          'target_stock_budget': float(target.sum())})
    return targets, calendars


def write_output(path, fields, rows, metadata):
    path = Path(path); sidecar = path.with_suffix(path.suffix + '.meta.json')
    require(path.parent.is_dir(), 'Output parent directory must already exist')
    require(not path.exists() and not sidecar.exists(), 'Existing output or metadata will not be overwritten')
    count = 0
    with path.open('x', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for row in rows:
            writer.writerow(row); count += 1
    value = {**metadata, 'output_rows': count, 'output_sha256': sha256(path), 'output_file': path.name}
    with sidecar.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False); f.write('\n')
    return {'output': str(path), 'metadata': str(sidecar), 'rows': count, 'sha256': value['output_sha256']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('export', 'tilt'):
        parser = sub.add_parser(name)
        parser.add_argument('--signals', type=Path, required=True)
        parser.add_argument('--signals-sha256', default=OLD_RANK_SHA256)
        parser.add_argument('--out', type=Path, required=True)
        if name == 'tilt':
            parser.add_argument('--pcf', type=Path, required=True)
            parser.add_argument('--mapping', type=Path, required=True, help='Explicit signal_date,execution_date CSV')
            parser.add_argument('--weight-date', choices=('signal_date',), required=True,
                                help='PCF date is the signal date; execution date comes only from --mapping')
            parser.add_argument('--k', type=float, default=.5)
    a = p.parse_args(); signal, digest = load_signals(a.signals, a.signals_sha256)
    frozen = digest == OLD_RANK_SHA256
    metadata = {'schema': 'old_rank_backtest_inputs_v1', 'operation': a.command, 'signal_sha256': digest,
        'is_frozen_OLD_RANK_e402': frozen, 'source_model_if_frozen': {
            'input_fields': 122, 'output_heads': 4, 'selected_head': 'y4', 'seeds': [66, 77, 88, 99, 111],
            'artificial_factor_uniform_rank_count': 64, 'earliest_rank_label3_model': False, 'minute134_model': False} if frozen else None,
        'scores_already_processed': True, 'additional_score_transform_applied': False,
        'signal_clock': '15:00 Asia/Shanghai; timestamps stored as UTC int64 nanoseconds',
        'account_or_return_calculation': False, 'orders_or_trades_generated': False, 'network_access': False,
        'PCF_publication_asof_certified': False, 'helper_program_sha256': sha256(__file__)}
    if a.command == 'export':
        result = write_output(a.out, EXPORT_FIELDS, export_rows(signal), metadata)
    else:
        rows, calendars = build_targets(signal, read_pcf(a.pcf), read_mapping(a.mapping), a.weight_date, a.k)
        metadata.update(k=a.k, clip=[-2, 2], weight_date_interpretation=a.weight_date,
            pcf_sha256=sha256(a.pcf), mapping_sha256=sha256(a.mapping), calendar_checks=calendars,
            missing_alpha_rule='neutral multiplier1; retain every PCF row; no veto',
            budget_rule='preserve each original PCF stock-weight sum; never scale cash to stocks',
            zero_multiplier_sum_rule='reject; reduce K to preserve stock budget')
        result = write_output(a.out, TARGET_FIELDS, rows, metadata)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
