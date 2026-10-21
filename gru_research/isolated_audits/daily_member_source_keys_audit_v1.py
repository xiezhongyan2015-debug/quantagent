"""Once-only current daily-source key audit; no feature or model execution."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_DECODED_MEMBERSHIP_BYTES = 128 * 1024
MAX_SOURCE_BYTES = 160 * 1024 * 1024
MAX_ROWS = 2500000


def reject_duplicate(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=reject_duplicate,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def stat_identity(path):
    s = path.stat()
    return {'bytes': s.st_size, 'mtime_ns': s.st_mtime_ns,
            'device': s.st_dev, 'inode': s.st_ino}


def read_exact(item, source_root):
    path = Path(item['path'])
    if not path.is_absolute() or not path.is_relative_to(source_root):
        raise ValueError('source path outside declared root')
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('symlink source forbidden')
    if not path.is_file() or stat_identity(path) != item['stat']:
        raise ValueError('source STAT changed before read')
    if not 0 < item['stat']['bytes'] <= MAX_FILE_BYTES:
        raise ValueError('source exceeds per-file bound')
    with path.open('rb') as f:
        raw = f.read(MAX_FILE_BYTES + 1)
    if len(raw) != item['stat']['bytes'] or stat_identity(path) != item['stat']:
        raise ValueError('source changed during read')
    return raw, {'path': str(path), 'bytes': len(raw), 'sha256': digest(raw)}


def calendar_date(value):
    if isinstance(value, str):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('daily source date not an exact ISO day')
        return date.fromisoformat(value).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.time().isoformat() != '00:00:00':
            raise ValueError('daily datetime is not an unambiguous day')
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise ValueError('unexpected daily date type')


def write_new(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def audit(manifest_path, output):
    manifest_raw = manifest_path.read_bytes()
    manifest = parse_json(manifest_raw)
    if manifest['schema_version'] != 1 or manifest['mode'] != 'CURRENT_DAILY_SOURCE_KEYS_NOT_HISTORICAL_ARRIVAL_PROOF':
        raise ValueError('unsupported audit contract')
    if str(output) != manifest['fixed_output']:
        raise ValueError('only the sealed once output path is allowed')
    source_root = Path(manifest['source_root'])
    if not source_root.is_absolute() or source_root.is_symlink():
        raise ValueError('invalid declared source root')
    groups = manifest['source_files']
    sources = groups['memberships'] + groups['daily_bars']
    if len(groups['memberships']) > 4100 or len(groups['daily_bars']) > 300:
        raise ValueError('source roster bound exceeded')
    if len({x['path'] for x in sources}) != len(sources):
        raise ValueError('duplicate source identity')
    expected_bytes = sum(x['stat']['bytes'] for x in sources)
    if expected_bytes != manifest['total_source_bytes'] or expected_bytes > MAX_SOURCE_BYTES:
        raise ValueError('source total byte bound mismatch')
    start, end = manifest['date_start'], manifest['date_end']
    if start != '2010-01-01' or end != '2026-09-23':
        raise ValueError('unexpected historical formula warmup range')
    for x in groups['memberships']:
        p = Path(x['path'])
        if p.parent != source_root / 'memberships' or not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json\.gz', p.name):
            raise ValueError('membership path not declared daily archive')
        if not start <= p.name[:10] <= end:
            raise ValueError('membership outside declared date range')
    for x in groups['daily_bars']:
        p = Path(x['path']); rel = p.relative_to(source_root / 'bars')
        if len(rel.parts) != 3 or not re.fullmatch(r'\d{4}', rel.parts[0]) or not re.fullmatch(r'\d{4}-\d{2}', rel.parts[1]) or p.suffix != '.parquet':
            raise ValueError('daily bar path not declared yearly/monthly archive')
        if rel.parts[1][:4] != rel.parts[0] or not start[:4] <= rel.parts[0] <= end[:4]:
            raise ValueError('daily bar year mismatch')
    for item in manifest['small_bindings']:
        raw = Path(item['path']).read_bytes()
        if len(raw) != item['bytes'] or digest(raw) != item['sha256']:
            raise ValueError('small source binding changed')
    # Import a known bundled/project-local Arrow runtime; no source operator imports.
    sys.path.insert(0, str(Path(manifest['research_root']) / '.deps'))
    import pyarrow as pa
    import pyarrow.parquet as pq
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / 'READONLY_STARTED_v1.json', {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'manifest_path': str(manifest_path), 'manifest_bytes': len(manifest_raw),
        'manifest_sha256': digest(manifest_raw), 'source_count': len(sources),
        'declared_source_bytes': expected_bytes})
    identities, membership, total_read, total_member_keys = [], {}, 0, 0
    try:
        for item in groups['memberships']:
            raw, ident = read_exact(item, source_root); identities.append(ident); total_read += len(raw)
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
                decoded = f.read(MAX_DECODED_MEMBERSHIP_BYTES + 1)
            if len(decoded) > MAX_DECODED_MEMBERSHIP_BYTES:
                raise ValueError('membership decoded bound exceeded')
            doc = parse_json(decoded)
            if type(doc.get('errorcode')) is not int or doc['errorcode'] != 0:
                raise ValueError('membership response was unsuccessful')
            symbols = doc.get('thscode') or doc['data'][0]['table']['THSCODE']
            if not isinstance(symbols, list) or not symbols or any(not isinstance(s, str) or not re.fullmatch(r'\d{6}\.(?:SH|SZ|BJ)', s) for s in symbols):
                raise ValueError('invalid membership symbols')
            if len(symbols) != len(set(symbols)):
                raise ValueError('duplicate membership symbols')
            day = Path(item['path']).name[:10]
            if day in membership:
                raise ValueError('duplicate membership date')
            membership[day] = set(symbols); total_member_keys += len(symbols)
            if total_member_keys > MAX_ROWS:
                raise ValueError('membership key bound exceeded')
        observations, total_rows, scoped_rows, nonmember_count, missing_date_count = [], 0, 0, 0, 0
        negative_examples = []
        for item in groups['daily_bars']:
            raw, ident = read_exact(item, source_root); identities.append(ident); total_read += len(raw)
            # The only decompressed Parquet columns are date and thscode.
            table = pq.read_table(pa.BufferReader(raw), columns=['date', 'thscode'], use_threads=False)
            if table.column_names != ['date', 'thscode']:
                raise ValueError('daily key column ordering changed')
            total_rows += table.num_rows
            if total_rows > MAX_ROWS:
                raise ValueError('daily key row bound exceeded')
            within, bad, missing, duplicates, seen = 0, 0, 0, 0, set()
            days = table['date'].to_pylist(); symbols = table['thscode'].to_pylist()
            for day_value, symbol in zip(days, symbols):
                day = calendar_date(day_value)
                if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(?:SH|SZ|BJ)', symbol):
                    raise ValueError('daily security code is invalid')
                if not start <= day <= end:
                    continue
                within += 1; key = (day, symbol)
                if key in seen:
                    duplicates += 1
                seen.add(key)
                if day not in membership:
                    missing += 1
                    if len(negative_examples) < 20: negative_examples.append({'kind': 'missing_membership_date', 'day': day, 'symbol': symbol})
                elif symbol not in membership[day]:
                    bad += 1
                    if len(negative_examples) < 20: negative_examples.append({'kind': 'nonmember_source_row', 'day': day, 'symbol': symbol})
            scoped_rows += within; nonmember_count += bad; missing_date_count += missing
            observations.append({'source': ident, 'actual_schema': str(table.schema), 'actual_rows': table.num_rows,
                                 'date_range_rows_checked': within, 'nonmember_rows': bad,
                                 'missing_membership_date_rows': missing, 'within_file_duplicate_keys': duplicates})
        if total_read != expected_bytes:
            raise ValueError('actual read total does not equal sealed roster')
        # Fresh STAT only; no second whole-body source hash/read.
        if any(stat_identity(Path(x['path'])) != x['stat'] for x in sources):
            raise ValueError('source STAT changed before publication')
        for item in manifest['small_bindings']:
            raw = Path(item['path']).read_bytes()
            if len(raw) != item['bytes'] or digest(raw) != item['sha256']:
                raise ValueError('small binding changed before publication')
        receipt = {
            'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'CURRENT_SOURCE_KEYS_ALL_IN_DATED_MEMBERSHIP' if not nonmember_count and not missing_date_count else 'CURRENT_SOURCE_KEY_MEMBERSHIP_CONDITION_FAILED',
            'manifest_sha256': digest(manifest_raw), 'actual_source_bytes_read': total_read,
            'actual_membership_files': len(membership), 'actual_membership_keys': total_member_keys,
            'actual_daily_files': len(observations), 'actual_daily_rows': total_rows,
            'actual_date_range_daily_rows_checked': scoped_rows,
            'actual_nonmember_rows': nonmember_count, 'actual_missing_membership_date_rows': missing_date_count,
            'first_negative_examples_bounded20': negative_examples,
            'actual_source_byte_identities': identities, 'daily_key_observations': observations,
            'all_after_STAT_equal_before_not_second_body_hash': True,
            'source_price_volume_amount_and_other_numeric_columns_decoded': False,
            'duplicate_keys_do_not_certify_equal_numeric_values': True,
            'membership_effective_provider_event_publication_or_arrival_verified': False,
            'current_source_hashes_equal_original_training_source_hashes': 'UNKNOWN_NO_FULL_ORIGINAL_SOURCE_HASH_PIN',
            'all122_feature_formulas_or_model_scores_recomputed': False,
            'source_catalog_factor_selection_pipeline_or_old_auditor_executed': False,
            'original_source_data_models_R2_results_modified': False,
            'economic_target_accepted': False, 'full_PLAN_complete': False, 'ic_target_achieved': False}
        write_new(output / 'actual_current_daily_source_key_receipt_v1.json', receipt)
        print(json.dumps({'status': receipt['status'], 'daily_rows': total_rows,
                          'nonmember_rows': nonmember_count, 'missing_membership_rows': missing_date_count,
                          'source_bytes_read': total_read}))
        return 0 if receipt['status'] == 'CURRENT_SOURCE_KEYS_ALL_IN_DATED_MEMBERSHIP' else 2
    except Exception as exc:
        write_new(output / 'FAILED_v1.json', {'created_utc': datetime.now(timezone.utc).isoformat(),
                  'error': type(exc).__name__ + ': ' + str(exc), 'source_bytes_read': total_read,
                  'source_identities_read_before_failure': identities, 'retry_allowed': False})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    return audit(args.manifest.resolve(), args.output.resolve())


if __name__ == '__main__':
    raise SystemExit(main())
