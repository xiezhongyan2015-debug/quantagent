"""Decode existing archived CSVs with the OS archive reader, without installers."""
import csv
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gru_research/.deps'))
import pyarrow as pa
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/gru_beta_neutral_20261008'
DATES={'2019-03-29','2019-06-05','2019-06-06','2019-06-10'}


def main():
    original=pq.read_table(ROOT/'gru_research/artifacts/minute_invalid_details.parquet').to_pylist()
    dates=DATES|{r['date'] for r in original}
    symbols={r['symbol'] for r in original}
    symbols|={r['symbol'] for r in pq.read_table(ROOT/'gru_research/artifacts/minute_day_audit/2019-03-29.parquet').to_pylist()}
    symbols|={'000656.SZ','600754.SH','300244.SZ'}
    codes={s[:6]:s for s in symbols}
    ref=json.loads((ROOT/'2000-2025年a股60min/ARCHIVED_TO_ICLOUD.json').read_text())
    archive=Path(ref['archive']);digest=hashlib.sha256()
    with archive.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):digest.update(block)
    assert digest.hexdigest()==ref['archive_sha256']
    destination=OUT/'legacy_repair_sources';destination.mkdir(exist_ok=True)
    payloads=[]
    with tarfile.open(archive,'r|gz') as arc:
        for m in arc:
            if not m.isfile():continue
            filename=Path(m.name).name;code=filename[2:8]
            if code not in codes or not filename.endswith('.csv.7z'):continue
            p=destination/filename;contents=arc.extractfile(m).read();p.write_bytes(contents)
            payloads.append((p,codes[code],hashlib.sha256(contents).hexdigest()))
    def decode(item):
        p,symbol,digest=item
        result=subprocess.run(['/usr/bin/tar','-xOf',str(p),p.name.removesuffix('.7z')],capture_output=True,check=True,timeout=20)
        if len(result.stdout)>12*1024*1024:raise ValueError('Decoded stock CSV exceeded bound')
        try:text=result.stdout.decode('utf-8-sig')
        except UnicodeDecodeError:text=result.stdout.decode('gb18030')
        rows=[]
        for row in csv.DictReader(io.StringIO(text)):
            if row['日期'] not in dates:continue
            rows.append({'symbol':symbol,'date':row['日期'],'legacy_time':row['时间'],
                         **{k:float(row[v]) for k,v in [('open','开盘'),('high','最高'),('low','最低'),('close','收盘'),('volume','成交量'),('amount','成交额')]}})
        return rows,{'symbol':symbol,'archive_member_csv7z_sha256':digest,'path':str(p),
                     'decoded_csv_sha256':hashlib.sha256(result.stdout).hexdigest(),'selected_rows':len(rows)}
    rows=[];sources=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for selected,receipt in pool.map(decode,payloads):rows.extend(selected);sources.append(receipt)
    dest=OUT/'legacy_2019_selected_bars.parquet'
    pq.write_table(pa.Table.from_pylist(rows),dest,compression='zstd')
    receipt={'status':'decoded_existing_legacy_rows_pending_grid_and_price_validation','archive':str(archive),
             'archive_sha256':ref['archive_sha256'],'archive_checksum_verified':True,
             'requested_symbols':len(symbols),'found_symbols':len(payloads),'selected_rows':len(rows),
             'rows_path':str(dest),'rows_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),
             'sources':sources,'source_prices_repaired':False}
    (OUT/'legacy_2019_repair_source_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k!='sources'},ensure_ascii=False))


if __name__=='__main__':main()
