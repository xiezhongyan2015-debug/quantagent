"""Download only pinned runtime wheels; verify official PyPI hashes before publication."""
import concurrent.futures,hashlib,json,math,re,subprocess,time
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path('C:/ChoiceBridge/runtime')
manifest=json.loads((ROOT/'mirror-wheel-manifest.json').read_text(encoding='utf-8-sig'))
allowed={'requests','psutil','pandas','pyarrow','cryptography','pywin32','charset-normalizer','idna','urllib3','certifi','numpy','python-dateutil','tzdata','cffi','pycparser','six'}
assert len(manifest)==16 and {x['package'] for x in manifest}==allowed
stage=ROOT/'download-parts';stage.mkdir(exist_ok=True)
wheelhouse=ROOT/'wheelhouse';wheelhouse.mkdir(exist_ok=True)
CHUNK=2*1024*1024
jobs=[]
for item in manifest:
    assert urlparse(item['mirror_url']).scheme=='https' and urlparse(item['mirror_url']).hostname=='mirrors.aliyun.com'
    assert re.fullmatch(r'[A-Za-z0-9_.-]+\.whl',item['filename'])
    assert re.fullmatch(r'[0-9a-f]{64}',item['sha256']) and 0<item['size']<50000000
    for idx,start in enumerate(range(0,item['size'],CHUNK)):
        jobs.append((item,idx,start,min(item['size']-1,start+CHUNK-1)))
def fetch(job):
    item,idx,start,end=job;part=stage/(item['filename']+'.part'+str(idx));wanted=end-start+1
    if part.exists() and part.stat().st_size==wanted:return part
    for attempt in range(4):
        have=part.stat().st_size if part.exists() else 0
        if have>wanted:raise ValueError('Part oversized')
        if have==wanted:return part
        piece=part.with_suffix(part.suffix+'.incoming');headers=part.with_suffix(part.suffix+'.headers')
        args=['curl.exe','--silent','--show-error','--fail','--location','--proto','=https','--proto-redir','=https','--connect-timeout','10','--max-time','90','--range',str(start+have)+'-'+str(end),'--dump-header',str(headers),'--output',str(piece),item['mirror_url']]
        result=subprocess.run(args,capture_output=True,timeout=100)
        h=headers.read_text(errors='replace') if headers.exists() else ''
        ranges=re.findall(r'(?im)^content-range:\s*bytes\s+(\d+)-(\d+)/(\d+)',h)
        expected=(start+have,end,item['size'])
        if ranges and tuple(map(int,ranges[-1]))==expected and piece.exists():
            size=piece.stat().st_size
            if not 0<=size<=wanted-have:raise ValueError('Download range oversized')
            with part.open('ab') as output,piece.open('rb') as source:
                while data:=source.read(65536):output.write(data)
            if part.stat().st_size==wanted:
                print('RANGE_OK',item['package'],idx,flush=True);return part
        if attempt==3:raise RuntimeError('Pinned wheel range download failed: '+item['package'])
    raise RuntimeError('Download incomplete')
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(fetch,jobs))
for item in manifest:
    assembled=stage/(item['filename']+'.assembled')
    with assembled.open('wb') as output:
        for idx in range(math.ceil(item['size']/CHUNK)):
            with (stage/(item['filename']+'.part'+str(idx))).open('rb') as source:
                while data:=source.read(65536):output.write(data)
    digest=hashlib.sha256(assembled.read_bytes()).hexdigest()
    if assembled.stat().st_size!=item['size'] or digest!=item['sha256']:raise RuntimeError('Official PyPI wheel hash mismatch: '+item['package'])
    assembled.replace(wheelhouse/item['filename'])
    print('OFFICIAL_HASH_OK',item['package'],flush=True)
print('ALL_16_WHEELS_HASH_VERIFIED',flush=True)
