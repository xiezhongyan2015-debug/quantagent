"""Re-extract and check all 499 original day controls directly from whole source."""
import gzip
import hashlib
import io
import json
import sys
import tarfile
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive/all_original_day_controls'
PRICE = ['open', 'high', 'low', 'close']
GRID = list(range(570, 690)) + list(range(780, 900))


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


class SourceParts(io.RawIOBase):
    def __init__(self, paths):
        self.paths = paths
        self.handles = [p.open('rb') for p in paths]
        self.hashes = [hashlib.sha256() for p in paths]
        self.index = 0
        self.bytes_read = 0
    def readable(self):
        return True
    def readinto(self, buffer):
        result = bytearray()
        remaining = len(buffer)
        while remaining and self.index < len(self.handles):
            chunk = self.handles[self.index].read(remaining)
            if not chunk:
                self.index += 1
                continue
            self.hashes[self.index].update(chunk)
            result.extend(chunk)
            self.bytes_read += len(chunk)
            remaining -= len(chunk)
        buffer[:len(result)] = result
        return len(result)
    def close(self):
        for f in self.handles:
            f.close()
        super().close()


def main():
    output = OUT / 'independent_all_original_control_verification.json'
    assert not output.exists()
    audit_path = OUT / 'all_original_control_reference_audit.json'
    audit = json.loads(audit_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_all_original_day_controls.py') == audit['source_code_sha256']
    receipt_path = OUT / 'extraction_receipt.json'
    assert sha(receipt_path) == audit['original_control_extraction_receipt_sha256']
    receipt = json.loads(receipt_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_all_original_day_controls.py') == receipt['source_code_sha256']
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(meta_path) == audit['current_candidate_meta_sha256']
    complete_path = meta_path.parent / 'hour_complete.npy'
    assert sha(complete_path) == audit['current_hour_complete_sha256']
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(calendar_path) == audit['calendar_sha256']
    calendar = json.loads(calendar_path.read_text())
    d = calendar['dates'].index('2026-09-23')
    complete = np.load(complete_path, mmap_mode='r')
    symbols = {calendar['union_symbols'][i] for i in np.flatnonzero(complete[d * 4:d * 4 + 4].all(0))}
    by_member = {r['original_tar_member']: r for r in receipt['payloads']}
    by_symbol = {r['symbol']: r for r in audit['records']}
    assert len(symbols) == len(by_member) == len(by_symbol) == 499
    assert set(by_symbol) == symbols
    assert sha(audit['minute_reference_path']) == audit['minute_reference_sha256']
    references = {}
    for row in pq.read_table(audit['minute_reference_path']).to_pylist():
        references.setdefault(row['symbol'], []).append(row)
    paths = [ROOT / r['name'] for r in receipt['source_parts']]
    reader = SourceParts(paths)
    source_rows = members = 0
    found = set()
    started = last = time.monotonic()
    with io.BufferedReader(reader, buffer_size=1024 * 1024) as raw, gzip.GzipFile(fileobj=raw) as zipped:
        with tarfile.open(fileobj=zipped, mode='r|') as archive:
            for member in archive:
                members += 1
                if member.name in by_member:
                    assert member.isfile() and member.name not in found
                    found.add(member.name)
                    record = by_member[member.name]
                    payload = archive.extractfile(member).read()
                    assert len(payload) == record['bytes'] == member.size
                    assert hashlib.sha256(payload).hexdigest() == record['sha256']
                    assert payload == Path(record['path']).read_bytes()
                    rows = pq.read_table(io.BytesIO(payload)).to_pylist()
                    symbol = record['code'] + ('.SH' if record['code'].startswith('6') else '.SZ')
                    assert len(rows) == len(references[symbol]) == 240
                    index = []
                    for r, expected in zip(rows, references[symbol]):
                        nanos = r['datetime_nano']
                        assert nanos % 1000000000 == 0
                        local = datetime.fromtimestamp(nanos // 1000000000, tz=ZoneInfo('Asia/Shanghai'))
                        assert local.strftime('%Y-%m-%d %H:%M:%S') == r['datetime'][:10] + ' ' + r['datetime'][11:19]
                        assert str(local.date()) == '2026-09-23' and local.second == 0
                        left = local.hour * 60 + local.minute
                        index.append(left)
                        assert (expected['left_minute'], expected['source_datetime_nano'], expected['source_datetime']) == (left, nanos, r['datetime'])
                        assert expected['original_source_payload_sha256'] == record['sha256']
                        assert all(D(str(r[f])) == D(expected[f]) for f in PRICE + ['volume'])
                        values = [D(str(r[f])) for f in PRICE]
                        volume = D(str(r['volume']))
                        valid = all(x.is_finite() and x > 0 for x in values) and volume.is_finite() and volume >= 0 and volume == volume.to_integral_value()
                        valid = valid and values[1] == max(values) and values[2] == min(values)
                        assert valid and expected['original_price_quantity_valid']
                    assert index == GRID
                    p = by_symbol[symbol]
                    assert sha(p['primary_daily_path']) == p['primary_daily_sha256']
                    dayobj = json.loads(Path(p['primary_daily_path']).read_text())
                    day = next(dict(zip(dayobj['fields'], r)) for r in dayobj['rows'] if r[0] == '2026-09-23')
                    actual = [D(str(rows[0]['open'])), max(D(str(r['high'])) for r in rows), min(D(str(r['low'])) for r in rows),
                              D(str(rows[-1]['close'])), sum(D(str(r['volume'])) for r in rows)]
                    exact = {f: v == D(day[f]) for f, v in zip(PRICE + ['volume'], actual)}
                    assert exact == p['original_aggregate_vs_primary_daily_exact'] and all(exact.values())
                    source_rows += len(rows)
                if time.monotonic() - last >= 20:
                    print(json.dumps({'phase': 'independent_original_control_source_rescan', 'members_seen': members,
                                      'verified_original_controls': len(found), 'source_bytes_read': reader.bytes_read}), flush=True)
                    last = time.monotonic()
                if members % 1000 == 0:
                    archive.members.clear()
        while zipped.read(1024 * 1024):
            pass
    assert found == set(by_member) and source_rows == 119760
    fingerprint = [{'name': p.name, 'size_bytes': p.stat().st_size, 'sha256': digest.hexdigest()}
                   for p, digest in zip(paths, reader.hashes)]
    assert fingerprint == receipt['source_parts']
    assert reader.bytes_read == sum(r['size_bytes'] for r in fingerprint)
    assert audit['stockdays_all_original_price_quantity_valid'] == audit['stockdays_all_OHLC_V_daily_fields_exact'] == 499
    assert audit['stockdays_daily_exact_by_field'] == {f: 499 for f in PRICE + ['volume']}
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True, 'source_code_sha256': sha(__file__),
              'audited_reference_receipt_sha256': sha(audit_path), 'original_control_extraction_receipt_sha256': sha(receipt_path),
              'current_candidate_meta_sha256': sha(meta_path), 'whole_original_source_parts_fresh_SHA256_verified': True,
              'whole_original_gzip_CRC_and_EOF_verified': True, 'whole_original_archive_members_scanned': members,
              'complete_controls_reextracted_directly_from_whole_original_archive': len(found),
              'original_native_minutes_checked': source_rows, 'every_original_payload_and_normalized_reference_value_exact': True,
              'all_true_240_left_label_grids_and_Asia_Shanghai_nanos_reproduced': True,
              'all_499_controls_raw_day_OHLC_V_exact_primary_daily': True,
              'original_minute_amount_not_available_and_not_reconstructed': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
