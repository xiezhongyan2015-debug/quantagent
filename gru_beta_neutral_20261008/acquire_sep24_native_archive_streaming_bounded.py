"""Hash, decompress and select a pinned archive in one resumable stream.

The compressed spool is temporary. A retry replays its prefix through the
useful gzip/tar pass, then continues the public Range transfer; it does not
prehash that prefix separately. Completed members remain guarded and no
market data is admitted before complete published SHA and gzip CRC pass.
"""
import io
import gzip
import tarfile
from . import bounded_tail_pipeline as p

SOURCE=p.BASE/'public_source_probes/additional_native_inventory_20261009'
DEST=SOURCE/'whole_event_archive_sep24_streaming'
REVISION='301397490bea1a168083298da77f750a083a4ef1'
DAY='2026-09-24'
MEMBER_CAP=128*1024**2
RETENTION_CAP=8*1024**3
DISK_RESERVE=6*1024**3


class ResumableDigestStream(io.RawIOBase):
    def __init__(self,partial,url,size,receipt,save):
        super().__init__();self.partial=partial;self.url=url;self.size=size;self.receipt=receipt;self.save=save;self.position=0;self.digest=p.hashlib.sha256();self.prefix_bytes=partial.stat().st_size if partial.exists() else 0
        assert 0<=self.prefix_bytes<=size
        self.prefix=partial.open('rb') if self.prefix_bytes else None;self.spool=partial.open('ab');self.response=None;self.last=p.time.monotonic();self.request=None;self.started=p.time.monotonic()
        receipt.update(resumed_compressed_prefix_bytes=self.prefix_bytes,compressed_prefix_not_prehashed=True)
    def readable(self):return True
    def tell(self):return self.position
    def read(self,size=-1):
        assert 0<=size<=4*1024**2,'Only bounded compressed reads permitted'
        if not size or self.position==self.size:return b''
        size=min(size,self.size-self.position)
        if self.position<self.prefix_bytes:
            raw=self.prefix.read(min(size,self.prefix_bytes-self.position));assert raw
        else:
            if self.response is None:
                start=self.prefix_bytes;url=self.url+f'?download=true&bounded_stream_start={start}'
                self.request=dict(started_utc=p.utc(),start=start,url=url);self.receipt['requests'].append(self.request);self.save()
                req=p.urllib.request.Request(url,headers={'Range':f'bytes={start}-{self.size-1}','User-Agent':'bounded-single-pass-native-archive-evidence/1.0'})
                self.response=p.urllib.request.urlopen(req,timeout=30,context=p.ssl.create_default_context());self.request.update(http_status=self.response.status,content_range=self.response.headers.get('Content-Range'),content_length=self.response.headers.get('Content-Length'),resolved_host=p.urllib.parse.urlsplit(self.response.url).hostname)
                if self.response.status==206:assert self.response.headers['Content-Range']==f'bytes {start}-{self.size-1}/{self.size}'
                else:assert self.response.status==200 and start==0 and int(self.response.headers['Content-Length'])==self.size
                self.save()
            raw=self.response.read(size)
            if not raw:raise EOFError(f'Pinned source short body at {self.position} of {self.size} compressed bytes')
            assert len(raw)<=size
            self.spool.write(raw)
        self.digest.update(raw);self.position+=len(raw)
        if p.time.monotonic()-self.last>=15:
            self.spool.flush();self.receipt.update(received_total_bytes=self.partial.stat().st_size,compressed_bytes_hashed_and_decompressed=self.position,source_network_bytes_this_attempt=max(0,self.position-self.prefix_bytes));self.save()
            print(p.json.dumps(dict(phase='single_stream_download_hash_useful_extract',received_bytes=self.receipt['received_total_bytes'],published_bytes=self.size,retained_members=len(self.receipt['records']),retained_bytes=sum(r['bytes'] for r in self.receipt['records']),peak_rss_bytes=self.receipt.get('peak_rss_bytes'))),flush=True);self.last=p.time.monotonic()
        assert p.time.monotonic()-self.started<1800,'Bounded stream retained for a later resume'
        return raw
    def close(self):
        if not self.closed:
            self.spool.flush();p.os.fsync(self.spool.fileno());self.spool.close()
            if self.prefix:self.prefix.close()
            if self.response:self.response.close()
        super().close()


def main():
    scope_calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    assert DAY in scope_calendar['dates'],'Source day outside the fixed panel calendar; transfer forbidden'
    assert any(key[0]==DAY for key in p.targets()),'Source day has no requested actual missing hours; transfer forbidden'
    lock=p.locked('sep24_native_archive_single_stream');DEST.mkdir(exist_ok=True);receipt_path=DEST/'acquisition_and_extraction.json'
    metadata_path=SOURCE/'suncong__Ashare-events_info.json';metadata=p.json.loads(metadata_path.read_text());assert metadata['sha']==REVISION and not metadata['private'] and not metadata['gated']
    item=next(r for r in metadata['siblings'] if r['rfilename']=='20260924.tar.gz');size=item['size'];expected=item['lfs']['sha256']
    assert size==15721191431 and expected=='8234fce60996203c33985b4ee06148017649758d4a619800923f00334885f2a0'
    partial=DEST/(item['rfilename']+'.part');calendar_path=p.ROOT/'gru_research/artifacts/minute_calendar.json';calendar=p.json.loads(calendar_path.read_text());requested=set(calendar['union_symbols']);assert len(requested)==1178
    result=p.json.loads(receipt_path.read_text()) if receipt_path.exists() else dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),producer_script_path=__file__,metadata_sha256=p.sha(metadata_path),repository=metadata['id'],pinned_revision=REVISION,filename=item['rfilename'],published_bytes=size,published_LFS_sha256=expected,compressed_spool_path=str(partial),scope_date=DAY,calendar_sha256=p.sha(calendar_path),requested_symbols=sorted(requested),requested_union_stocks=1178,credentials_used=False,TLS_and_hostname_verification_enabled=True,requests=[],records=[],candidate_hours_admitted=0,production_ready=False,status='queued_single_stream',whole_published_LFS_SHA256_verified=False,whole_gzip_CRC_verified=False,compressed_spool_is_temporary=True,compressed_spool_deleted_after_full_SHA_and_CRC=False,maximum_retained_member_bytes=MEMBER_CAP,extraction_total_retention_cap=RETENTION_CAP,disk_reserve_bytes=DISK_RESERVE,unattended_popups=False)
    assert result['source_code_sha256']==p.sha(__file__) and result['metadata_sha256']==p.sha(metadata_path)
    def save():result.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(receipt_path,result)
    if result['status']=='complete_published_digest_crc_and_union_members_verified':
        assert result['whole_published_LFS_SHA256_verified'] and result['whole_gzip_CRC_verified'] and not partial.exists();lock.close();return
    retained={r['original_member']:r for r in result['records']}
    for r in retained.values():assert p.identity(r['path'])==r['verified_identity']
    # Maximum newly retained native bytes plus remaining spool and sixGiB free.
    existing_spool=partial.stat().st_size if partial.exists() else 0;existing_members=sum(r['bytes'] for r in retained.values())
    assert p.shutil.disk_usage(DEST).free>(size-existing_spool)+(RETENTION_CAP-existing_members)+DISK_RESERVE
    root=DEST/'union_native_members';root.mkdir(exist_ok=True);stream=None
    try:
        for attempt in range(3):
            seen=set();members=written=0;last=p.time.monotonic();result.update(status='single_stream_hash_gzip_crc_and_useful_extraction',stream_attempt=attempt+1);save()
            url=f'https://huggingface.co/datasets/{metadata["id"]}/resolve/{REVISION}/{item["rfilename"]}';stream=ResumableDigestStream(partial,url,size,result,save)
            try:
                with stream,gzip.GzipFile(fileobj=stream) as zipped:
                    with tarfile.open(fileobj=zipped,mode='r|') as archive:
                        for member in archive:
                            members+=1;parts=member.name.split('/')
                            if member.isfile() and len(parts)==3 and parts[0]=='20260924' and parts[1] in requested and parts[2] in ['events.parquet','snapshot.parquet']:
                                assert member.name not in seen and member.size<=MEMBER_CAP;seen.add(member.name);written+=member.size;assert written<=RETENTION_CAP
                                old=retained.get(member.name)
                                if old:assert p.identity(old['path'])==old['verified_identity']
                                else:
                                    assert p.shutil.disk_usage(DEST).free>member.size+DISK_RESERVE
                                    folder=root/('symbol='+parts[1]);folder.mkdir(exist_ok=True);path=folder/parts[2];temp=path.with_suffix('.parquet.part');assert not path.exists()
                                    digest=p.hashlib.sha256();copied=0
                                    with archive.extractfile(member) as source,temp.open('wb') as target:
                                        while chunk:=source.read(1024**2):target.write(chunk);digest.update(chunk);copied+=len(chunk)
                                    assert copied==member.size;pf=p.pq.ParquetFile(temp);temp.replace(path)
                                    r=dict(original_member=member.name,symbol=parts[1],kind=parts[2][:-8],path=str(path),bytes=copied,sha256=digest.hexdigest(),verified_identity=p.identity(path),rows=pf.metadata.num_rows,row_groups=pf.num_row_groups,columns=pf.schema_arrow.names,actual_source_values_preserved=True);result['records'].append(r);retained[member.name]=r
                            if p.time.monotonic()-last>=15:result.update(members_seen=members,written_bytes=written);save();last=p.time.monotonic()
                            if members%1000==0:archive.members.clear()
                    while zipped.read(1024**2):pass
                    assert stream.position==size and stream.digest.hexdigest()==expected
                assert partial.stat().st_size==size and p.sha(calendar_path)==result['calendar_sha256']
                pairs={s for s in requested if f'20260924/{s}/events.parquet' in seen and f'20260924/{s}/snapshot.parquet' in seen}
                assert seen==set(retained)
                result.update(status='complete_stream_integrity_pending_compressed_spool_cleanup',received_total_bytes=size,compressed_bytes_hashed_and_decompressed=size,actual_sha256=stream.digest.hexdigest(),whole_published_LFS_SHA256_verified=True,whole_gzip_CRC_verified=True,gzip_footer_and_EOF_checked_during_useful_extraction=True,whole_archive_separate_prehash_or_rehash=False,members_seen=members,written_bytes=written,complete_event_snapshot_pairs=len(pairs),missing_requested_members=[f'20260924/{s}/{name}' for s in sorted(requested) for name in ['events.parquet','snapshot.parquet'] if f'20260924/{s}/{name}' not in seen],verified_compressed_spool_identity=p.identity(partial));save()
                assert p.identity(partial)==result['verified_compressed_spool_identity'];partial.unlink()
                result.update(status='complete_published_digest_crc_and_union_members_verified',compressed_spool_deleted_after_full_SHA_and_CRC=True,compressed_spool_removed_bytes=size,finished_utc=p.utc());save();break
            except Exception as exc:
                stream.close();failure=dict(failed_utc=p.utc(),attempt=attempt+1,error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc(),compressed_bytes_retained=partial.stat().st_size if partial.exists() else 0,source_stream_consumed_bytes=stream.position)
                result.setdefault('failures',[]).append(failure);result.update(status='stream_failed_complete_native_members_and_compressed_prefix_retained',received_total_bytes=failure['compressed_bytes_retained']);save()
                if isinstance(exc,AssertionError) or isinstance(exc,p.urllib.error.HTTPError) and exc.code in [401,403,429] or attempt==2:raise
                # Reuse validated complete members; discard only our own
                # interrupted member temporaries before replaying the prefix.
                for path in root.glob('symbol=*/*.parquet.part'):path.unlink()
    except Exception as exc:
        result.update(status='failure_payload_and_complete_members_retained',error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc());save();raise
    finally:
        if stream:stream.close()
        lock.close()


if __name__=='__main__':main()
