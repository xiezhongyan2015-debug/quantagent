"""Inspect ZIP central directories by bounded anonymous HTTPS range reads.

No extracted code is executed. Selective ranges do not verify the whole LFS
object digest; range bytes and central-directory CRC metadata are retained.
"""
import hashlib
import io
import json
import ssl
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_public_archives_20261009/wind17'
OUT = BASE / 'zip_directory_inspection'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RangeReader(io.RawIOBase):
    def __init__(self, url, size, directory):
        self.url, self.size, self.directory = url, size, directory
        self.position = 0
        self.receipts = []
        self.total = 0
        directory.mkdir(exist_ok=True)

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        if not 0 <= position <= self.size:
            raise ValueError('Seek outside source object')
        self.position = position
        return position

    def read(self, size=-1):
        size = self.size - self.position if size < 0 else min(size, self.size - self.position)
        if not size:
            return b''
        assert 0 < size <= 16 * 1024 * 1024
        assert len(self.receipts) < 12 and self.total + size <= 24 * 1024 * 1024
        start, end = self.position, self.position + size - 1
        path = self.directory / ('range_%d_%d.bin' % (start, end))
        assert not path.exists()
        request = urllib.request.Request(self.url, headers={'User-Agent': 'PublicHistoricalArchiveRangeReview/1.0', 'Range': 'bytes=%d-%d' % (start, end)})
        info = {'started_utc': datetime.now(timezone.utc).isoformat(), 'range_start': start, 'range_end': end,
                'url': self.url, 'path': str(path)}
        self.receipts.append(info)
        with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
            info.update(http_status=response.status, content_range=response.headers.get('Content-Range'),
                        resolved_host=urllib.parse.urlsplit(response.url).hostname, etag=response.headers.get('ETag'))
            assert response.status == 206, 'Server did not honor the bounded Range request'
            assert info['content_range'] == 'bytes %d-%d/%d' % (start, end, self.size)
            raw = response.read(size + 1)
            assert len(raw) == size
        path.write_bytes(raw)
        info.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), completed_utc=datetime.now(timezone.utc).isoformat())
        self.total += size
        self.position += size
        return raw


def main():
    OUT.mkdir(exist_ok=True)
    path = BASE / 'dataset_info_blobs.json'
    metadata = json.loads(path.read_text())
    assert metadata['sha'] == 'ca4c51770f1fafb40317cc35bfd92e7c2be17827' and not metadata.get('gated') and not metadata.get('private')
    result = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'metadata_path': str(path), 'metadata_sha256': sha(path), 'results': [],
              'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
              'archive_whole_LFS_SHA256_verified': False, 'candidate_changed': False, 'candidate_hours_admitted': 0}
    assert not (OUT / 'directory_receipt.json').exists()
    for year in ['2018', '2026']:
        file = next(f for f in metadata['siblings'] if f['rfilename'] == year + '.zip')
        url = 'https://huggingface.co/datasets/wind17/china-a-share-zip/resolve/' + metadata['sha'] + '/' + file['rfilename']
        reader = RangeReader(url, file['size'], OUT / year)
        item = {'year': year, 'source_file': file, 'revision': metadata['sha'], 'status': 'pending', 'range_receipts': reader.receipts}
        try:
            with zipfile.ZipFile(reader) as archive:
                entries = archive.infolist()
                assert len(entries) <= 200000
                item.update(status='central_directory_acquired', entry_count=len(entries),
                            entries=[{'filename': e.filename, 'compressed_bytes': e.compress_size, 'uncompressed_bytes': e.file_size,
                                      'CRC32': e.CRC, 'compression_method': e.compress_type,
                                      'local_header_offset': e.header_offset, 'encrypted': bool(e.flag_bits & 1),
                                      'utf8_filename': bool(e.flag_bits & 2048)} for e in entries])
        except Exception as error:
            item.update(status='range_directory_read_failed', error_type=type(error).__name__, error=str(error))
        item['range_bytes_acquired'] = reader.total
        result['results'].append(item)
        print(json.dumps({'year': year, 'status': item['status'], 'entry_count': item.get('entry_count'),
                          'filenames_first_30': [e['filename'] for e in item.get('entries', [])][:30],
                          'range_bytes_acquired': reader.total, 'error': item.get('error')}, ensure_ascii=False), flush=True)
        (OUT / 'directory_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    result['completed_utc'] = datetime.now(timezone.utc).isoformat()
    (OUT / 'directory_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
