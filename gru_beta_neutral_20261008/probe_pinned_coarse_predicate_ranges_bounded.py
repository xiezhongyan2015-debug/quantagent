"""Read code/date first, reusing accepted ranges, before fetching prices.

Broad row-group statistics cannot select a single day. This diagnostic instead
scans just predicate columns, then actual matching groups, within fixed limits.
Incomplete publisher file fingerprints remain explicitly unverified.
"""
import io
from datetime import date as Date
from . import bounded_tail_pipeline as p
from .probe_pinned_coarse_stockdays_by_ranges_bounded import SOURCE

PRIOR=SOURCE/'pinned_range_stockday_probe'
DEST=SOURCE/'pinned_predicate_range_stockday_probe'


class ReusedRanges(io.RawIOBase):
    def __init__(self,url,size,folder,prior,progress):
        super().__init__();self.url=url;self.size=size;self.position=0;self.folder=folder;self.progress=progress;self.receipts=[];self.total=0;self.reused_bytes=0;self.verified=set();self.cache=[]
        folder.mkdir(exist_ok=True)
        for r in prior:
            if not r.get('bytes'):continue
            path=p.Path(r['path']);assert r['url']==url and r['end']-r['start']+1==r['bytes'] and path.stat().st_size==r['bytes']
            self.cache.append(dict(r,reused_prior_range=True,identity=p.identity(path)))
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        pos=offset if whence==0 else self.position+offset if whence==1 else self.size+offset;assert 0<=pos<=self.size;self.position=pos;return pos
    def cached_read(self,r,a,b):
        path=p.Path(r['path']);assert p.identity(path)==r['identity']
        if str(path) not in self.verified:
            assert p.sha(path)==r['sha256'];self.verified.add(str(path))
        with path.open('rb') as f:f.seek(a-r['start']);raw=f.read(b-a+1)
        assert len(raw)==b-a+1
        if r.get('reused_prior_range'):self.reused_bytes+=len(raw)
        return raw
    def read(self,size=-1):
        size=self.size-self.position if size<0 else min(size,self.size-self.position)
        if not size:return b''
        assert 0<size<=32*1024**2
        a=self.position;end=a+size-1;pieces=[]
        while a<=end:
            covering=[r for r in self.cache if r['start']<=a<=r['end']]
            if covering:
                r=max(covering,key=lambda x:x['end']);b=min(end,r['end']);pieces.append(self.cached_read(r,a,b));a=b+1;continue
            next_cached=min([r['start'] for r in self.cache if r['start']>a]+[end+1]);b=min(end,next_cached-1,a+4*1024**2-1)
            assert len(self.receipts)<1024 and self.total+b-a+1<=128*1024**2
            path=self.folder/f'range_{a}_{b}.bin';assert not path.exists();r=dict(start=a,end=b,url=self.url,path=str(path),started_utc=p.utc());self.receipts.append(r);self.progress()
            req=p.urllib.request.Request(self.url,headers={'User-Agent':'bounded-predicate-parquet-evidence/1.0','Range':f'bytes={a}-{b}'})
            try:
                with p.urllib.request.urlopen(req,context=p.ssl.create_default_context(),timeout=25) as response:
                    r.update(status=response.status,content_range=response.headers.get('Content-Range'),etag=response.headers.get('ETag'),resolved_host=p.urllib.parse.urlsplit(response.url).hostname)
                    assert response.status==206 and r['content_range']==f'bytes {a}-{b}/{self.size}'
                    raw=response.read(b-a+2);assert len(raw)==b-a+1
                path.write_bytes(raw);r.update(bytes=len(raw),sha256=p.hashlib.sha256(raw).hexdigest(),identity=p.identity(path),finished_utc=p.utc());self.total+=len(raw);self.cache.append(r);self.verified.add(str(path));pieces.append(raw);self.progress();a=b+1
            except Exception:r.update(error_traceback=p.traceback.format_exc(),failed_utc=p.utc());self.progress();raise
        self.position+=size;return b''.join(pieces)


def main():
    lock=p.locked('probe_pinned_coarse_predicate_ranges');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    prior_path=PRIOR/'receipt.json';prior=p.json.loads(prior_path.read_text());assert prior['status']=='complete_pinned_range_probe_diagnostic_only' and prior['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/probe_pinned_coarse_stockdays_by_ranges_bounded.py')
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),prior_range_receipt_sha256=p.sha(prior_path),status='bounded_predicate_index_first_probe',pinned_revision=prior['pinned_revision'],
        complete_file_publisher_SHA_verified=False,upstream_independence_certified=False,candidate_hours_admitted=0,raw_candidate_modified=False,bytes_limit_per_published_file=128*1024**2,request_limit_per_published_file=1024,credentials_used=False,TLS_verification_enabled=True,records=[])
    def save():result.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
    save()
    for old in prior['records']:
        ticker=old['symbol'][:6];target=Date.fromisoformat(old['date']);item=dict(symbol=old['symbol'],date=old['date'],published_file=old['published_file'],status='opening_reused_footer',ranges=[],predicate_rowgroups_complete=0,actual_matching_rowgroups=[]);result['records'].append(item)
        reader=ReusedRanges(old['url'],old['published_file']['bytes'],DEST/(ticker+'_'+old['date']+'_ranges'),old['ranges'],save);item['ranges']=reader.receipts;save()
        try:
            pf=p.pq.ParquetFile(reader,pre_buffer=False);assert pf.schema_arrow.names==['stock_code','date','datetime','open','high','low','close','volume','amount'];matching=[];counts=[]
            item.update(status='scanning_code_date_columns_only',rowgroups=pf.metadata.num_row_groups,file_rows=pf.metadata.num_rows);save()
            for i in old['selected_rowgroups']:
                count=0
                for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,row_groups=[i],columns=['stock_code','date'],use_threads=False):
                    table=p.pa.Table.from_batches([batch]);keep=p.pc.and_(p.pc.equal(table['stock_code'],ticker),p.pc.equal(table['date'],p.pa.scalar(target,type=table.schema.field('date').type)));count+=len(table.filter(keep))
                if count:matching.append(i);counts.append(dict(group=i,matched_rows=count))
                item.update(predicate_rowgroups_complete=item['predicate_rowgroups_complete']+1,actual_matching_rowgroups=counts,range_bytes_acquired=reader.total,prior_range_bytes_reused=reader.reused_bytes);save()
            item.update(status='reading_only_actual_matched_ohlcv_groups',matched_rows=sum(r['matched_rows'] for r in counts));save();rows=[]
            for i in matching:
                for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,row_groups=[i],use_threads=False):
                    table=p.pa.Table.from_batches([batch]);keep=p.pc.and_(p.pc.equal(table['stock_code'],ticker),p.pc.equal(table['date'],p.pa.scalar(target,type=table.schema.field('date').type)));rows.extend(table.filter(keep).to_pylist());assert len(rows)<=1000
            assert len(rows)==item['matched_rows'];path=DEST/(ticker+'_'+old['date']+'_observed_5m.parquet')
            if rows:p.pq.write_table(p.pa.Table.from_pylist(sorted(rows,key=lambda r:r['datetime'])),path,compression='zstd')
            item.update(status='complete_observed_stockday_diagnostic_only',rows_retained=len(rows),parquet_path=str(path) if rows else None,parquet_sha256=p.sha(path) if rows else None)
        except Exception:item.update(status='predicate_range_probe_failed_with_stack',traceback=p.traceback.format_exc())
        finally:
            item.update(range_bytes_acquired=reader.total,prior_range_bytes_reused=reader.reused_bytes,requests=len(reader.receipts));reader.close();save()
    result.update(status='complete_bounded_predicate_range_probe_no_admission',finished_utc=p.utc());save();lock.close()


if __name__=='__main__':main()
