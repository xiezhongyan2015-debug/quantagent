"""Archive issuer rename evidence and independently query two public quote sources."""
import hashlib
import json
from pathlib import Path
import requests
from verify_readonly import ROOT
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import now_cn
from watch_engine import save

DEST=ROOT/'research/choice_pcf_automation/identity_300223_20260921'
NOTICE='https://static.cninfo.com.cn/finalpage/2026-08-10/1225466827.PDF'

def main():
    DEST.mkdir(exist_ok=True)
    r=requests.get(NOTICE,timeout=25)
    downloaded=r.status_code==200 and r.content.startswith(b'%PDF')
    if downloaded:(DEST/'2026-052_证券简称变更公告.pdf').write_bytes(r.content)
    q=quote('SZ300223')
    tx=requests.get('https://qt.gtimg.cn/q=sz300223',timeout=15);tx.raise_for_status();tx.encoding='gbk'
    body=tx.text;fields=body.split('="',1)[1].split('"',1)[0].split('~')
    independent=dict(source='https://qt.gtimg.cn/q=sz300223',name=fields[1],code=fields[2],last=float(fields[3]),previous_close=float(fields[4]),timestamp=fields[30])
    (DEST/'tencent_raw.txt').write_text(body,encoding='utf-8')
    result=dict(checked_at=now_cn().isoformat(),security='SZ300223',issuer_notice=NOTICE,
                notice_sha256=hashlib.sha256(r.content).hexdigest() if downloaded else None,
                notice_local_download_http_status=r.status_code,notice_verified_via='Official CNINFO PDF opened and read through web tool, pages 1-2; local download may be denied',
                old_name='北京君正',current_name='君正股份',effective_date='2026-08-13',code_unchanged=True,
                choice=q,independent_quote=independent,
                identity_verified=q['code']=='SZ300223' and independent['code']=='300223' and q['name']==independent['name']=='君正股份',
                prices_same_previous_close=abs(q['previous_close']-independent['previous_close'])<.001,
                correction='Earlier June disclosure was outdated; Choice current name was correct. No quote price or timestamp was rewritten.')
    save(DEST/'verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
