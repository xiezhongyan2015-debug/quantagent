"""Plan all unexamined original historical gaps from an already retained index."""
from collections import Counter
from . import bounded_tail_pipeline as p
from .acquire_remaining_2019_native_columns_bounded import SparseView, SOURCE, FIELDS

DEST=SOURCE/'remaining_historical_native_matrix_full_scope_bounded'
RAW=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_raw'

def main():
    DEST.mkdir(exist_ok=True);op=DEST/'scope.json';assert not op.exists()
    oldp=SOURCE/'native_matrix_pilot/bounded_column_acquisition.json';old=p.json.loads(oldp.read_text())
    close=next(r for r in old['results'] if r['field']=='close');indexpath=p.Path(close['selection_path'])
    assert p.sha(indexpath)==close['selection_sha256']
    stamps=p.pq.ParquetFile(indexpath).read(columns=['__index_level_0__'],use_threads=False).column(0).to_pylist()
    assert len(stamps)==478800 and stamps==sorted(stamps)
    dates={s.strftime('%Y-%m-%d') for s in stamps}
    previous=[SOURCE/'native_matrix_pilot/actual_minimum_gap_pilot_with_verified_alias_diagnostics.json']
    previous.extend(SOURCE/name/'independent_daily_diagnostic.json' for name in ['remaining_native_matrix_two_stock_bounded','late_remaining_native_matrix_four_stock_bounded','remaining_2019_native_matrix_four_stock_bounded'])
    examined=set();prior_pins=[]
    for path in previous:
        j=p.json.loads(path.read_text());prior_pins.append(dict(path=str(path),sha256=p.sha(path)))
        examined.update((r['symbol'],r['date']) for r in j.get('records',j.get('results',[])))
    cp=p.ROOT/'gru_research/artifacts/minute_calendar.json';cal=p.json.loads(cp.read_text())
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    sp=p.BASE/'public_source_probes/additional_public_archives_20261009/wind17/all_minimum_gap_consolidation/verified_minimum_gap_source_scope.json'
    scope=p.json.loads(sp.read_text());assert scope['passed']
    requested=[];outside=[]
    for r in scope['records']:
        if (r['symbol'],r['date']) in examined:continue
        assert r['date'] in cal['dates'] and r['symbol'] in cal['union_symbols']
        day=cal['dates'].index(r['date']);col=cal['union_symbols'].index(r['symbol'])
        slots=[s for s in r['actual_missing_slots'] if not mask[day*4+s,col]]
        if not slots:continue
        if r['date'] not in dates:outside.append(dict(symbol=r['symbol'],date=r['date'],slots=slots));continue
        requested.append(dict(symbol=r['symbol'],date=r['date'],slots=slots))
    columns={};available=None
    for field in FIELDS:
        prev=next(r for r in old['results'] if r['field']==field);segments=[]
        footer=prev.get('reused_retained_footer') or next(r for r in prev['requests'] if r['path'].endswith('pilot_footer.bin'))
        header=next(r for r in prev['requests'] if r['start']==0 and r['bytes']==4)
        for q in [footer,header]:
            raw=p.Path(q['path']).read_bytes();assert p.hashlib.sha256(raw).hexdigest()==q['retained_range_sha256'];segments.append((q['start'],raw))
        parser=p.pq.ParquetFile(SparseView(prev['published_bytes'],segments),pre_buffer=False)
        assert parser.metadata.num_row_groups==1 and parser.metadata.num_rows==len(stamps)
        names=set(parser.schema_arrow.names);available=names if available is None else available & names
        for code in {r['symbol'][:6] for r in requested}:
            if code not in names:continue
            c=parser.metadata.row_group(0).column(parser.schema_arrow.names.index(code))
            start=c.dictionary_page_offset if c.has_dictionary_page else c.data_page_offset
            assert 0<c.total_compressed_size<=8*1024**2
            columns[code,field]=dict(start=start,bytes=c.total_compressed_size)
        del parser,segments
    absent=[r for r in requested if r['symbol'][:6] not in available]
    requested=[r for r in requested if r['symbol'][:6] in available]
    bycode={c:[r for r in requested if r['symbol'][:6]==c] for c in {r['symbol'][:6] for r in requested}}
    order=sorted(bycode,key=lambda c:(-sum(len(r['slots']) for r in bycode[c]),min(r['date'] for r in bycode[c]),c))
    stocks=[dict(code=c,stockdays=len(bycode[c]),actual_missing_hours=sum(len(r['slots']) for r in bycode[c]),column_bytes=sum(columns[c,f]['bytes'] for f in FIELDS),requested_stockdays=bycode[c],fields={f:columns[c,f] for f in FIELDS}) for c in order]
    p.atomic_json(op,dict(passed=True,created_utc=p.utc(),source_code_sha256=p.sha(__file__),current_raw_meta_sha256=p.sha(RAW/'dataset/meta.json'),current_raw_mask_identity=p.identity(RAW/'dataset/hour_complete.npy'),calendar_sha256=p.sha(cp),original_scope_sha256=p.sha(sp),cached_source_index_sha256=close['selection_sha256'],prior_native_diagnostic_pins=prior_pins,stocks=stocks,total_stockdays=len(requested),total_actual_missing_hours=sum(len(r['slots']) for r in requested),total_original_column_bytes=sum(r['column_bytes'] for r in stocks),stockdays_by_year=dict(Counter(r['date'][:4] for r in requested)),outside_native_timestamp_scope=outside,missing_native_source_columns=absent,network_requests=0,candidate_hours_admitted=0,source_date_code_and_missing_mask_intersection_verified_before_payload_requests=True))
    print(p.json.dumps(dict(stocks=len(stocks),stockdays=len(requested),hours=sum(len(r['slots']) for r in requested),column_bytes=sum(r['column_bytes'] for r in stocks),first8=[r['code'] for r in stocks[:8]])))

if __name__=='__main__':main()
