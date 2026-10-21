"""Fetch only the three pinned public 1m files overlapping remaining gaps.

The published calendar is not treated as per-stock coverage. Hash each payload
while downloading, inspect only small Arrow batches, and retain complete source
and failure evidence. This source probe never qualifies or changes a panel.
"""
from collections import Counter
from . import bounded_tail_pipeline as p

SOURCE = p.BASE/'public_source_probes/new_public_history_and_tail_inventory_20261009/Lucifer744__KiT_data_demo'
DEST = SOURCE/'historical_minute_overlap_probe'
REMAINING = p.OUT/'historical_remaining_after_v14/remaining_hours.csv'


def main():
    lock = p.locked('probe_kit_historical_minute_overlap');DEST.mkdir(exist_ok=True);output = DEST/'receipt.json';assert not output.exists()
    info_path = SOURCE/'info.json';info = p.json.loads(info_path.read_text())
    coverage_path = SOURCE/'calendar_coverage_probe/receipt.json';coverage = p.json.loads(coverage_path.read_text())
    assert coverage['pinned_revision'] == info['sha'] and not coverage['credentials_used']
    assert all(r['status'] == 'complete_pinned_public_metadata' for r in coverage['records'])
    files = {r['rfilename'].split('/')[-1][:-8]:r for r in info['siblings'] if r['rfilename'].startswith('ashare/1m/')}
    remaining = p.pd.read_csv(REMAINING,dtype={'symbol':str,'date':str})
    wanted = sorted(set(remaining.symbol)&set(files));assert len(wanted) == 3
    total = sum(files[s]['size'] for s in wanted);assert total < 64*1024**2 and max(files[s]['size'] for s in wanted) < 32*1024**2
    result = dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='fetching_three_pinned_minute_sources',repository=info['id'],pinned_revision=info['sha'],
        published_info_sha256=p.sha(info_path),calendar_metadata_receipt_sha256=p.sha(coverage_path),remaining_scope_sha256=p.sha(REMAINING),
        symbols_requested=wanted,remaining_gap_hours_in_published_symbols=int(remaining.symbol.isin(wanted).sum()),expected_total_bytes=total,
        public_calendar_not_claimed_actual_per_stock_coverage=True,credentials_used=False,TLS_and_hostname_verification_enabled=True,
        candidate_hours_admitted=0,raw_panel_modified=False,source_clock_quantity_adjustment_or_independence_certified=False,records=[])
    p.atomic_json(output,result)
    for symbol in wanted:
        published = files[symbol];url = f'https://huggingface.co/datasets/{info["id"]}/resolve/{info["sha"]}/{published["rfilename"]}'
        path = DEST/(symbol+'.parquet');partial = path.with_suffix('.parquet.partial');assert not path.exists() and not partial.exists()
        record = dict(symbol=symbol,url=url,path=str(path),published_bytes=published['size'],published_sha256=published['lfs']['sha256'])
        try:
            count = 0;digest = p.hashlib.sha256()
            with p.urllib.request.urlopen(p.urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),context=p.ssl.create_default_context(),timeout=40) as response, partial.open('wb') as sink:
                record['http_status'] = response.status
                while chunk := response.read(128*1024):
                    count += len(chunk);assert count <= published['size'];digest.update(chunk);sink.write(chunk)
            assert count == published['size'] and digest.hexdigest() == published['lfs']['sha256'];partial.replace(path)
            identity = p.identity(path);pf = p.pq.ParquetFile(path)
            record.update(status='published_size_SHA_verified_during_download',bytes=count,actual_sha256=digest.hexdigest(),verified_identity=identity,
                columns=pf.schema_arrow.names,arrow_schema=str(pf.schema_arrow),schema_metadata={k.decode(errors='replace'):v.decode(errors='replace') for k,v in (pf.schema_arrow.metadata or {}).items()},rows=pf.metadata.num_rows,row_groups=pf.metadata.num_row_groups,
                first_rows=[],last_rows=[],maximum_batch_rows=0)
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,use_threads=False):
                record['maximum_batch_rows'] = max(record['maximum_batch_rows'],len(batch))
                if not record['first_rows']:record['first_rows'] = batch.slice(0,min(3,len(batch))).to_pylist()
                record['last_rows'] = batch.slice(max(0,len(batch)-3)).to_pylist()
            # Parquet timestamps may be Python datetime values; serialize explicitly.
            for k in ['first_rows','last_rows']:
                record[k] = [{f:v.isoformat() if hasattr(v,'isoformat') else v for f,v in row.items()} for row in record[k]]
            assert p.identity(path) == identity
        except Exception as e:
            record.update(status='failed_preserved',error_type=type(e).__name__,error=str(e),traceback=p.traceback.format_exc(),partial_path=str(partial),partial_bytes=partial.stat().st_size if partial.exists() else 0)
        result['records'].append(record);result.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
        if record.get('http_status') in [401,403,429]:break
    result.update(status='bounded_three_symbol_probe_complete_no_admission',finished_utc=p.utc(),successful_files=sum(r['status']=='published_size_SHA_verified_during_download' for r in result['records']))
    p.atomic_json(output,result);lock.close()


if __name__ == '__main__':main()
