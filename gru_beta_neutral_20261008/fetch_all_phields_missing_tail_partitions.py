"""Acquire every published partition needed by the immutable full V6 tail.

Public data only, pinned revision, full LFS hashes, bounded resumable requests.
Ticker metadata is a scope reference, never minute/bar admission evidence.
"""
import csv
import hashlib
import json
import shutil
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/phields_trades_20261009'
OUT = SOURCE / 'all_actual_missing_tail_partitions'
REV = '3acef8b18f56786e253ccd034072a8f1845b507e'
REPO = 'phields/a-share-l2-trades'


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    op = OUT / 'all_actual_missing_tail_partition_acquisition.json'
    assert not op.exists(), 'Resume an existing live run, do not launch duplicates'
    ip = SOURCE / 'dataset_info_blobs.json'
    assert sha(ip) == '72f90f6108603626a72b022f47709e28336afb8bd33e81d7974bed10ac117b37'
    info = json.loads(ip.read_text())
    assert info['sha'] == REV and info['gated'] is False and info['private'] is False
    cp = SOURCE / 'exact_tail_metadata_coverage_receipt.json'
    coverage = json.loads(cp.read_text())
    proofp = SOURCE / 'independent_phields_findings_verification.json'
    proof = json.loads(proofp.read_text())
    assert proof['passed']
    csvp = Path(coverage['coverage_csv_path'])
    assert sha(csvp) == coverage['coverage_csv_sha256']
    rows = list(csv.DictReader(csvp.open()))
    assert len(rows) == 21215 and sum(int(r['actual_missing_nonmember_hours']) for r in rows) == 84860
    groups = {(r['date'], r['symbol'][:2]) for r in rows}
    partitions = []
    for item in info['siblings']:
        path = item['rfilename']
        if not path.startswith('data/l2_trades/trade_date=') or not path.endswith('.parquet'):
            continue
        date = path.split('trade_date=')[1].split('/')[0]
        prefix = path.split('code_prefix=')[1].split('/')[0]
        if (date, prefix) in groups:
            partitions.append((date, prefix, item))
    partitions.sort(key=lambda x: x[2]['rfilename'])
    assert len(partitions) == 226 and sum(p[2]['size'] for p in partitions) == 41458253700
    assert {(d, p) for d, p, _ in partitions} == groups
    mp = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(mp) == '6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    assert shutil.disk_usage(OUT).free > 41458253700 + 10 * 1024**3
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'repository': REPO, 'pinned_revision': REV, 'pinned_inventory_sha256': sha(ip),
               'full_original_missing_tail_scope_receipt_sha256': sha(cp), 'independent_prior_scope_proof_sha256': sha(proofp),
               'all_original_actual_missing_tail_stockdays_requested': 21215, 'all_original_actual_missing_tail_hours_requested': 84860,
               'metadata_present_stockdays': 21079, 'metadata_present_missing_hours': 84316,
               'metadata_absent_stockdays': 136, 'metadata_absent_missing_hours': 544,
               'all_needed_published_partitions_requested': 226, 'all_needed_published_partition_bytes': 41458253700,
               'full_union_2018_2026_scope_complete': False, 'full_tail_payload_verification_complete': False,
               'status': 'acquiring_all_226_published_partitions_for_exact_actual_missing_tail',
               'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
               'source_code_or_dependency_installation': False, 'parallel_public_data_transfers': 3,
               'maximum_attempts_per_file': 2, 'maximum_network_bytes_budget': 2 * 41458253700,
               'candidate_meta_sha256': sha(mp), 'candidate_changed': False, 'candidate_hours_admitted': 0,
               'production_ready': False, 'records': []}
    lock = threading.Lock()
    stop = threading.Event()
    last_print = time.monotonic()
    for date, prefix, item in partitions:
        dest = OUT / ('trade_date=' + date) / ('code_prefix=' + prefix) / Path(item['rfilename']).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        receipt['records'].append({'filename': item['rfilename'], 'date': date, 'prefix': prefix, 'path': str(dest),
                                  'published_bytes': item['size'], 'published_LFS_SHA256': item['lfs']['sha256'],
                                  'status': 'queued', 'attempts': [], 'received_bytes': 0})

    def persist_locked():
        nonlocal last_print
        receipt['complete_published_partitions'] = sum(r['status'] == 'complete_whole_published_LFS_SHA256_and_Parquet_footer_verified' for r in receipt['records'])
        receipt['complete_published_bytes'] = sum(r['published_bytes'] for r in receipt['records'] if r['status'] == 'complete_whole_published_LFS_SHA256_and_Parquet_footer_verified')
        receipt['last_updated_utc'] = datetime.now(timezone.utc).isoformat()
        tmp = op.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)); tmp.replace(op)
        if time.monotonic() - last_print >= 20:
            print(json.dumps({'completed_partitions': receipt['complete_published_partitions'], 'requested_partitions': 226,
                              'certified_bytes': receipt['complete_published_bytes'],
                              'received_bytes_all_files': sum(r['received_bytes'] for r in receipt['records'])}), flush=True)
            last_print = time.monotonic()

    def certify(record, path):
        assert path.stat().st_size == record['published_bytes'] and sha(path) == record['published_LFS_SHA256']
        pf = pq.ParquetFile(path)
        assert pf.schema_arrow.names == ['ticker', 'time_s', 'tran_id', 'price_x10000', 'volume', 'sale_order_volume', 'buy_order_volume', 'side', 'sale_order_id', 'sale_order_price_x10000', 'buy_order_id', 'buy_order_price_x10000']
        return {'sha256': record['published_LFS_SHA256'], 'Parquet_footer_rows': pf.metadata.num_rows, 'Parquet_footer_row_groups': pf.num_row_groups}

    def transfer(record):
        if stop.is_set():
            return
        path = Path(record['path']); temp = path.with_suffix('.parquet.part')
        cache = SOURCE / 'verified_whole_samples' / (record['date'] + '_' + record['prefix'] + '_' + path.name)
        if cache.exists():
            verified = certify(record, cache)
            assert not path.exists()
            path.hardlink_to(cache)
            with lock:
                record.update(verified, status='complete_whole_published_LFS_SHA256_and_Parquet_footer_verified',
                              received_bytes=record['published_bytes'], cached_verified_whole_sample_reused=True)
                persist_locked()
            return
        assert not path.exists() and not temp.exists()
        url = 'https://huggingface.co/datasets/' + REPO + '/resolve/' + REV + '/' + urllib.parse.quote(record['filename'], safe='/')
        for attempt in range(2):
            if stop.is_set():
                return
            offset = temp.stat().st_size if temp.exists() else 0
            with lock:
                record['status'] = 'transferring_public_pinned_data'
                ar = {'started_utc': datetime.now(timezone.utc).isoformat(), 'resume_offset': offset}
                record['attempts'].append(ar); persist_locked()
            headers = {'User-Agent': 'QuantResearch-public-data-audit/1.0'}
            if offset:
                headers['Range'] = 'bytes=' + str(offset) + '-'
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
                    assert response.url.startswith('https://')
                    if offset:
                        assert response.status == 206 and response.headers['Content-Range'].startswith('bytes ' + str(offset) + '-')
                    else:
                        assert response.status == 200
                    with temp.open('ab' if offset else 'wb') as f:
                        count = offset
                        while True:
                            if stop.is_set():
                                raise InterruptedError('another transfer encountered terminal access failure')
                            chunk = response.read(4 * 1024 * 1024)
                            if not chunk:
                                break
                            count += len(chunk); assert count <= record['published_bytes']
                            f.write(chunk)
                            with lock:
                                record['received_bytes'] = count
                                if time.monotonic() - last_print >= 20:
                                    persist_locked()
                    ar['http_status'] = response.status
                verified = certify(record, temp)
                temp.rename(path)
                with lock:
                    ar['completed_utc'] = datetime.now(timezone.utc).isoformat()
                    record.update(verified, status='complete_whole_published_LFS_SHA256_and_Parquet_footer_verified')
                    persist_locked()
                return
            except Exception as error:
                with lock:
                    ar.update(error_type=type(error).__name__, http_error_status=getattr(error, 'code', None), completed_utc=datetime.now(timezone.utc).isoformat())
                    if isinstance(error, urllib.error.HTTPError) and error.code in [401, 403]:
                        record['status'] = 'terminal_access_denial_no_bypass'; stop.set()
                    elif isinstance(error, (AssertionError, InterruptedError)) or attempt == 1:
                        record['status'] = 'terminal_file_transfer_failure_preserved'
                    elif isinstance(error, urllib.error.HTTPError) and error.code not in [408, 429, 500, 502, 503, 504]:
                        record['status'] = 'terminal_nonretryable_http_failure_preserved'
                    else:
                        record['status'] = 'bounded_resume_pending_after_transport_failure'
                    persist_locked()
                    terminal = record['status'].startswith('terminal_')
                if terminal:
                    return
                time.sleep(1)
    with lock:
        persist_locked()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(transfer, r) for r in receipt['records']]
        for future in as_completed(futures):
            future.result()
    with lock:
        receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
        full = all(r['status'] == 'complete_whole_published_LFS_SHA256_and_Parquet_footer_verified' for r in receipt['records'])
        receipt['full_tail_payload_verification_complete'] = full
        receipt['status'] = 'complete_all_226_needed_published_files_SHA256_and_footer_verified' if full else 'terminal_incomplete_full_tail_acquisition_preserved'
        assert sha(mp) == receipt['candidate_meta_sha256']
        persist_locked()
    print(json.dumps({k: v for k, v in receipt.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
