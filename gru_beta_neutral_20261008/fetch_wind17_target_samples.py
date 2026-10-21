"""Read exact selected CSV members from pinned public ZIP objects by ranges."""
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from inspect_wind17_remote_zip import RangeReader, sha, BASE

OUT = BASE / 'target_samples'
TARGETS = [('2018', 'sz000099'), ('2018', 'sz002831'), ('2018', 'sz300114'),
           ('2018', 'sh600167'), ('2026', 'sh600004')]


def main():
    OUT.mkdir(exist_ok=True)
    directory_path = BASE / 'zip_directory_inspection/directory_receipt.json'
    directory = json.loads(directory_path.read_text())
    assert sha(BASE / 'dataset_info_blobs.json') == directory['metadata_sha256']
    receipt_path = OUT / 'sample_acquisition_receipt.json'
    assert not receipt_path.exists()
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'range_reader_path': str(Path(__file__).parent / 'inspect_wind17_remote_zip.py'),
               'range_reader_sha256': sha(Path(__file__).parent / 'inspect_wind17_remote_zip.py'),
               'directory_receipt_sha256': sha(directory_path), 'results': [],
               'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
               'whole_archive_LFS_SHA256_verified': False,
               'extracted_downloaded_code_executed': False, 'candidate_changed': False, 'candidate_hours_admitted': 0}
    for year, symbol in TARGETS:
        year_info = next(r for r in directory['results'] if r['year'] == year)
        assert year_info['status'] == 'central_directory_acquired'
        target = year + '/' + symbol + '.csv'
        entry_info = next((r for r in year_info['entries'] if r['filename'] == target), None)
        item = {'year': year, 'symbol': symbol, 'zip_member': target, 'status': 'not_in_central_directory'}
        if entry_info is not None:
            assert not entry_info['encrypted'] and entry_info['uncompressed_bytes'] <= 16 * 1024 * 1024
            assert entry_info['compressed_bytes'] <= 4 * 1024 * 1024
            assert entry_info['uncompressed_bytes'] <= entry_info['compressed_bytes'] * 500
            reader = RangeReader('https://huggingface.co/datasets/wind17/china-a-share-zip/resolve/' + year_info['revision'] + '/' + year + '.zip',
                                 year_info['source_file']['size'], OUT / (symbol + '_' + year + '_source_ranges'))
            item.update(central_directory_entry=entry_info, range_receipts=reader.receipts,
                        pinned_revision=year_info['revision'], whole_archive_advertised_LFS_SHA256=year_info['source_file']['lfs']['sha256'])
            try:
                with zipfile.ZipFile(reader) as archive:
                    entry = archive.getinfo(target)
                    assert entry.CRC == entry_info['CRC32'] and entry.header_offset == entry_info['local_header_offset']
                    assert entry.compress_size == entry_info['compressed_bytes'] and entry.file_size == entry_info['uncompressed_bytes']
                    with archive.open(entry) as stream:
                        raw = stream.read(entry.file_size + 1)
                    assert len(raw) == entry.file_size
                path = OUT / (symbol + '_' + year + '.csv')
                assert not path.exists()
                path.write_bytes(raw)
                item.update(status='exact_CSV_member_acquired_CRC_verified', path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                            member_CRC32_verified=True, ZIP_whole_digest_verified=False,
                            first_512_bytes_utf8_preview=raw[:512].decode('utf-8', errors='replace'))
            except Exception as error:
                item.update(status='target_member_acquisition_failed', error_type=type(error).__name__, error=str(error))
            item['range_bytes_acquired'] = reader.total
        receipt['results'].append(item)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in item.items() if k not in ('range_receipts', 'central_directory_entry')}, ensure_ascii=False), flush=True)
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
