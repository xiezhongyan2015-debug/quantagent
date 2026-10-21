"""Retain bounded parquet footers and a gzip prefix; never claim full-file hashes."""
import hashlib
import io
import json
import ssl
import struct
import sys
import tarfile
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_native_inventory_20261009'
MAX_RANGE_BYTES = 32 * 1024 * 1024


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch_range(repo, revision, filename, size, start, length, name):
    assert length <= MAX_RANGE_BYTES and 0 <= start <= size - length
    end = start + length - 1
    url = ('https://huggingface.co/datasets/' + repo + '/resolve/' + revision + '/' + urllib.parse.quote(filename, safe='/') +
           '?download=true&audit_start=' + str(start) + '&audit_end=' + str(end))
    request = urllib.request.Request(url, headers={'Range': 'bytes=' + str(start) + '-' + str(end),
                                                 'User-Agent': 'QuantResearch-public-data-audit/1.0'})
    with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
        assert response.status == 206
        assert response.headers['Content-Range'] == 'bytes ' + str(start) + '-' + str(end) + '/' + str(size)
        raw = response.read(length + 1); assert len(raw) == length
        header = response.headers['Content-Range']
    path = OUT / name; assert not path.exists(); path.write_bytes(raw)
    return {'url': url, 'http_status': 206, 'content_range': header, 'path': str(path),
            'retained_range_sha256': sha(path), 'bytes': len(raw), 'start': start,
            'whole_published_LFS_sha256_verified': False, 'TLS_and_hostname_verification_enabled': True}, raw


class FooterView(io.RawIOBase):
    """Expose retained real footer bytes at their real logical file offsets."""
    def __init__(self, size, start, raw):
        self.size = size; self.start = start; self.raw = raw; self.position = 0
    def seekable(self):
        return True
    def readable(self):
        return True
    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        return self.position
    def tell(self):
        return self.position
    def read(self, count=-1):
        if count < 0:
            count = self.size - self.position
        assert self.start <= self.position <= self.size
        assert self.position + count <= self.size
        begin = self.position - self.start
        self.position += count
        return self.raw[begin:begin + count]


def main():
    output = OUT / 'bounded_structure_probe_receipt.json'; assert not output.exists()
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'whole_remote_archives_downloaded': False, 'whole_LFS_SHA256_verified': False,
               'external_code_downloaded_or_executed': False, 'candidate_changed': False,
               'candidate_hours_admitted': 0, 'results': []}
    info = json.loads((OUT / 'suncong__ashare_1min_info.json').read_text())
    index = {r['rfilename']: r for r in info['siblings']}
    for filename in ['close.parquet', 'volume.parquet']:
        published = index[filename]; size = published['size']
        record = {'repository': info['id'], 'revision': info['sha'], 'filename': filename,
                  'published_bytes': size, 'published_LFS_sha256_not_verified': published['lfs']['sha256']}
        try:
            request, tail = fetch_range(info['id'], info['sha'], filename, size, size - 8, 8, filename + '.last8.bin')
            assert tail[4:] == b'PAR1'
            footer_length = struct.unpack('<I', tail[:4])[0]
            record.update(footer_length=footer_length, last8_request=request)
            length = max(65536, footer_length + 8)
            if length > MAX_RANGE_BYTES:
                record['status'] = 'footer_exceeds_bounded_probe_cap_no_payload_download'
            else:
                request, raw = fetch_range(info['id'], info['sha'], filename, size, size - length, length, filename + '.footer.bin')
                assert raw[-8:] == tail
                metadata = pq.ParquetFile(FooterView(size, size - length, raw)).metadata
                schema = pq.ParquetFile(FooterView(size, size - length, raw)).schema_arrow
                names = schema.names
                stats = []
                for i in range(metadata.num_row_groups):
                    group = metadata.row_group(i)
                    for col in [0, len(names) - 1]:
                        stat = group.column(col).statistics
                        stats.append({'row_group': i, 'column': names[col], 'rows': group.num_rows,
                                      'min': str(stat.min) if stat and stat.has_min_max else None,
                                      'max': str(stat.max) if stat and stat.has_min_max else None})
                record.update(status='actual_footer_metadata_parsed', footer_request=request,
                              rows=metadata.num_rows, row_groups=metadata.num_row_groups, columns=metadata.num_columns,
                              schema_first_columns=names[:15], schema_last_columns=names[-15:],
                              first_and_last_column_statistics=stats,
                              pandas_index_metadata=json.loads((schema.metadata or {}).get(b'pandas', b'null')))
        except Exception as error:
            record.update(status='failed_terminal', error_type=type(error).__name__, error_detail=str(error)[:200])
        receipt['results'].append(record); output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in record.items() if k not in ('pandas_index_metadata', 'first_and_last_column_statistics', 'last8_request', 'footer_request')}, ensure_ascii=False), flush=True)
    info = json.loads((OUT / 'suncong__Ashare-events_info.json').read_text())
    published = next(r for r in info['siblings'] if r['rfilename'] == '20260923.tar.gz')
    record = {'repository': info['id'], 'revision': info['sha'], 'filename': published['rfilename'],
              'published_bytes': published['size'], 'published_LFS_sha256_not_verified': published['lfs']['sha256']}
    try:
        request, raw = fetch_range(info['id'], info['sha'], published['rfilename'], published['size'], 0,
                                   8 * 1024 * 1024, '20260923.tar.gz.first8MiB.bin')
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        unpacked = decoder.decompress(raw, 64 * 1024 * 1024)
        assert len(unpacked) <= 64 * 1024 * 1024
        entries = []; record.update(prefix_request=request, decompressed_prefix_bytes=len(unpacked),
                                     whole_gzip_stream_eof_seen=decoder.eof, whole_gzip_CRC_verified=False)
        stream = tarfile.open(fileobj=io.BytesIO(unpacked), mode='r|')
        for member in stream:
            entry = {'name': member.name, 'declared_bytes': member.size, 'type': str(member.type)}
            if member.isfile():
                content = stream.extractfile(member).read(min(member.size, 8192))
                entry['first_8192_bytes_or_less_utf8_preview'] = content.decode('utf-8', errors='replace')
            entries.append(entry)
            if len(entries) >= 12:
                break
        record.update(status='actual_gzip_tar_prefix_entries_observed', entries=entries)
    except Exception as error:
        record.update(status='failed_terminal_after_bounded_prefix', error_type=type(error).__name__, error_detail=str(error)[:200])
        if 'entries' in locals():
            record['entries_observed_before_prefix_boundary'] = entries
    receipt['results'].append(record); receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in record.items() if k not in ('entries', 'entries_observed_before_prefix_boundary', 'prefix_request')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
