"""Check pinned digest provenance, stat identities and Parquet footers.

Reuse the in-flight archive/member digests. The required field scan and cross
source transaction comparison happen in a separate bounded pairing operation.
"""
from . import bounded_tail_pipeline as p
from .acquire_sep22_native_archive_bounded import DEST,SOURCE,REVISION


def main():
    lock=p.locked('verify_sep22_native_members')
    path=DEST/'acquisition_and_extraction.json';r=p.json.loads(path.read_text())
    assert r['status']=='complete_published_digest_crc_and_union_members_verified'
    assert r['whole_published_LFS_SHA256_verified'] and r['whole_gzip_CRC_verified']
    producer=p.Path(r.get('producer_script_path',str(p.ROOT/'gru_beta_neutral_20261008/acquire_sep22_native_archive_bounded.py')))
    assert producer in [p.ROOT/'gru_beta_neutral_20261008/acquire_sep22_native_archive_bounded.py',p.ROOT/'gru_beta_neutral_20261008/resume_sep22_native_archive_bounded_v2.py']
    assert r['source_code_sha256']==p.sha(producer)
    if 'prior_terminal_acquisition_receipt' in r:
        prior=r['prior_terminal_acquisition_receipt'];assert p.sha(prior['path'])==prior['sha256']
    metadata_path=SOURCE/'suncong__Ashare-events_info.json';info=p.json.loads(metadata_path.read_text())
    assert p.sha(metadata_path)==r['metadata_sha256'] and info['sha']==REVISION
    item=next(x for x in info['siblings'] if x['rfilename']=='20260922.tar.gz')
    assert item['size']==r['received_total_bytes']==r['published_bytes']
    assert item['lfs']['sha256']==r['actual_sha256']==r['published_LFS_sha256']
    assert p.identity(p.Path(r['path']))==r['verified_archive_identity']
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    assert set(calendar['union_symbols'])==set(r['requested_symbols'])
    seen=set();total=0
    for leaf in r['records']:
        key=leaf['symbol'],leaf['kind'];assert key not in seen;seen.add(key)
        assert leaf['original_member']==f"20260922/{leaf['symbol']}/{leaf['kind']}.parquet"
        assert leaf['symbol'] in r['requested_symbols'] and leaf['kind'] in ['events','snapshot']
        file=p.Path(leaf['path']);assert p.identity(file)==leaf['verified_identity'] and file.stat().st_size==leaf['bytes']
        pf=p.pq.ParquetFile(file)
        assert pf.metadata.num_rows==leaf['rows'] and pf.num_row_groups==leaf['row_groups'] and pf.schema_arrow.names==leaf['columns']
        if leaf['kind']=='events':
            assert pf.schema_arrow.metadata[b'price_scale']==b'10000' and pf.schema_arrow.metadata[b'qty_scale']==b'1'
        assert p.identity(file)==leaf['verified_identity'];total+=leaf['bytes']
    assert total==r['written_bytes']
    pairs={s for s in r['requested_symbols'] if (s,'events') in seen and (s,'snapshot') in seen}
    assert len(pairs)==r['complete_event_snapshot_pairs']
    targets={x['symbol'] for k,g in p.targets().items() if k[0]=='2026-09-22' for x in g.values()}
    assert len(targets)==624 and targets<=pairs
    p.atomic_json(DEST/'independent_member_verification.json',dict(passed=True,checked_utc=p.utc(),
        source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),published_archive_digest_reused_with_stat_guard=True,
        useful_extraction_gzip_crc_and_eof_passed=True,full_archive_not_rehashed=True,member_digests_reused_with_stat_and_footer_guards=True,
        union_source_pairs=len(pairs),actual_missing_stockday_source_pairs=len(targets),retained_source_bytes=total,
        transaction_fields_and_minute_admission_pending=True,candidate_hours_admitted=0,production_ready=False,
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    lock.close()


if __name__=='__main__':main()
