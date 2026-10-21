"""Retrieve the published historical-code column using an already official identity."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from acquire_native_matrix_pilot import SparseView
from probe_new_archive_structure import fetch_range
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_native_inventory_20261009'
DEST = OUT / 'native_matrix_pilot'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    output = DEST / 'bounded_verified_alias_column_acquisition.json'; assert not output.exists()
    identity_path = ROOT / 'outputs/gru_beta_neutral_20261008/official_old_code_minute_audit_20261009/receipt.json'
    identity = json.loads(identity_path.read_text())['factor_bridge_contract']['source_records']['302132.SZ']
    assert identity['historical_code'] == 'sz.300114' and identity['official_code_change_date'] == '2025-02-17'
    assert sha(identity['official_statement_path']) == identity['official_statement_sha256']
    info_path = OUT / 'suncong__ashare_1min_info.json'; info = json.loads(info_path.read_text())
    previous_path = DEST / 'bounded_column_acquisition.json'; previous = json.loads(previous_path.read_text())
    assert previous['status'] == 'six_field_native_pilot_columns_acquired'
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'pinned_revision': info['sha'], 'previous_acquisition_receipt_sha256': sha(previous_path),
               'historical_code': '300114', 'canonical_symbol': '302132.SZ', 'official_identity': identity,
               'official_identity_receipt_sha256': sha(identity_path), 'maximum_new_bytes': 30 * 1024 * 1024,
               'new_bytes': 0, 'results': [], 'whole_LFS_SHA256_verified': False,
               'candidate_changed': False, 'candidate_hours_admitted': 0}
    try:
        for record in previous['results']:
            filename, size = record['filename'], record['published_bytes']
            segments = []
            if 'reused_retained_footer' in record:
                footer = record['reused_retained_footer']; assert sha(footer['path']) == footer['retained_range_sha256']
                segments.append((footer['start'], Path(footer['path']).read_bytes()))
            for request in record['requests']:
                assert sha(request['path']) == request['retained_range_sha256']
                segments.append((request['start'], Path(request['path']).read_bytes()))
            parser = pq.ParquetFile(SparseView(size, segments), pre_buffer=False)
            column = parser.metadata.row_group(0).column(parser.schema_arrow.names.index('300114'))
            offset = column.dictionary_page_offset if column.has_dictionary_page else column.data_page_offset
            length = column.total_compressed_size
            assert receipt['new_bytes'] + length <= receipt['maximum_new_bytes']
            request, chunk = fetch_range(info['id'], info['sha'], filename, size, offset, length, filename + '.300114.verified_alias_chunk.bin')
            receipt['new_bytes'] += length; segments.append((offset, chunk))
            parser = pq.ParquetFile(SparseView(size, segments), pre_buffer=False, page_checksum_verification=True)
            table = parser.read(columns=['300114', '__index_level_0__'], use_threads=False)
            path = DEST / (record['field'] + '_historical_300114_native_column.parquet'); assert not path.exists()
            pq.write_table(table, path, compression='zstd')
            receipt['results'].append({'field': record['field'], 'request': request, 'selection_path': str(path),
                                       'selection_sha256': sha(path), 'rows': table.num_rows})
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print(json.dumps({'field': record['field'], 'historical_native_rows': table.num_rows, 'new_bytes': receipt['new_bytes']}), flush=True)
        receipt['status'] = 'six_historical_code_native_columns_acquired'
    except Exception as error:
        receipt.update(status='failed_terminal', error_type=type(error).__name__, error_detail=str(error)[:200])
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({'status': receipt['status'], 'new_bytes': receipt['new_bytes']}), flush=True)


if __name__ == '__main__':
    main()
