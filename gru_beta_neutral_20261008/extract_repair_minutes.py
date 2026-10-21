"""Copy exact original source payloads required for the 2019 repair audit."""
import gzip
import hashlib
import io
import json
import sys
import tarfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'gru_research/.deps'))
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/gru_beta_neutral_20261008'


class SplitSource(io.RawIOBase):
    def __init__(self, paths):
        self.paths = paths
        self.index = 0
        self.handle = paths[0].open('rb')
        self.bytes_read = 0
    def readable(self): return True
    def readinto(self, buffer):
        n = self.handle.readinto(buffer)
        if not n and self.index+1 < len(self.paths):
            self.handle.close();self.index += 1;self.handle = self.paths[self.index].open('rb')
            n = self.handle.readinto(buffer)
        self.bytes_read += n
        return n
    def close(self):
        self.handle.close();super().close()


def main():
    bad = pq.read_table(ROOT/'gru_research/artifacts/minute_invalid_details.parquet').to_pylist()
    targets = {(r['date'],r['symbol']) for r in bad if r['cause']=='nonpositive_raw_price'}
    march = pq.read_table(ROOT/'gru_research/artifacts/minute_day_audit/2019-03-29.parquet').to_pylist()
    targets |= {(r['date'],r['symbol']) for r in march}
    targets |= {(d,s) for d in ['2019-06-05','2019-06-06','2019-06-10']
                for s in ['600754.SH','300244.SZ','000656.SZ']}
    destination = OUT/'minute_original_selected'
    parts = sorted(ROOT.glob('A股分钟k线数据.tar.gz.*'))
    assert len(parts)==5
    source = SplitSource(parts)
    start = time.monotonic();found={};members=0;written=0
    progress_path = OUT/'minute_repair_extraction_progress.json'
    def progress(status):
        p={'status':status,'requested_stockdays':len(targets),'copied_stockdays':len(found),
           'archive_members_seen':members,'compressed_bytes_read':source.bytes_read,
           'written_bytes':written,'elapsed_seconds':time.monotonic()-start}
        progress_path.write_text(json.dumps(p,indent=2))
        print(json.dumps(p),flush=True)
    with io.BufferedReader(source,buffer_size=1024*1024) as raw,gzip.GzipFile(fileobj=raw) as zipped:
        with tarfile.open(fileobj=zipped,mode='r|') as archive:
            for member in archive:
                members += 1
                if members%50000==0: progress('copying')
                pieces=member.name.split('/')
                if not member.isfile() or len(pieces)!=3 or not pieces[-1].endswith('.parquet'): continue
                day=pieces[1];day=f'{day[:4]}-{day[4:6]}-{day[6:]}'
                code=pieces[-1][:-8];symbol=code+('.SH' if code.startswith(('6','9')) else '.SZ')
                if (day,symbol) not in targets:continue
                if member.size>1024*1024:raise ValueError('Selected payload exceeded 1 MiB cap')
                payload=archive.extractfile(member).read()
                dest=destination/day/(symbol+'.parquet');dest.parent.mkdir(parents=True,exist_ok=True)
                if dest.exists():assert dest.read_bytes()==payload
                else:dest.write_bytes(payload)
                written += len(payload)
                if written>256*1024*1024:raise ValueError('Selected payload cap exceeded')
                found[(day,symbol)]={'date':day,'symbol':symbol,'member':member.name,'path':str(dest),
                                     'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
                if len(found)==len(targets):break
    receipt={'status':'complete' if len(found)==len(targets) else 'partial',
             'source_parts':[{'path':str(p),'bytes':p.stat().st_size} for p in parts],
             'payloads':list(found.values()),'missing':[list(k) for k in sorted(targets-set(found))],
             'original_sources_modified':False,'decoded_or_repaired':False}
    (OUT/'minute_repair_extraction_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    progress(receipt['status'])


if __name__=='__main__':main()
