"""Inspect two historical days using pinned Parquet ranges, not full downloads.

Range hashes and metadata are evidence for this diagnostic only. They do not
verify the complete publisher file SHA or independent upstream provenance.
"""
import io
from datetime import date as Date
from . import bounded_tail_pipeline as p

SOURCE=p.BASE/'public_source_probes/alternative_coarse_public_inventory_20261010/ANTICH__traderharness-ashare-5y'
DEST=SOURCE/'pinned_range_stockday_probe'


class BoundedRanges(io.RawIOBase):
    def __init__(self,url,size,folder,progress):
        super().__init__();self.url=url;self.size=size;self.folder=folder;self.position=0;self.receipts=[];self.total=0;self.progress=progress
        folder.mkdir(exist_ok=True)

    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position

    def seek(self,offset,whence=0):
        position=offset if whence==0 else self.position+offset if whence==1 else self.size+offset
        assert 0<=position<=self.size;self.position=position;return position

    def read(self,size=-1):
        size=self.size-self.position if size<0 else min(size,self.size-self.position)
        if not size:return b''
        assert 0<size<=32*1024**2
        start=self.position;end=start+size-1
        for r in self.receipts:
            if r.get('bytes') and r['start']<=start and end<=r['end']:
                raw=p.Path(r['path']).read_bytes();assert p.hashlib.sha256(raw).hexdigest()==r['sha256'];self.position+=size;return raw[start-r['start']:end-r['start']+1]
        assert len(self.receipts)<64 and self.total+size<=128*1024**2
        # Cap each request/read. One Arrow column chunk may need several ranges.
        chunks=[]
        for a in range(start,end+1,4*1024**2):
            b=min(end,a+4*1024**2-1);path=self.folder/f'range_{a}_{b}.bin';assert not path.exists()
            r=dict(start=a,end=b,url=self.url,path=str(path),started_utc=p.utc());self.receipts.append(r);self.progress()
            req=p.urllib.request.Request(self.url,headers={'User-Agent':'bounded-historical-parquet-evidence/1.0','Range':f'bytes={a}-{b}'})
            try:
                with p.urllib.request.urlopen(req,context=p.ssl.create_default_context(),timeout=25) as response:
                    r.update(status=response.status,content_range=response.headers.get('Content-Range'),etag=response.headers.get('ETag'),resolved_host=p.urllib.parse.urlsplit(response.url).hostname)
                    assert response.status==206 and r['content_range']==f'bytes {a}-{b}/{self.size}'
                    raw=response.read(b-a+2);assert len(raw)==b-a+1
                path.write_bytes(raw);r.update(bytes=len(raw),sha256=p.hashlib.sha256(raw).hexdigest(),finished_utc=p.utc());self.total+=len(raw);chunks.append(raw);self.progress()
            except Exception:
                r.update(error_traceback=p.traceback.format_exc(),failed_utc=p.utc());self.progress();raise
        self.position+=size;return b''.join(chunks)


def main():
    lock=p.locked('probe_pinned_coarse_stockdays_ranges');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    manifest_path=SOURCE/'manifest.json';manifest=p.json.loads(manifest_path.read_text());inventory_path=SOURCE/'receipt.json';inventory=p.json.loads(inventory_path.read_text())
    assert p.sha(manifest_path)==next(r['sha256'] for r in inventory['records'] if r['path']==str(manifest_path))
    revision=inventory['pinned_revision'];info=p.json.loads((SOURCE/'info.json').read_text());assert revision==info['sha'] and not info['gated'] and not info['private']
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='inspecting_pinned_metadata_and_stockday_ranges',source_inventory_sha256=p.sha(inventory_path),manifest_sha256=p.sha(manifest_path),pinned_revision=revision,
        credentials_used=False,TLS_and_hostname_verification_enabled=True,whole_published_file_SHA_verified=False,upstream_independence_certified=False,candidate_hours_admitted=0,raw_candidate_modified=False,records=[])
    p.atomic_json(output,result)
    for ticker,date in [('688122','2023-10-18'),('688235','2025-01-10')]:
        file=next(f for f in manifest['files'] if f['path']==f'5min_clean/year={date[:4]}/part-6.parquet')
        sibling=next(f for f in info['siblings'] if f['rfilename']==file['path']);assert sibling['size']==file['bytes'] and sibling['lfs']['sha256']==file['sha256']
        url='https://huggingface.co/datasets/ANTICH/traderharness-ashare-5y/resolve/'+revision+'/'+file['path'];item=dict(symbol=ticker+'.SH',date=date,published_file=file,url=url,status='reading_footer',ranges=[]);result['records'].append(item)
        def progress():
            result.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
        reader=BoundedRanges(url,file['bytes'],DEST/(ticker+'_'+date+'_ranges'),progress);item['ranges']=reader.receipts;progress()
        try:
            pf=p.pq.ParquetFile(reader,pre_buffer=False);names=pf.schema_arrow.names
            assert names==['stock_code','date','datetime','open','high','low','close','volume','amount']
            target=Date.fromisoformat(date);selected=[];stats=[]
            for i in range(pf.metadata.num_row_groups):
                rg=pf.metadata.row_group(i);code=rg.column(names.index('stock_code')).statistics;dt=rg.column(names.index('date')).statistics
                lo=code.min if code and code.has_min_max else None;hi=code.max if code and code.has_min_max else None
                if isinstance(lo,bytes):lo=lo.decode();hi=hi.decode()
                dlow=dt.min if dt and dt.has_min_max else None;dhigh=dt.max if dt and dt.has_min_max else None
                eligible=(lo is None or lo<=ticker<=hi) and (dlow is None or dlow<=target<=dhigh)
                stats.append(dict(group=i,rows=rg.num_rows,stock_code_min=lo,stock_code_max=hi,date_min=str(dlow),date_max=str(dhigh),eligible=eligible))
                if eligible:selected.append(i)
            item.update(status='reading_only_candidate_rowgroups',file_rows=pf.metadata.num_rows,rowgroups=pf.metadata.num_row_groups,schema=str(pf.schema_arrow),rowgroup_stats=stats,selected_rowgroups=selected);progress()
            rows=[];scanned=0
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,row_groups=selected,use_threads=False):
                scanned+=len(batch);table=p.pa.Table.from_batches([batch]);keep=p.pc.and_(p.pc.equal(table['stock_code'],ticker),p.pc.equal(table['date'],p.pa.scalar(target,type=table.schema.field('date').type)))
                rows.extend(table.filter(keep).to_pylist());assert len(rows)<=1000
            path=DEST/(ticker+'_'+date+'_observed_5m.parquet')
            if rows:p.pq.write_table(p.pa.Table.from_pylist(rows),path,compression='zstd')
            item.update(status='selected_day_rows_retained_diagnostic_only',rows_scanned=scanned,rows_retained=len(rows),parquet_path=str(path) if rows else None,parquet_sha256=p.sha(path) if rows else None,range_bytes_acquired=reader.total)
        except Exception:
            item.update(status='range_probe_failed_with_stack',traceback=p.traceback.format_exc(),range_bytes_acquired=reader.total)
        finally:reader.close();progress()
    result.update(status='complete_pinned_range_probe_diagnostic_only',finished_utc=p.utc());progress();lock.close()


if __name__=='__main__':main()
