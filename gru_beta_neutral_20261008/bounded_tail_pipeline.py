"""Resumable, bounded-memory public tail acquisition and observation pipeline.

Legacy receipts and V6 remain immutable. Whole-file digest is checked once on
acquisition; a local stat identity guards reuse. No trade tables are concatenated.
Minutes are observations pending independent concordance, never filled prices.
"""
import argparse
import csv
import fcntl
import hashlib
import json
import logging
import os
import resource
import shutil
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/phields_trades_20261009'
ACQ = SOURCE / 'all_actual_missing_tail_partitions'
OLD_EXTRACT = SOURCE / 'full_actual_missing_tail_trade_payloads'
OUT = SOURCE / 'streaming_resume_v1'
CORE = ['ticker', 'time_s', 'tran_id', 'price_x10000', 'volume']
COMPLETE = 'complete_whole_published_LFS_SHA256_and_Parquet_footer_verified'
BATCH_ROWS = 65536


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def identity(path):
    s = Path(path).stat()
    return dict(size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns,
                inode=s.st_ino, device=s.st_dev)


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def locked(name):
    OUT.mkdir(parents=True, exist_ok=True)
    f = (OUT / (name + '.lock')).open('a')
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def init_inventory():
    op = OUT / 'acquisition.json'
    if op.exists():
        return json.loads(op.read_text())
    previous = ACQ / 'all_actual_missing_tail_partition_acquisition.json'
    legacy = json.loads(previous.read_text())
    assert legacy['all_needed_published_partitions_requested'] == 226
    inventory = json.loads((SOURCE / 'dataset_info_blobs.json').read_text())
    assert sha(SOURCE / 'dataset_info_blobs.json') == legacy['pinned_inventory_sha256']
    assert inventory['sha'] == legacy['pinned_revision']
    assert not inventory['gated'] and not inventory['private']
    published = {r['rfilename']: r for r in inventory['siblings']}
    previous_sha = sha(previous)
    records = []
    for old in legacy['records']:
        item = published[old['filename']]
        assert item['size'] == old['published_bytes']
        assert item['lfs']['sha256'] == old['published_LFS_SHA256']
        r = {k: old[k] for k in ['filename', 'date', 'prefix', 'path', 'published_bytes', 'published_LFS_SHA256']}
        p = Path(r['path'])
        r.update(status='queued', attempts=[], received_bytes=0)
        if old['status'] == COMPLETE:
            assert p.exists() and p.stat().st_size == r['published_bytes']
            pf = pq.ParquetFile(p)
            assert pf.metadata.num_rows == old['Parquet_footer_rows']
            assert pf.num_row_groups == old['Parquet_footer_row_groups']
            r.update(status=COMPLETE, received_bytes=p.stat().st_size,
                     Parquet_footer_rows=pf.metadata.num_rows, Parquet_footer_row_groups=pf.num_row_groups,
                     verified_identity=identity(p), certification='legacy_published_digest_receipt_reused_with_size_footer_and_stat_guard',
                     legacy_receipt_sha256=previous_sha)
        else:
            assert not p.exists(), 'Unexpected file requires explicit certification'
        records.append(r)
    d = dict(started_utc=utc(), repository=legacy['repository'], pinned_revision=legacy['pinned_revision'],
             immutable_legacy_receipt_sha256=sha(previous), source_code_sha256=sha(__file__),
             requested_partitions=226, initial_preserved_partitions=177,
             initial_remaining_bytes=8790309553, download_workers=2,
             batch_rows=BATCH_ROWS, candidate_hours_admitted=0, candidate_changed=False,
             status='ready', records=records)
    assert sum(r['status'] == COMPLETE for r in records) == 177
    assert sum(r['published_bytes'] for r in records if r['status'] != COMPLETE) == 8790309553
    atomic_json(op, d)
    return d


def guarded_source(r):
    p = Path(r['path'])
    assert r['status'] == COMPLETE
    if identity(p) != r['verified_identity']:
        raise RuntimeError('Certified file identity changed; re-certification required: ' + str(p))
    pf = pq.ParquetFile(p)
    assert pf.metadata.num_rows == r['Parquet_footer_rows']
    return pf


def download():
    process_lock = locked('download')
    d = init_inventory()
    op = OUT / 'acquisition.json'
    mutex = threading.Lock()
    stop = threading.Event()
    last_save = [0.0]
    remaining = sum(r['published_bytes'] for r in d['records'] if r['status'] != COMPLETE)
    assert shutil.disk_usage(OUT).free > remaining + 10 * 1024**3

    def save(force=False):
        now = time.monotonic()
        if not force and now - last_save[0] < 10:
            return
        d.update(updated_utc=utc(), complete_partitions=sum(r['status'] == COMPLETE for r in d['records']),
                 complete_bytes=sum(r['published_bytes'] for r in d['records'] if r['status'] == COMPLETE),
                 peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        atomic_json(op, d)
        last_save[0] = now

    def transfer(r):
        if r['status'] == COMPLETE:
            guarded_source(r)
            return
        p = Path(r['path'])
        temp = p.with_suffix('.parquet.part')
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            # Crash between atomic publication and receipt save: certify once.
            assert p.stat().st_size == r['published_bytes'] and sha(p) == r['published_LFS_SHA256']
            pf = pq.ParquetFile(p)
            with mutex:
                r.update(status=COMPLETE, received_bytes=p.stat().st_size, verified_identity=identity(p),
                         Parquet_footer_rows=pf.metadata.num_rows, Parquet_footer_row_groups=pf.num_row_groups,
                         certification='recovered_atomic_publish_with_digest_and_footer')
                save(True)
            return
        url = 'https://huggingface.co/datasets/' + d['repository'] + '/resolve/' + d['pinned_revision'] + '/' + urllib.parse.quote(r['filename'], safe='/')
        for retry in range(4):
            if stop.is_set():
                return
            offset = temp.stat().st_size if temp.exists() else 0
            digest = hashlib.sha256()
            if offset:
                with temp.open('rb') as f:
                    while block := f.read(1024**2):
                        digest.update(block)
            attempt = dict(started_utc=utc(), resume_offset=offset, stage='request')
            with mutex:
                r['attempts'].append(attempt)
                r['status'] = 'transferring'
                save(True)
            try:
                if offset < r['published_bytes']:
                    headers = {'User-Agent': 'QuantResearch-public-data-audit/2.0'}
                    if offset:
                        headers['Range'] = 'bytes=' + str(offset) + '-'
                    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60,
                                                context=ssl.create_default_context()) as response:
                        assert response.url.startswith('https://')
                        attempt.update(http_status=response.status, stage='validate_response')
                        if offset and response.status == 200:
                            # Range ignored: overwrite, never append duplicate bytes.
                            offset = 0
                            digest = hashlib.sha256()
                            attempt['server_ignored_range_restart'] = True
                        elif offset:
                            assert response.status == 206
                            cr = response.headers.get('Content-Range', '')
                            assert cr.startswith('bytes ' + str(offset) + '-') and cr.endswith('/' + str(r['published_bytes']))
                        else:
                            assert response.status == 200
                        with temp.open('ab' if offset else 'wb') as f:
                            count = offset
                            while True:
                                attempt['stage'] = 'read_network'
                                block = response.read(1024**2)
                                if not block:
                                    break
                                count += len(block)
                                assert count <= r['published_bytes']
                                attempt['stage'] = 'write_local_file'
                                f.write(block)
                                digest.update(block)
                                with mutex:
                                    r['received_bytes'] = count
                                    save()
                            f.flush()
                            os.fsync(f.fileno())
                attempt['stage'] = 'size_digest_footer_certification'
                assert temp.stat().st_size == r['published_bytes'], 'Incomplete response body'
                assert digest.hexdigest() == r['published_LFS_SHA256'], 'Published digest mismatch'
                pf = pq.ParquetFile(temp)
                assert pf.schema_arrow.names[:5] == CORE
                footer = dict(Parquet_footer_rows=pf.metadata.num_rows, Parquet_footer_row_groups=pf.num_row_groups)
                temp.replace(p)
                with mutex:
                    attempt.update(completed_utc=utc(), stage='complete')
                    r.update(footer, status=COMPLETE, received_bytes=p.stat().st_size,
                             verified_identity=identity(p), certification='streamed_published_SHA256_and_footer')
                    save(True)
                return
            except Exception as error:
                logging.exception('Transfer failed %s stage=%s', r['filename'], attempt['stage'])
                with mutex:
                    attempt.update(completed_utc=utc(), error_type=type(error).__name__, error_message=str(error),
                                   traceback=traceback.format_exc(), http_error_status=getattr(error, 'code', None))
                    r['status'] = 'retry_pending' if retry < 3 else 'failed_preserved'
                    if isinstance(error, urllib.error.HTTPError) and error.code in (401, 403):
                        r['status'] = 'access_denied_preserved'
                        stop.set()
                    if isinstance(error, AssertionError) and attempt['stage'] == 'size_digest_footer_certification' and temp.exists() and temp.stat().st_size == r['published_bytes']:
                        temp.rename(temp.with_suffix('.invalid'))
                    save(True)
                if stop.is_set():
                    return
                time.sleep(min(2**retry, 8))
    with mutex:
        d['status'] = 'downloading'
        save(True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(transfer, d['records']))
    with mutex:
        d['status'] = 'complete' if all(r['status'] == COMPLETE for r in d['records']) else 'incomplete_preserved'
        d['finished_utc'] = utc()
        save(True)
    process_lock.close()


def targets():
    result = defaultdict(dict)
    with (SOURCE / 'every_missing_tail_stockday_metadata_coverage.csv').open() as f:
        for r in csv.DictReader(f):
            result[r['date'], r['symbol'][:2]][r['symbol'][:6]] = r
    assert sum(map(len, result.values())) == 21215
    return result


def stream_subset(pf, tickers, path):
    temp = path.with_suffix('.parquet.tmp')
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([pf.schema_arrow.field(k) for k in CORE])
    count = selected = batches = 0
    values = pa.array(sorted(tickers), type=schema.field('ticker').type)
    with pq.ParquetWriter(temp, schema, compression='zstd') as writer:
        for batch in pf.iter_batches(batch_size=BATCH_ROWS, columns=CORE, use_threads=False):
            count += len(batch)
            batches += 1
            one = pa.Table.from_batches([batch]).filter(pc.is_in(batch.column(0), value_set=values))
            if len(one):
                writer.write_table(one, row_group_size=BATCH_ROWS)
                selected += len(one)
    assert count == pf.metadata.num_rows
    temp.replace(path)
    return dict(all_source_rows_scanned=count, selected_rows=selected, batches=batches,
                selected_native_column_subset_path=str(path), selected_native_column_subset_sha256=sha(path),
                verified_identity=identity(path), max_input_batch_rows=BATCH_ROWS)


def extract():
    process_lock = locked('extract')
    wanted = targets()
    oldp = OLD_EXTRACT / 'full_actual_missing_tail_trade_payload_extraction.json'
    old = json.loads(oldp.read_text())
    op = OUT / 'extraction.json'
    d = json.loads(op.read_text()) if op.exists() else dict(started_utc=utc(), source_code_sha256=sha(__file__),
            legacy_extraction_receipt_sha256=sha(oldp), partitions=[], candidate_hours_admitted=0)
    parts = {r['filename']: r for r in d['partitions']}
    for r in old['partitions']:
        if r['filename'] not in parts:
            p = Path(r['selected_native_column_subset_path'])
            pf = pq.ParquetFile(p)
            assert pf.metadata.num_rows == r['selected_rows']
            copy = dict(r, verified_identity=identity(p), certification='legacy_subset_digest_receipt_and_footer_reused')
            d['partitions'].append(copy)
            parts[r['filename']] = copy
    while True:
        acq = json.loads((OUT / 'acquisition.json').read_text())
        for r in acq['records']:
            if r['filename'] in parts or r['status'] != COMPLETE:
                continue
            pf = guarded_source(r)
            p = OUT / 'subsets' / ('trade_date=' + r['date']) / ('code_prefix=' + r['prefix']) / Path(r['filename']).name
            result = stream_subset(pf, wanted[r['date'], r['prefix']], p)
            record = dict(result, filename=r['filename'], date=r['date'], prefix=r['prefix'],
                          complete_source_path=r['path'], complete_source_SHA256_verified=r['published_LFS_SHA256'])
            d['partitions'].append(record)
            parts[r['filename']] = record
            d.update(updated_utc=utc(), processed_partitions=len(parts), selected_rows=sum(x['selected_rows'] for x in parts.values()),
                     peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, status='extracting')
            atomic_json(op, d)
        d.update(updated_utc=utc(), processed_partitions=len(parts), selected_rows=sum(x['selected_rows'] for x in parts.values()),
                 peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if acq['status'] in ('complete', 'incomplete_preserved'):
            d['status'] = 'complete' if len(parts) == 226 else 'all_available_complete_parts_processed'
            atomic_json(op, d)
            process_lock.close()
            return
        d['status'] = 'waiting_for_certified_partitions'
        atomic_json(op, d)
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['inventory', 'download', 'extract'])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=OUT / (args.mode + '.log'), level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    if args.mode == 'inventory':
        process_lock = locked('download')
        init_inventory()
        process_lock.close()
    elif args.mode == 'download':
        download()
    else:
        extract()


if __name__ == '__main__':
    main()
