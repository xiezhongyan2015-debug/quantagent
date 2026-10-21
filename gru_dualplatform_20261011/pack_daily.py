"""Verified panel archive split below the ECS console's 500 MB upload limit."""
from pathlib import Path
import gzip,hashlib,json,tarfile
from .audit_daily import audit,P

class Parts:
    def __init__(self,out):
        self.out=out;self.out.mkdir(parents=True,exist_ok=True)
        self.parts=[];self.file=None;self.n=0;self.h=hashlib.sha256();self.all=hashlib.sha256()
    def new(self):
        if self.file:self.closepart()
        name=f'daily-panel.part{len(self.parts):03d}'
        assert not (self.out/name).exists(), 'Existing archive parts: inspect before retry'
        self.file=(self.out/name).open('wb');self.n=0;self.h=hashlib.sha256()
    def closepart(self):
        self.parts.append({'name':Path(self.file.name).name,'size':self.n,'sha256':self.h.hexdigest()})
        self.file.close();self.file=None
    def write(self,b):
        self.all.update(b);count=len(b)
        while b:
            if self.file is None:self.new()
            k=min(len(b),400_000_000-self.n);chunk=b[:k]
            self.file.write(chunk);self.h.update(chunk);self.n+=k;b=b[k:]
            if self.n==400_000_000:self.closepart()
        return count
    def flush(self):
        if self.file:self.file.flush()
    def close(self):
        if self.file:self.closepart()

def pack():
    audit();meta=json.loads((P/'meta.json').read_text())
    out=P.parent/'upload';writer=Parts(out)
    with gzip.GzipFile(fileobj=writer,mode='wb',compresslevel=1,mtime=0) as z:
        with tarfile.open(fileobj=z,mode='w|') as tar:
            for name in list(meta['files'])+['meta.json']:
                tar.add(P/name,arcname='outputs/gru_dualplatform_20261011/daily_panel/'+name,recursive=False)
    writer.close()
    receipt={'archive_sha256':writer.all.hexdigest(),'parts':writer.parts,'data_fingerprint':meta['data_fingerprint']}
    (out/'upload_manifest.json').write_text(json.dumps(receipt,indent=2))
    print('PACKED',len(writer.parts),sum(x['size'] for x in writer.parts),flush=True)

if __name__=='__main__':pack()
