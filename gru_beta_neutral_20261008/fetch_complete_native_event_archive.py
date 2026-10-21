"""Authenticate one complete pinned public archive; retain all source bytes.

Use cached verified initial ranges. Limited resumable HTTPS requests, bounded
published size and disk reservation. No credentials or downloaded code.
"""
import gzip
import hashlib
import json
import shutil
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_native_inventory_20261009'
OUT = SOURCE / 'whole_event_archive'
REVISION = '301397490bea1a168083298da77f750a083a4ef1'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def main():
    OUT.mkdir(exist_ok=True)
    receipt_path = OUT / 'complete_archive_acquisition.json'
    assert not receipt_path.exists()
    info_path = SOURCE / 'suncong__Ashare-events_info.json'
    info = json.loads(info_path.read_text())
    assert info['sha'] == REVISION and info['private'] is False and info['gated'] is False
    published = next(r for r in info['siblings'] if r['rfilename'] == '20260923.tar.gz')
    size, digest = published['size'], published['lfs']['sha256']
    assert size == 15803513411 and size <= 16 * 1024 ** 3
    assert digest == '57627258bd6a46fafdef5f5a91454853ed612a44b40c86141a38bf8d35a04132'
    prior_path = SOURCE / 'bounded_native_millisecond_sample_acquisition.json'
    prior = json.loads(prior_path.read_text())
    assert prior['metadata_sha256'] == sha(info_path)
    assert shutil.disk_usage(OUT).free >= 40 * 1024 ** 3
    path = OUT / '20260923.tar.gz'
    temporary = OUT / '20260923.tar.gz.part'
    assert not path.exists() and not temporary.exists()
    result = {'started_utc': utc(), 'source_code_sha256': sha(__file__), 'repository': info['id'],
              'pinned_revision': REVISION, 'metadata_sha256': sha(info_path), 'filename': published['rfilename'],
              'published_bytes': size, 'published_LFS_sha256': digest, 'path': str(path),
              'partial_path': str(temporary), 'cached_initial_ranges_receipt_sha256': sha(prior_path),
              'personal_credentials_used': False, 'TLS_and_hostname_verification_enabled': True,
              'external_code_downloaded_or_executed': False, 'candidate_changed': False,
              'candidate_hours_admitted': 0, 'production_ready': False, 'status': 'acquiring_complete_archive',
              'whole_published_LFS_SHA256_verified': False, 'whole_gzip_CRC_verified': False, 'requests': []}
    def persist():
        receipt_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with temporary.open('wb') as stream:
        for r in [prior['initial_range'], prior['extension_range']]:
            assert sha(r['path']) == r['retained_range_sha256'] and stream.tell() == r['start']
            stream.write(Path(r['path']).read_bytes())
    count = temporary.stat().st_size
    result['cached_bytes_reused'] = count
    result['received_total_bytes'] = count
    persist()
    started = last = time.monotonic()
    try:
        for attempt in range(1, 4):
            if count == size:
                break
            start = count
            url = ('https://huggingface.co/datasets/suncong/Ashare-events/resolve/' + REVISION +
                   '/20260923.tar.gz?download=true&complete_audit_start=' + str(start))
            r = {'attempt': attempt, 'url': url, 'start': start, 'end': size - 1, 'started_utc': utc()}
            result['requests'].append(r)
            persist()
            try:
                request = urllib.request.Request(url, headers={'Range': 'bytes=' + str(start) + '-' + str(size - 1),
                                                              'User-Agent': 'QuantResearch-public-data-audit/1.0'})
                with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
                    assert response.status == 206
                    assert response.headers['Content-Range'] == 'bytes ' + str(start) + '-' + str(size - 1) + '/' + str(size)
                    r.update(http_status=206, content_range=response.headers['Content-Range'])
                    with temporary.open('ab') as stream:
                        while True:
                            chunk = response.read(min(1024 * 1024, size - count + 1))
                            if not chunk:
                                break
                            assert count + len(chunk) <= size
                            stream.write(chunk)
                            count += len(chunk)
                            if time.monotonic() - last >= 20:
                                stream.flush()
                                result.update(received_total_bytes=count, network_bytes_received=count - result['cached_bytes_reused'])
                                persist()
                                print(json.dumps({'phase': 'whole_archive_download', 'received_total_bytes': count,
                                                  'published_bytes': size, 'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
                                last = time.monotonic()
                            assert time.monotonic() - started <= 1800, 'Retain bounded run instead of an unbounded stalled download'
                assert count == size
                r.update(status='completed_range', completed_utc=utc())
            except Exception as error:
                r.update(status='failed_range_retained_partial', error_type=type(error).__name__, completed_utc=utc())
                result['received_total_bytes'] = count
                persist()
                if isinstance(error, AssertionError):
                    raise
        assert count == size and temporary.stat().st_size == size
        actual_hash = sha(temporary)
        assert actual_hash == digest
        temporary.rename(path)
        result.update(status='complete_published_SHA256_verified_checking_gzip', received_total_bytes=count,
                      network_bytes_received=count - result['cached_bytes_reused'], actual_sha256=actual_hash,
                      whole_published_LFS_SHA256_verified=True)
        persist()
        print(json.dumps({'phase': 'whole_published_SHA256_verified', 'bytes': count, 'sha256': actual_hash}), flush=True)
        # Drain the actual entire gzip stream, validating its real CRC and EOF.
        decompressed = 0
        last = time.monotonic()
        with gzip.open(path, 'rb') as stream:
            while True:
                chunk = stream.read(4 * 1024 * 1024)
                if not chunk:
                    break
                decompressed += len(chunk)
                assert decompressed <= 256 * 1024 ** 3
                if time.monotonic() - last >= 20:
                    result['decompressed_bytes_checked'] = decompressed
                    persist()
                    print(json.dumps({'phase': 'whole_gzip_CRC_check', 'decompressed_bytes_checked': decompressed}), flush=True)
                    last = time.monotonic()
        result.update(status='complete_whole_archive_SHA256_and_gzip_CRC_verified', whole_gzip_CRC_verified=True,
                      whole_gzip_EOF_observed=True, decompressed_bytes_checked=decompressed, completed_utc=utc())
        persist()
        print(json.dumps({k: v for k, v in result.items() if k != 'requests'}, ensure_ascii=False), flush=True)
    except Exception as error:
        result.update(status='failed_terminal_preserved_payload', error_type=type(error).__name__,
                      received_total_bytes=count, completed_utc=utc())
        persist()
        print(json.dumps({'status': result['status'], 'error_type': type(error).__name__, 'received_total_bytes': count}), flush=True)
        raise


if __name__ == '__main__':
    main()
