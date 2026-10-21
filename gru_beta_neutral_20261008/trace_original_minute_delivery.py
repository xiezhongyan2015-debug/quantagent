"""Read-only fresh archive trace of the frozen 2019 minute defects.

Compare compressed-part fingerprints, original payload bytes, archive inventory,
independent minute validity checks, and the persisted original aggregates. Never
repair, interpolate, or overwrite the original delivery or the frozen dataset.
"""
import collections
import gzip
import hashlib
import io
import json
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd
sys.path.insert(0, str(ROOT / 'gru_research'))
from minute_data import SplitFile, aggregate_stock_day, MINUTE_OFFSETS

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
ART = ROOT / 'gru_research/artifacts'
OUT = BASE / 'original_minute_delivery_trace_20261009'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save_json(name, data):
    path = OUT / name
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    tmp.replace(path)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    receipt = json.loads((BASE / 'minute_repair_extraction_receipt.json').read_text())
    copies = {(r['date'], r['symbol']): r for r in receipt['payloads']}
    assert len(copies) == len(receipt['payloads']) == 2581
    frozen = json.loads((ART / 'minute_source_fingerprints.json').read_text())
    parts = sorted(ROOT.glob('A股分钟k线数据.tar.gz.*'))
    before = [(p.stat().st_size, p.stat().st_mtime_ns) for p in parts]
    assert len(parts) == len(frozen['parts']) == 5
    source = SplitFile(parts, fingerprint=True)
    found, inventory, duplicate_members = {}, set(), []
    total_members = 0
    last_update = started
    with io.BufferedReader(source, buffer_size=1024 * 1024) as raw:
        with gzip.GzipFile(fileobj=raw) as zipped:
            with tarfile.open(fileobj=zipped, mode='r|') as archive:
                for member in archive:
                    total_members += 1
                    pieces = member.name.split('/')
                    if member.isfile() and len(pieces) == 3 and pieces[1].startswith('2019') and pieces[-1].endswith('.parquet'):
                        day = f'{pieces[1][:4]}-{pieces[1][4:6]}-{pieces[1][6:]}'
                        code = pieces[-1][:-8]
                        symbol = code + ('.SH' if code.startswith(('6', '9')) else '.SZ')
                        key = (day, symbol)
                        if key in inventory:
                            duplicate_members.append({'date': day, 'symbol': symbol, 'member': member.name})
                        inventory.add(key)
                        if key in copies:
                            assert member.size <= 1024 * 1024
                            payload = archive.extractfile(member).read()
                            previous = copies[key]
                            assert len(payload) == previous['bytes']
                            assert hashlib.sha256(payload).hexdigest() == previous['sha256']
                            assert payload == Path(previous['path']).read_bytes()
                            found[key] = {'date': day, 'symbol': symbol, 'member': member.name,
                                          'bytes': len(payload), 'sha256': previous['sha256']}
                    if time.monotonic() - last_update > 25:
                        progress = {'status': 'reading_original_delivery', 'members_seen': total_members,
                                    'payloads_matched': len(found), 'compressed_bytes_read': source.bytes_read,
                                    'elapsed_seconds': round(time.monotonic() - started, 2)}
                        save_json('progress.json', progress)
                        print(json.dumps(progress), flush=True)
                        last_update = time.monotonic()
                    # TarInfo history is unnecessary for a forward-only stream.
                    if total_members % 1000 == 0:
                        archive.members.clear()
            # Read through gzip's footer and CRC, including bytes after tar EOF.
            while zipped.read(1024 * 1024):
                pass
    assert source.bytes_read == sum(size for size, _ in before)
    fresh = []
    for p, h, old in zip(parts, source.digests, frozen['parts']):
        row = {'name': p.name, 'size_bytes': p.stat().st_size, 'sha256': h.hexdigest()}
        assert row == old
        fresh.append(row)
    assert before == [(p.stat().st_size, p.stat().st_mtime_ns) for p in parts]
    assert set(found) == set(copies)
    assert not duplicate_members
    save_json('archive_payload_trace.json', {'source_parts': fresh, 'payloads': list(found.values()),
                                           'all_2581_payloads_equal_previous_copies': True})

    members = pq.read_table(ART / 'minute_memberships.parquet').to_pandas()
    expected = set(zip(members.loc[members.date.str.startswith('2019'), 'date'],
                       members.loc[members.date.str.startswith('2019'), 'symbol']))
    stored_audits = {}
    source_row_counts = collections.Counter()
    for path in sorted((ART / 'minute_day_audit').glob('2019*.parquet')):
        for r in pq.read_table(path).to_pylist():
            stored_audits[(r['date'], r['symbol'])] = r
            source_row_counts[r['source_rows']] += 1
    assert set(stored_audits) == inventory
    pd.DataFrame(sorted(inventory), columns=['date', 'symbol']).to_csv(OUT / 'archive_2019_payload_inventory.csv', index=False)
    missing = sorted(expected - inventory)
    pd.DataFrame(missing, columns=['date', 'symbol']).to_csv(OUT / 'missing_historical_member_payloads.csv', index=False)

    originals = {}
    for day in sorted({d for d, _ in copies}):
        for row in pq.read_table(ART / 'minute_hourly_raw' / f'{day}.parquet').to_pylist():
            originals[(row['date'], row['symbol'], int(row['slot']))] = row
    stats, hours, mismatch = [], [], []
    all_bad_minutes = []
    aggregate_hour_checks = 0
    zero_volume_execution_diagnostics = set()
    for (day, symbol), previous in sorted(copies.items()):
        payload = Path(previous['path']).read_bytes()
        table = pq.read_table(io.BytesIO(payload), use_threads=False)
        df = table.to_pandas()
        minute = (pd.to_datetime(df['datetime']).dt.hour * 60 + pd.to_datetime(df['datetime']).dt.minute).to_numpy()
        order = np.argsort(minute, kind='stable')
        assert np.array_equal(minute[order], MINUTE_OFFSETS)
        raw_price = df[['open', 'high', 'low', 'close']].to_numpy(float)
        adj = df[['qfq_open', 'qfq_high', 'qfq_low', 'qfq_close']].to_numpy(float)
        volume = df.volume.to_numpy(float)
        factor = df.qfq_ratio.to_numpy(float)
        valid = (np.isfinite(raw_price).all(1) & (raw_price > 0).all(1)
                 & np.isfinite(adj).all(1) & (adj > 0).all(1)
                 & np.isfinite(volume) & (volume >= 0) & np.isfinite(factor) & (factor > 0)
                 & (raw_price[:, 1] >= raw_price[:, [0, 2, 3]].max(1) - 1e-8)
                 & (raw_price[:, 2] <= raw_price[:, [0, 1, 3]].min(1) + 1e-8)
                 & np.isclose(adj, raw_price * factor[:, None], rtol=1e-7, atol=1e-6).all(1))
        stats.append({'date': day, 'symbol': symbol, 'source_rows': len(df),
                      'independent_invalid_rows': int((~valid).sum()), 'daily_volume': float(volume.sum()),
                      'all_240_start_labeled_minutes_present': True,
                      'raw_ohlc_positive': bool((raw_price > 0).all()), 'payload_sha256': previous['sha256']})
        for j in np.flatnonzero(~valid):
            all_bad_minutes.append({'date': day, 'symbol': symbol, 'minute': int(minute[j]),
                                    'open': raw_price[j, 0], 'high': raw_price[j, 1],
                                    'low': raw_price[j, 2], 'close': raw_price[j, 3], 'volume': volume[j]})
        recomputed, audit = aggregate_stock_day((day, symbol, payload))
        assert audit == stored_audits[(day, symbol)]
        assert len(recomputed) == 4
        for r in recomputed:
            key = (day, symbol, int(r['slot']))
            old = originals[key]
            legacy_zero_diagnostic = (old['window_complete'] and old['window_volume'] == 0
                                      and not old['window_tradable'] and not r['window_complete'])
            if legacy_zero_diagnostic:
                starts = [571, 631, 781, 841]
                epos = (minute >= starts[r['slot']]) & (minute < starts[r['slot']] + 5)
                assert epos.sum() == 5 and valid[epos].all() and volume[epos].sum() == 0
                assert old['window_price'] == float(adj[epos, 0].mean())
                assert old['window_raw_price'] == float(raw_price[epos, 0].mean())
                assert old['window_all_flat'] == bool((raw_price[epos, 1] == raw_price[epos, 2]).all())
                zero_volume_execution_diagnostics.add(key)
            for k, v in r.items():
                if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_)):
                    stored_number = np.nan if old[k] is None else old[k]
                    same = bool(np.isclose(v, stored_number, rtol=0, atol=1e-12, equal_nan=True))
                else:
                    same = v == old[k]
                if not same:
                    mismatch.append({'date': day, 'symbol': symbol, 'slot': r['slot'], 'field': k,
                                     'fresh': str(v), 'stored': str(old[k]),
                                     'explained_zero_volume_execution_diagnostic': bool(legacy_zero_diagnostic and
                                         k in ['window_complete', 'window_price', 'window_raw_price', 'window_volume', 'window_all_flat'])})
            pos = np.isin(minute, MINUTE_OFFSETS[r['slot'] * 60:(r['slot'] + 1) * 60])
            independently_complete = bool(pos.sum() == 60 and valid[pos].all() and volume[pos].sum() > 0)
            assert independently_complete == r['hour_complete']
            hours.append({'date': day, 'symbol': symbol, 'slot': r['slot'],
                          'invalid_minutes': int((~valid[pos]).sum()), 'observed_volume': float(volume[pos].sum()),
                          'independent_complete': independently_complete, 'stored_complete': old['hour_complete']})
            aggregate_hour_checks += 1
    pd.DataFrame(mismatch).to_csv(OUT / 'aggregate_execution_diagnostic_differences.csv', index=False)
    unexplained = [r for r in mismatch if not r['explained_zero_volume_execution_diagnostic']]
    assert not unexplained, unexplained[:5]
    stats = pd.DataFrame(stats)
    hours = pd.DataFrame(hours)
    bad = pd.DataFrame(all_bad_minutes)
    stats.to_csv(OUT / 'selected_stockday_forensics.csv', index=False)
    hours.to_csv(OUT / 'selected_hour_forensics.csv', index=False)
    bad.to_csv(OUT / 'nonpositive_minutes_in_original_delivery.csv', index=False)
    assert len(bad) == 2077
    assert bad.minute.eq(570).all() and bad.open.eq(0).all() and bad.low.eq(0).all()

    # Compare March 29's observed raw volumes with separately published minute
    # source summaries; retain suspended days rather than declaring all traded.
    comparison = []
    for symbol in sorted(stats.loc[stats.date.eq('2019-03-29'), 'symbol']):
        donor_path = BASE / 'backfilled_panel_candidate/donor_daily_audit' / f'{symbol}.parquet'
        donor = pq.read_table(donor_path, filters=[('date', '=', '2019-03-29')]).to_pylist()
        assert len(donor) == 1
        donor = donor[0]
        for r in hours.loc[hours.date.eq('2019-03-29') & hours.symbol.eq(symbol)].to_dict('records'):
            slot = r['slot']
            comparison.append({**r, 'donor_hour_volume': donor[f'hour{slot}_volume'],
                               'donor_hour_complete': donor[f'hour{slot}_complete'],
                               'donor_corroborated': donor['donor_corroborated'],
                               'independent_daily_tradestatus': donor.get('tradestatus'),
                               'independent_daily_volume': donor.get('volume')})
    comparison = pd.DataFrame(comparison)
    comparison.to_csv(OUT / 'march29_original_vs_independent_minutes.csv', index=False)
    zero = comparison.observed_volume.eq(0)
    contradicted = zero & comparison.donor_hour_volume.gt(0) & comparison.donor_hour_complete & comparison.donor_corroborated
    summary = {
        'status': 'complete', 'checked_at_utc': pd.Timestamp.now(tz='UTC').isoformat(),
        'compressed_archive_bytes_verified': source.bytes_read, 'compressed_part_hashes_equal_frozen': True,
        'archive_members_seen': total_members, 'original_2019_source_stockdays': len(inventory),
        'original_2019_source_row_count_histogram': dict(source_row_counts),
        'original_2019_historical_member_stockdays': len(expected),
        'original_2019_missing_historical_member_payloads': len(missing),
        'original_2019_source_outside_membership_stockdays': len(inventory - expected),
        'original_2019_archive_inventory_equals_saved_audits': True,
        'fresh_archive_payloads_verified': len(found), 'fresh_hour_aggregates_verified': aggregate_hour_checks,
        'fresh_vs_saved_hour_and_tradable_execution_field_mismatches': len(unexplained),
        'historical_zero_volume_execution_diagnostic_windows': len(zero_volume_execution_diagnostics),
        'explained_zero_volume_execution_diagnostic_field_differences': len(mismatch),
        'execution_diagnostic_semantics': 'Historical raw diagnostics retain a five-minute quote mean for timestamp-complete zero-volume windows. Current aggregation excludes them. Historical window_tradable is already false; these quotes are not executable prices. All hourly fields and tradable execution-window fields reproduce exactly.',
        'original_delivery_zero_open_and_low_at_0930_stockdays': len(bad),
        'zero_open_and_low_date_counts': bad.groupby('date').size().to_dict(),
        'march29_original_zero_volume_hours': int(zero.sum()),
        'march29_zero_volume_hours_contradicted_by_corroborated_public_minutes': int(contradicted.sum()),
        'march29_corroborated_contradiction_stockdays': int(comparison.loc[contradicted, 'symbol'].nunique()),
        'source_files_modified': False, 'frozen_dataset_modified': False,
        'minute_prices_or_volume_fabricated': False,
        'conclusion': 'The 2077 invalid first-minute zero prices and the March 29 zero-volume blocks are present in the immutable raw delivery. Fresh aggregation reproduces all hourly fields and tradable execution-window fields. Explained zero-volume quote diagnostics differ under the current stricter execution-window flag; historical tradability is already false. Corroborated alternate-source trades contradict the admitted subset of March 29 zero-volume blocks. Missing historical-member files remain source gaps.',
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'calculation_source_sha256': digest(Path(__file__)),
        'original_aggregator_sha256': digest(ROOT / 'gru_research/minute_data.py'),
        'frozen_part_fingerprint_receipt_sha256': digest(ART / 'minute_source_fingerprints.json'),
    }
    save_json('receipt.json', summary)
    save_json('progress.json', {'status': 'complete', **summary})
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
