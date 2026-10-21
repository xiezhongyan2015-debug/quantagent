"""Read real native columns by verified HTTP offsets, within a fixed byte budget."""
import hashlib
import io
import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from probe_new_archive_structure import fetch_range, FooterView

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_native_inventory_20261009'
DEST = OUT / 'native_matrix_pilot'
CODES = ['600167', '600985', '302132', '600004']
FIELDS = ['close', 'volume', 'open', 'high', 'low', 'amount']
CAP = 120 * 1024 * 1024


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class SparseView(io.RawIOBase):
    def __init__(self, size, segments):
        self.size = size; self.segments = segments; self.position = 0
    def readable(self):
        return True
    def seekable(self):
        return True
    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        return self.position
    def tell(self):
        return self.position
    def read(self, count=-1):
        if count < 0:
            count = self.size - self.position
        for start, raw in self.segments:
            if start <= self.position and self.position + count <= start + len(raw):
                i = self.position - start; self.position += count
                return raw[i:i + count]
        raise ValueError('Parquet reader requested bytes outside the actual retained ranges')


def main():
    DEST.mkdir(exist_ok=True)
    output = DEST / 'bounded_column_acquisition.json'; assert not output.exists()
    info_path = OUT / 'suncong__ashare_1min_info.json'
    info = json.loads(info_path.read_text()); index = {r['rfilename']: r for r in info['siblings']}
    structure_path = OUT / 'bounded_structure_probe_receipt.json'; structure = json.loads(structure_path.read_text())
    old = {r['filename']: r for r in structure['results'] if r['status'] == 'actual_footer_metadata_parsed'}
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'range_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/probe_new_archive_structure.py'),
               'pinned_revision': info['sha'], 'repository': info['id'], 'metadata_sha256': sha(info_path),
               'codes': CODES, 'fields': FIELDS, 'maximum_new_network_bytes': CAP, 'new_network_bytes': 0,
               'whole_published_LFS_SHA256_verified': False, 'candidate_changed': False, 'candidate_hours_admitted': 0,
               'column_subsets_are_retained_native_rows_not_complete_remote_files': True, 'results': []}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))

    def acquire(filename, size, start, length, name):
        assert receipt['new_network_bytes'] + length <= CAP
        request, raw = fetch_range(info['id'], info['sha'], filename, size, start, length, name)
        receipt['new_network_bytes'] += length
        return request, raw

    try:
        for field in FIELDS:
            filename = field + '.parquet'; published = index[filename]; size = published['size']
            record = {'field': field, 'filename': filename, 'published_bytes': size,
                      'published_LFS_SHA256_not_verified': published['lfs']['sha256'], 'requests': []}
            receipt['results'].append(record)
            if filename in old:
                previous = old[filename]['footer_request']; footer_path = Path(previous['path'])
                assert sha(footer_path) == previous['retained_range_sha256']
                raw = footer_path.read_bytes(); start = previous['start']
                record['reused_retained_footer'] = previous
            else:
                request, tail = acquire(filename, size, size - 8, 8, filename + '.pilot_last8.bin')
                record['requests'].append(request); assert tail[4:] == b'PAR1'
                footer_length = struct.unpack('<I', tail[:4])[0]; length = max(65536, footer_length + 8)
                assert length <= 8 * 1024 * 1024
                start = size - length
                request, raw = acquire(filename, size, start, length, filename + '.pilot_footer.bin')
                record['requests'].append(request); assert raw[-8:] == tail
            footer = pq.ParquetFile(FooterView(size, start, raw)); schema = footer.schema_arrow; metadata = footer.metadata
            assert metadata.num_row_groups == 1 and metadata.num_rows == 478800
            record.update(rows=metadata.num_rows, row_groups=metadata.num_row_groups,
                          available_target_columns=[c for c in CODES if c in schema.names],
                          missing_target_columns=[c for c in CODES if c not in schema.names])
            selected = record['available_target_columns'] + ['__index_level_0__']
            segments = [(start, raw)]
            request, header = acquire(filename, size, 0, 4, filename + '.pilot_header.bin')
            record['requests'].append(request); assert header == b'PAR1'; segments.append((0, header))
            group = metadata.row_group(0)
            record['column_chunks'] = []
            for code in selected:
                column = group.column(schema.names.index(code))
                offset = column.dictionary_page_offset if column.has_dictionary_page else column.data_page_offset
                length = column.total_compressed_size
                assert 0 < length <= 12 * 1024 * 1024
                request, chunk = acquire(filename, size, offset, length, filename + '.' + code + '.chunk.bin')
                record['requests'].append(request); segments.append((offset, chunk))
                record['column_chunks'].append({'column': code, 'offset': offset, 'bytes': length,
                                               'source_column_physical_type': column.physical_type,
                                               'compression': column.compression, 'range_sha256': request['retained_range_sha256']})
                output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            parser = pq.ParquetFile(SparseView(size, segments), pre_buffer=False, page_checksum_verification=True)
            table = parser.read(columns=selected, use_threads=False)
            assert table.num_rows == metadata.num_rows
            dest = DEST / (field + '_four_stock_native_columns.parquet'); assert not dest.exists()
            pq.write_table(table, dest, compression='zstd')
            record.update(status='actual_native_columns_decoded_and_retained', selection_path=str(dest),
                          selection_sha256=sha(dest), selection_rows=table.num_rows,
                          source_schema_metadata={k.decode(): v.decode() for k, v in (schema.metadata or {}).items()},
                          page_checksum_verification_requested=True, presence_of_original_page_checksums_not_certified=True)
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print(json.dumps({'field': field, 'available': record['available_target_columns'],
                              'missing': record['missing_target_columns'], 'native_rows': table.num_rows,
                              'cumulative_new_network_bytes': receipt['new_network_bytes']}), flush=True)
        receipt['status'] = 'six_field_native_pilot_columns_acquired'
    except Exception as error:
        receipt.update(status='failed_terminal', error_type=type(error).__name__, error_detail=str(error)[:300])
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: receipt[k] for k in ['status', 'new_network_bytes', 'whole_published_LFS_SHA256_verified', 'candidate_hours_admitted']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
