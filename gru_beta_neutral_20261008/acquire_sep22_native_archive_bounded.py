"""Resume a public pinned archive with one in-flight digest, then stream members.

The gzip CRC check is combined with the useful extraction pass. No repeated
whole-archive hash, no concatenated transaction tables, no market admission.
"""
import gzip
import tarfile
from . import bounded_tail_pipeline as p

SOURCE=p.BASE/'public_source_probes/additional_native_inventory_20261009'
DEST=SOURCE/'whole_event_archive_sep22'
REVISION='301397490bea1a168083298da77f750a083a4ef1'
DAY='2026-09-22'


def main():
    lock=p.locked('sep22_native_archive');DEST.mkdir(exist_ok=True)
    receipt_path=DEST/'acquisition_and_extraction.json'
    metadata_path=SOURCE/'suncong__Ashare-events_info.json'
    metadata=p.json.loads(metadata_path.read_text())
    assert metadata['sha']==REVISION and not metadata['private'] and not metadata['gated']
    item=next(r for r in metadata['siblings'] if r['rfilename']=='20260922.tar.gz')
    size=item['size'];expected=item['lfs']['sha256']
    assert size==16570740230 and expected=='1161ccaf6d1cd5b8c390c4a9bc4730ac51540adf488b58d1c03c43d197a7ecc1'
    assert p.shutil.disk_usage(DEST).free>size+24*1024**3
    archive_path=DEST/item['rfilename'];partial=archive_path.with_suffix('.tar.gz.part')
    calendar_path=p.ROOT/'gru_research/artifacts/minute_calendar.json'
    calendar=p.json.loads(calendar_path.read_text());requested=set(calendar['union_symbols']);assert len(requested)==1178
    result=p.json.loads(receipt_path.read_text()) if receipt_path.exists() else dict(
        started_utc=p.utc(),source_code_sha256=p.sha(__file__),metadata_sha256=p.sha(metadata_path),
        repository=metadata['id'],pinned_revision=REVISION,filename=item['rfilename'],published_bytes=size,
        published_LFS_sha256=expected,path=str(archive_path),partial_path=str(partial),scope_date=DAY,
        calendar_sha256=p.sha(calendar_path),requested_symbols=sorted(requested),requested_union_stocks=1178,
        credentials_used=False,TLS_and_hostname_verification_enabled=True,requests=[],records=[],
        candidate_hours_admitted=0,production_ready=False,status='queued',whole_published_LFS_SHA256_verified=False,
        whole_gzip_CRC_verified=False,download_chunk_bytes=1024**2,extraction_chunk_bytes=1024**2,
        maximum_retained_member_bytes=128*1024**2,extraction_total_retention_cap=16*1024**3,
        whole_archive_rehash_before_extraction=False,unattended_popups=False)
    assert result['source_code_sha256']==p.sha(__file__) and result['metadata_sha256']==p.sha(metadata_path)
    def save():
        result.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
        p.atomic_json(receipt_path,result)
    try:
        if not result['whole_published_LFS_SHA256_verified']:
            assert not archive_path.exists()
            digest=p.hashlib.sha256();count=partial.stat().st_size if partial.exists() else 0
            assert count<=size
            if count:
                with partial.open('rb') as stream:
                    while chunk:=stream.read(1024**2):digest.update(chunk)
                result['partial_resume_digest_bytes_read_once']=count
            started=p.time.monotonic();last=started
            result.update(status='downloading_with_inflight_digest',received_total_bytes=count);save()
            for attempt in range(3):
                if count==size:break
                start=count
                url=f'https://huggingface.co/datasets/{metadata["id"]}/resolve/{REVISION}/{item["rfilename"]}?download=true&bounded_start={start}'
                record=dict(started_utc=p.utc(),start=start,url=url);result['requests'].append(record);save()
                try:
                    request=p.urllib.request.Request(url,headers={'Range':f'bytes={start}-{size-1}','User-Agent':'QuantResearch-public-data-audit/1.0'})
                    with p.urllib.request.urlopen(request,timeout=30,context=p.ssl.create_default_context()) as response:
                        record['http_status']=response.status
                        if response.status==206:assert response.headers['Content-Range']==f'bytes {start}-{size-1}/{size}'
                        else:assert response.status==200 and start==0 and int(response.headers['Content-Length'])==size
                        with partial.open('ab') as stream:
                            while chunk:=response.read(min(1024**2,size-count+1)):
                                assert count+len(chunk)<=size
                                stream.write(chunk);digest.update(chunk);count+=len(chunk)
                                if p.time.monotonic()-last>=15:
                                    stream.flush();result['received_total_bytes']=count;save()
                                    print(p.json.dumps(dict(phase='download',received_bytes=count,total_bytes=size,peak_rss_bytes=result['peak_rss_bytes'])),flush=True)
                                    last=p.time.monotonic()
                                assert p.time.monotonic()-started<1800,'bounded transfer retained for a later resume'
                    assert count==size;record.update(status='complete',finished_utc=p.utc())
                except Exception as exc:
                    record.update(status='failure_partial_retained',error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc(),finished_utc=p.utc())
                    result['received_total_bytes']=count;save()
                    if isinstance(exc,AssertionError) or isinstance(exc,p.urllib.error.HTTPError) and exc.code in [401,403,429]:raise
            assert count==size and partial.stat().st_size==size and digest.hexdigest()==expected
            partial.replace(archive_path)
            result.update(status='download_verified_pending_useful_crc_extraction',received_total_bytes=count,
                whole_published_LFS_SHA256_verified=True,actual_sha256=digest.hexdigest(),verified_archive_identity=p.identity(archive_path))
            save()
        assert p.identity(archive_path)==result['verified_archive_identity']
        if result['whole_gzip_CRC_verified']:
            assert result['status']=='complete_published_digest_crc_and_union_members_verified';lock.close();return
        members_root=DEST/'union_native_members';members_root.mkdir(exist_ok=True)
        retained={r['original_member']:r for r in result['records']};seen=set();members=written=0
        result['status']='streaming_useful_extraction_and_gzip_crc';save();last=p.time.monotonic()
        with archive_path.open('rb') as raw,gzip.GzipFile(fileobj=raw) as zipped:
            with tarfile.open(fileobj=zipped,mode='r|') as archive:
                for member in archive:
                    members+=1;pieces=member.name.split('/')
                    if member.isfile() and len(pieces)==3 and pieces[0]=='20260922' and pieces[1] in requested and pieces[2] in ['events.parquet','snapshot.parquet']:
                        assert member.name not in seen and member.size<=result['maximum_retained_member_bytes']
                        seen.add(member.name);written+=member.size;assert written<=result['extraction_total_retention_cap']
                        old=retained.get(member.name)
                        if old is not None:
                            assert p.identity(p.Path(old['path']))==old['verified_identity']
                        else:
                            folder=members_root/('symbol='+pieces[1]);folder.mkdir(exist_ok=True)
                            path=folder/pieces[2];temp=path.with_suffix('.parquet.part');assert not path.exists()
                            digest=p.hashlib.sha256();copied=0
                            with archive.extractfile(member) as source,temp.open('wb') as target:
                                while chunk:=source.read(1024**2):target.write(chunk);digest.update(chunk);copied+=len(chunk)
                            assert copied==member.size
                            pf=p.pq.ParquetFile(temp);temp.replace(path)
                            record=dict(original_member=member.name,symbol=pieces[1],kind=pieces[2][:-8],path=str(path),bytes=copied,sha256=digest.hexdigest(),verified_identity=p.identity(path),rows=pf.metadata.num_rows,row_groups=pf.num_row_groups,columns=pf.schema_arrow.names,actual_source_values_preserved=True)
                            result['records'].append(record);retained[member.name]=record
                    if p.time.monotonic()-last>=15:
                        result.update(members_seen=members,written_bytes=written);save()
                        print(p.json.dumps(dict(phase='stream_extract_crc',members_seen=members,retained_members=len(result['records']),written_bytes=written)),flush=True);last=p.time.monotonic()
                    if members%1000==0:archive.members.clear()
            while zipped.read(1024**2):pass
        assert p.identity(archive_path)==result['verified_archive_identity']
        assert p.sha(calendar_path)==result['calendar_sha256']
        paired={s for s in requested if f'20260922/{s}/events.parquet' in seen and f'20260922/{s}/snapshot.parquet' in seen}
        result.update(status='complete_published_digest_crc_and_union_members_verified',whole_gzip_CRC_verified=True,
            gzip_footer_and_EOF_checked_during_useful_extraction=True,members_seen=members,written_bytes=written,
            complete_event_snapshot_pairs=len(paired),missing_requested_members=[f'20260922/{s}/{name}' for s in sorted(requested) for name in ['events.parquet','snapshot.parquet'] if f'20260922/{s}/{name}' not in seen],finished_utc=p.utc());save()
        print(p.json.dumps(dict(status=result['status'],complete_source_pairs=len(paired),peak_rss_bytes=result['peak_rss_bytes'],candidate_hours_admitted=0)),flush=True)
    except Exception as exc:
        result.update(status='failure_payload_and_complete_members_retained',error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc());save();raise
    finally:lock.close()


if __name__=='__main__':main()
