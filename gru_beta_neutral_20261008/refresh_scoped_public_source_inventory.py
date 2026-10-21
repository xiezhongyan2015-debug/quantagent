"""Refresh public metadata only; prohibit data transfer outside actual missing scope."""
import json,hashlib,ssl,urllib.request,traceback,re
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/gru_beta_neutral_20261008/public_source_probes/scoped_metadata_refresh_20261010'
REPOS=['suncong/Ashare-events','suncong/ashare_1min','neigezhu/china-a-share-1min-ohlcv']
def probe(repo):
    row={'repository':repo,'quote_payload_bytes':0}
    try:
        url='https://huggingface.co/api/datasets/'+repo+'?blobs=true'
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'QuantResearch-public-data-audit/1.0'}),timeout=25,context=ssl.create_default_context()) as response:
            assert response.status==200
            raw=response.read(4*1024*1024+1);assert len(raw)<=4*1024*1024
        path=OUT/(repo.replace('/','__')+'_metadata.json');path.write_bytes(raw)
        d=json.loads(raw);row.update(status='complete_public_metadata_only',revision=d['sha'],gated=d['gated'],private=d['private'],lastModified=d.get('lastModified'),metadata_path=str(path),metadata_sha256=hashlib.sha256(raw).hexdigest(),metadata_bytes=len(raw),files=len(d['siblings']))
        dates=set(json.loads((ROOT/'gru_research/artifacts/minute_calendar.json').read_text())['dates'])
        named_dates=[]
        for f in d['siblings']:
            match=re.search(r'(20\d{2})[-_]?(\d{2})[-_]?(\d{2})',f['rfilename'])
            if match:
                day='-'.join(match.groups());named_dates.append({'path':f['rfilename'],'date':day,'within_actual_calendar':day in dates,'published_bytes':f.get('size')})
        row['date_named_files']=named_dates
        row['scope_first_date']=min(dates);row['scope_last_date']=max(dates)
    except Exception as e:row.update(status='terminal_metadata_probe_failed',error_type=type(e).__name__,error_message=str(e),traceback=traceback.format_exc())
    return row

def main():
    OUT.mkdir(exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool: records=list(pool.map(probe,REPOS))
    receipt={'checked_utc':datetime.now(timezone.utc).isoformat(),'source_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'records':records,'quote_payload_bytes':0,'candidate_hours_admitted':0,'candidate_changed':False,'production_ready':False,'personal_credentials_used':False,'TLS_and_hostname_verification_enabled':True,'data_transfer_requires_date_and_actual_gap_intersection':True}
    (OUT/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(receipt,ensure_ascii=False))
if __name__=='__main__':main()
