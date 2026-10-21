"""Inspect the exact remaining-gap years and read targeted raw CSV members."""
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from inspect_wind17_remote_zip import RangeReader, sha, BASE

OUT = BASE / 'gap_year_samples'
TARGETS = [('2023', 'sz300114'), ('2017', 'sz000022')]


def main():
    OUT.mkdir(exist_ok=True)
    metadata_path = BASE / 'dataset_info_blobs.json'
    metadata = json.loads(metadata_path.read_text())
    receipt_path = OUT / 'sample_acquisition_receipt.json'
    assert not receipt_path.exists()
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
        'range_reader_path': str(Path(__file__).parent / 'inspect_wind17_remote_zip.py'),
        'range_reader_sha256': sha(Path(__file__).parent / 'inspect_wind17_remote_zip.py'),
        'metadata_path': str(metadata_path), 'metadata_sha256': sha(metadata_path),
        'results': [], 'whole_archive_LFS_SHA256_verified': False,
        'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
        'candidate_changed': False, 'candidate_hours_admitted': 0}
    for year, symbol in TARGETS:
        file = next(f for f in metadata['siblings'] if f['rfilename'] == year + '.zip')
        url = 'https://huggingface.co/datasets/wind17/china-a-share-zip/resolve/' + metadata['sha'] + '/' + file['rfilename']
        reader = RangeReader(url, file['size'], OUT / (symbol + '_' + year + '_source_ranges'))
        target = year + '/' + symbol + '.csv'
        result = {'year': year, 'symbol': symbol, 'zip_member': target, 'source_file': file,
                  'pinned_revision': metadata['sha'], 'range_receipts': reader.receipts, 'status': 'pending'}
        try:
            with zipfile.ZipFile(reader) as archive:
                names = archive.namelist()
                result['directory_entry_count'] = len(names)
                assert len(names) <= 200000
                if target not in names:
                    result['status'] = 'target_not_in_central_directory'
                else:
                    entry = archive.getinfo(target)
                    assert not entry.flag_bits & 1
                    assert entry.file_size <= 16 * 1024 * 1024 and entry.compress_size <= 4 * 1024 * 1024
                    assert entry.file_size <= entry.compress_size * 500
                    with archive.open(entry) as stream:
                        raw = stream.read(entry.file_size + 1)
                    assert len(raw) == entry.file_size
                    path = OUT / (symbol + '_' + year + '.csv')
                    assert not path.exists()
                    path.write_bytes(raw)
                    result.update(status='exact_CSV_member_acquired_CRC_verified', path=str(path), bytes=len(raw), sha256=sha(path),
                                  CRC32=entry.CRC, member_CRC32_verified=True, ZIP_whole_digest_verified=False,
                                  first_512_bytes_gbk_preview=raw[:512].decode('gbk', errors='replace'))
        except Exception as error:
            result.update(status='gap_year_sample_failed', error_type=type(error).__name__, error=str(error))
        result['range_bytes_acquired'] = reader.total
        receipt['results'].append(result)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k not in ('source_file', 'range_receipts')}, ensure_ascii=False), flush=True)
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
