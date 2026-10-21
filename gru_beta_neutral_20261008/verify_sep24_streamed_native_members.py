"""Check accepted single-stream digest/CRC evidence and bounded member footers.

No repeat whole archive read; its temporary spool was already removed after
full integrity success. Absent requested source members are reported, never
automatically classified as a suspension or promoted into minute data.
"""
from . import bounded_tail_pipeline as p
from .acquire_sep24_native_archive_streaming_bounded import DEST,SOURCE,REVISION,DAY


def main():
    lock=p.locked('verify_sep24_streamed_native_members');path=DEST/'acquisition_and_extraction.json';r=p.json.loads(path.read_text());output=DEST/'independent_member_verification.json';assert not output.exists()
    assert r['status']=='complete_published_digest_crc_and_union_members_verified' and r['whole_published_LFS_SHA256_verified'] and r['whole_gzip_CRC_verified']
    producer=p.ROOT/'gru_beta_neutral_20261008/acquire_sep24_native_archive_streaming_bounded.py';assert r['source_code_sha256']==p.sha(producer)
    metadata_path=SOURCE/'suncong__Ashare-events_info.json';info=p.json.loads(metadata_path.read_text());assert p.sha(metadata_path)==r['metadata_sha256'] and info['sha']==REVISION and not info['private'] and not info['gated']
    item=next(s for s in info['siblings'] if s['rfilename']=='20260924.tar.gz');assert item['size']==r['published_bytes']==r['received_total_bytes']==r['compressed_bytes_hashed_and_decompressed']==r['compressed_spool_removed_bytes']
    assert item['lfs']['sha256']==r['actual_sha256']==r['published_LFS_sha256'] and r['gzip_footer_and_EOF_checked_during_useful_extraction']
    assert r['compressed_spool_deleted_after_full_SHA_and_CRC'] and not p.Path(r['compressed_spool_path']).exists() and not r['whole_archive_separate_prehash_or_rehash']
    calendar_path=p.ROOT/'gru_research/artifacts/minute_calendar.json';assert p.sha(calendar_path)==r['calendar_sha256'];calendar=p.json.loads(calendar_path.read_text());assert set(calendar['union_symbols'])==set(r['requested_symbols']) and len(r['requested_symbols'])==1178
    seen=set();total=0
    for leaf in r['records']:
        key=leaf['symbol'],leaf['kind'];assert key not in seen;seen.add(key);assert leaf['original_member']==f"20260924/{leaf['symbol']}/{leaf['kind']}.parquet" and leaf['symbol'] in r['requested_symbols'] and leaf['kind'] in ['events','snapshot']
        file=p.Path(leaf['path']);assert p.identity(file)==leaf['verified_identity'] and file.stat().st_size==leaf['bytes'] and leaf['actual_source_values_preserved'];pf=p.pq.ParquetFile(file)
        assert pf.metadata.num_rows==leaf['rows'] and pf.num_row_groups==leaf['row_groups'] and pf.schema_arrow.names==leaf['columns']
        if leaf['kind']=='events':assert pf.schema_arrow.metadata[b'price_scale']==b'10000' and pf.schema_arrow.metadata[b'qty_scale']==b'1'
        assert p.identity(file)==leaf['verified_identity'];total+=leaf['bytes']
    assert total==r['written_bytes'] and total<=r['extraction_total_retention_cap'];pairs={s for s in r['requested_symbols'] if (s,'events') in seen and (s,'snapshot') in seen};assert len(pairs)==r['complete_event_snapshot_pairs']
    absent_members=[f'20260924/{s}/{name}.parquet' for s in sorted(r['requested_symbols']) for name in ['events','snapshot'] if (s,name) not in seen];assert absent_members==r['missing_requested_members']
    targets={x['symbol'] for k,g in p.targets().items() if k[0]==DAY for x in g.values()};assert len(targets)==624
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),published_archive_digest_reused_with_stream_byte_count_and_pinned_metadata_guard=True,useful_extraction_gzip_crc_and_eof_passed=True,compressed_spool_removed_after_complete_integrity_proof=True,full_archive_not_rehashed=True,member_digests_reused_with_stat_and_footer_guards=True,union_source_pairs=len(pairs),requested_missing_stockdays=len(targets),actual_missing_stockday_source_pairs=len(targets&pairs),source_absent_requested_stockdays=sorted(targets-pairs),source_absence_not_automatically_classified_as_suspension=True,retained_source_bytes=total,transaction_fields_and_minute_admission_pending=True,candidate_hours_admitted=0,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss));lock.close()


if __name__=='__main__':main()
