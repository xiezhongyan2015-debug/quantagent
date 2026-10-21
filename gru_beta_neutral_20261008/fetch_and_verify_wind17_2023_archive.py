"""Download one pinned data-only ZIP and verify its entire advertised LFS digest."""
import hashlib
import json
import shutil
import ssl
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from inspect_wind17_remote_zip import BASE, sha

OUT = BASE / 'verified_whole_2023_archive'


def main():
    OUT.mkdir(exist_ok=True)
    metadata_path = BASE / 'dataset_info_blobs.json'
    metadata = json.loads(metadata_path.read_text())
    file = next(f for f in metadata['siblings'] if f['rfilename'] == '2023.zip')
    assert metadata['sha'] == 'ca4c51770f1fafb40317cc35bfd92e7c2be17827'
    assert not metadata.get('gated') and not metadata.get('private')
    expected_size, expected_sha = file['lfs']['size'], file['lfs']['sha256']
    assert expected_size == file['size'] == 3644873876
    assert shutil.disk_usage(OUT).free > expected_size + 30 * 1024**3
    target, partial = OUT / '2023.zip', OUT / '2023.zip.partial'
    receipt_path = OUT / 'archive_acquisition_receipt.json'
    assert not any(p.exists() for p in [target, partial, receipt_path])
    url = 'https://huggingface.co/datasets/wind17/china-a-share-zip/resolve/' + metadata['sha'] + '/2023.zip'
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'status': 'downloading_one_pinned_data_archive',
        'source_code_sha256': sha(__file__), 'metadata_path': str(metadata_path), 'metadata_sha256': sha(metadata_path),
        'url': url, 'expected_size_bytes': expected_size, 'expected_whole_LFS_SHA256': expected_sha,
        'authentication_used': False, 'TLS_and_hostname_verification_enabled': True, 'downloaded_code_executed': False,
        'candidate_changed': False, 'candidate_hours_admitted': 0}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    count, digest = 0, hashlib.sha256()
    started = last = time.monotonic()
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'PublicHistoricalArchiveIntegrityReview/1.0'})
        with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response, partial.open('xb') as stream:
            assert response.status == 200
            receipt.update(http_status=response.status, resolved_host=urllib.parse.urlsplit(response.url).hostname)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                assert count <= expected_size
                stream.write(chunk)
                digest.update(chunk)
                if time.monotonic() - last >= 15:
                    print(json.dumps({'bytes_received': count, 'expected_bytes': expected_size,
                                      'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
                    last = time.monotonic()
        assert count == expected_size and digest.hexdigest() == expected_sha
        partial.rename(target)
        sample_path = BASE / 'gap_year_samples/sz300114_2023.csv'
        sample = sample_path.read_bytes()
        with zipfile.ZipFile(target) as archive:
            assert len(archive.infolist()) == 5374
            exact = archive.read('2023/sz300114.csv')
            assert exact == sample
        receipt.update(status='complete_whole_archive_SHA256_and_target_CSV_verified', path=str(target),
            whole_LFS_SHA256_verified=True, whole_archive_sha256=digest.hexdigest(), bytes_received=count,
            target_CSV_path=str(sample_path), target_CSV_sha256=hashlib.sha256(sample).hexdigest(),
            selected_range_CSV_equals_whole_archive_member=True, central_directory_entries=5374,
            archive_CRC_scope='Selected target CSV checked by full read; other archive payload CRCs not yet checked')
    except Exception as error:
        receipt.update(status='archive_acquisition_or_verification_failed', error_type=type(error).__name__, error=str(error),
                       bytes_received=count, partial_sha256=digest.hexdigest(), partial_path=str(partial))
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    receipt['elapsed_seconds'] = round(time.monotonic() - started, 2)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
