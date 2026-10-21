"""Retain two real historical stock columns; no whole remote matrix transfer.

The immutable cached native index establishes the exact date intersection before
any network access. Each field is decoded, filtered, and written immediately.
"""
import io
from . import bounded_tail_pipeline as p
from .probe_new_archive_structure import FooterView

SOURCE=p.BASE/'public_source_probes/additional_native_inventory_20261009'
DEST=SOURCE/'late_remaining_native_matrix_four_stock_bounded'
RAW=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_raw'
CODES=['688617','001914','603786','688172']
FIELDS=['open','high','low','close','volume','amount']
MAX_BYTES=24*1024**2

class SparseView(io.RawIOBase):
    def __init__(self,size,segments):self.size=size;self.segments=segments;self.position=0
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        self.position=offset if whence==0 else self.position+offset if whence==1 else self.size+offset
        assert 0<=self.position<=self.size;return self.position
    def read(self,count=-1):
        if count<0:count=self.size-self.position
        for start,raw in self.segments:
            if start<=self.position and self.position+count<=start+len(raw):
                i=self.position-start;self.position+=count;return raw[i:i+count]
        raise ValueError('Read outside actual retained native range')

def main():
    lock=p.locked('late_remaining_native_matrix');DEST.mkdir(exist_ok=True);out=DEST/'receipt.json';assert not out.exists()
    info_path=SOURCE/'suncong__ashare_1min_info.json';info=p.json.loads(info_path.read_text());assert info['sha']=='c05c0da3940e167d689392852e9d490107a2c3e1' and not info['gated'] and not info['private']
    old_path=SOURCE/'native_matrix_pilot/bounded_column_acquisition.json';old=p.json.loads(old_path.read_text());assert old['status']=='six_field_native_pilot_columns_acquired'
    raw_audit=RAW/'raw_full_history_audit.json';audit=p.json.loads(raw_audit.read_text());assert audit['passed'] and audit['stocks_checked']==1178
    calendar_path=p.ROOT/'gru_research/artifacts/minute_calendar.json';calendar=p.json.loads(calendar_path.read_text());mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    scope_path=p.BASE/'public_source_probes/additional_public_archives_20261009/wind17/all_minimum_gap_consolidation/verified_minimum_gap_source_scope.json';scope=p.json.loads(scope_path.read_text());assert scope['passed']
    time_source=SOURCE/'native_matrix_pilot/close_four_stock_native_columns.parquet';close=next(r for r in old['results'] if r['field']=='close');assert p.sha(time_source)==close['selection_sha256']
    index=p.pq.ParquetFile(time_source).read(columns=['__index_level_0__'],use_threads=False).column(0);stamps=index.to_pylist();assert len(stamps)==478800 and stamps==sorted(stamps)
    source_dates={s.strftime('%Y-%m-%d') for s in stamps};requested=[]
    failure_path=p.OUT/'historical_daily_failure_definition_diagnostic/receipt.json';failure=p.json.loads(failure_path.read_text());eligible={(r['symbol'],r['date']) for r in failure['records'] if r['date']>='2020-01-01' and r['date']<='2026-03-26' and r['failed_daily_fields']==['volume_difference_within_100_shares'] and r['symbol'][:6] in CODES}
    assert len(eligible)==6

    for r in scope['records']:
        if (r['symbol'],r['date']) not in eligible or r['date'] not in source_dates:continue
        assert r['date'] in calendar['dates']
        day=calendar['dates'].index(r['date']);column=calendar['union_symbols'].index(r['symbol']);slots=[s for s in r['actual_missing_slots'] if not mask[day*4+s,column]]
        if slots:requested.append(dict(symbol=r['symbol'],date=r['date'],slots=slots))
    assert requested and {r['symbol'][:6] for r in requested}==set(CODES)
    wanted={c:{r['date'] for r in requested if r['symbol'][:6]==c} for c in CODES};indices={c:[i for i,t in enumerate(stamps) if t.strftime('%Y-%m-%d') in wanted[c]] for c in CODES}
    for c in CODES:assert len(indices[c])==len(wanted[c])*240
    receipt=dict(status='scoped_column_acquisition_running',started_utc=p.utc(),source_code_sha256=p.sha(__file__),repository=info['id'],revision=info['sha'],metadata_sha256=p.sha(info_path),prior_acquisition_sha256=p.sha(old_path),raw_audit_sha256=p.sha(raw_audit),historical_scope_sha256=p.sha(scope_path),cached_native_timestamp_index_sha256=close['selection_sha256'],actual_requested_stockdays=requested,actual_missing_hours=sum(len(r['slots']) for r in requested),source_scope_intersection_checked_before_any_network=True,new_network_bytes=0,max_new_bytes=MAX_BYTES,results=[],candidate_hours_admitted=0,whole_remote_field_SHA_verified=False,provider_independence_certified=False)
    def save():receipt.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(out,receipt)
    save();deadline=p.time.monotonic()+1200
    try:
        for field in FIELDS:
            prev=next(r for r in old['results'] if r['field']==field);segments=[];pins=[]
            footer=prev.get('reused_retained_footer') or next(r for r in prev['requests'] if r['path'].endswith('pilot_footer.bin'))
            header=next(r for r in prev['requests'] if r['start']==0 and r['bytes']==4)
            for source in [footer,header]:
                path=p.Path(source['path']);raw=path.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==source['retained_range_sha256'];segments.append((source['start'],raw));pins.append(source)
            parser=p.pq.ParquetFile(SparseView(prev['published_bytes'],segments),pre_buffer=False);assert parser.metadata.num_rows==len(stamps) and parser.metadata.num_row_groups==1
            columns=[]
            for code in CODES:
                assert code in parser.schema_arrow.names
                col=parser.metadata.row_group(0).column(parser.schema_arrow.names.index(code));offset=col.dictionary_page_offset if col.has_dictionary_page else col.data_page_offset;length=col.total_compressed_size;assert 0<length<=4*1024**2
                assert receipt['new_network_bytes']+length<=MAX_BYTES and p.time.monotonic()<deadline
                url='https://huggingface.co/datasets/'+info['id']+'/resolve/'+info['sha']+'/'+field+'.parquet?download=true&scope_start='+str(offset)+'&scope_end='+str(offset+length-1)
                req=p.urllib.request.Request(url,headers={'Range':f'bytes={offset}-{offset+length-1}','User-Agent':'QuantResearch-scoped-native-history/1.0'})
                with p.urllib.request.urlopen(req,timeout=30,context=p.ssl.create_default_context()) as response:
                    assert response.status==206 and response.headers['Content-Range']==f'bytes {offset}-{offset+length-1}/{prev["published_bytes"]}'
                    chunk=response.read(length+1);assert len(chunk)==length
                path=DEST/(field+'_'+code+'_original_range.bin');assert not path.exists();path.write_bytes(chunk);guard=p.identity(path);digest=p.hashlib.sha256(chunk).hexdigest();receipt['new_network_bytes']+=length
                view=p.pq.ParquetFile(SparseView(prev['published_bytes'],segments+[(offset,chunk)]),pre_buffer=False,page_checksum_verification=True)
                table=view.read(columns=[code],use_threads=False);assert table.num_rows==len(stamps);rows=indices[code]
                selected=p.pa.table({'datetime':index.take(p.pa.array(rows,type=p.pa.int64())),'value':table.column(0).take(p.pa.array(rows,type=p.pa.int64()))})
                target=DEST/(field+'_'+code+'_actual_gap_dates.parquet');assert not target.exists();p.pq.write_table(selected,target,compression='zstd')
                columns.append(dict(code=code,url=url,range_start=offset,range_bytes=length,range_path=str(path),range_sha256=digest,range_verified_identity=guard,selected_path=str(target),selected_sha256=p.sha(target),selected_verified_identity=p.identity(target),selected_rows=selected.num_rows))
                receipt['results'].append(dict(field=field,code=code,published_filename=field+'.parquet',published_bytes=prev['published_bytes'],reused_cached_footer_and_header=pins,**{k:v for k,v in columns[-1].items() if k!='code'}));save();del chunk,view,table,selected
            del parser,segments;print(p.json.dumps(dict(field=field,network_bytes=receipt['new_network_bytes'],stockdays=len(requested))),flush=True)
        receipt.update(status='complete_late_four_stock_native_columns_no_admission',finished_utc=p.utc())
    except Exception as e:
        receipt.update(status='failed_terminal',error_type=type(e).__name__,error_detail=str(e),error_stack=p.traceback.format_exc())
    save();lock.close()

if __name__=='__main__':main()
