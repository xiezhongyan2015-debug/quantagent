import sys,json,sqlite3,datetime
from pathlib import Path
from direct_client import DirectClient
from unpaged_recovery import recover

sys.stdout.reconfigure(encoding='utf-8')
r=Path(__file__).resolve().parent
token=sys.stdin.readline().strip()
client=DirectClient(token,json.loads((r/'network_config.json').read_text(encoding='utf-8')),max_rps=None)
c=sqlite3.connect(r/'control'/'manifest.sqlite',timeout=30)
result=[]
for api in ('fina_mainbz','namechange'):
    jobs=c.execute("select id,params,fields from jobs indexed by job_state where status='needs_review' and api=? limit 3",(api,)).fetchall()
    for key,params,fields in jobs:
        params,fields=json.loads(params),json.loads(fields)
        def request(p):
            obj,meta=client.call(api,{**p,'limit':1000,'offset':0},fields)
            if obj.get('code')!=0: raise RuntimeError('Live recovery API error: '+str(obj.get('msg')))
            return obj
        rows,artifacts,info=recover(api,params,fields,request,r/'dataset'/api/key[:2]/key,key)
        item={'api':api,'params':params,'rows':len(rows),'mode':info['mode'],'intervals':len(info['leaves']),'artifacts':len(artifacts)}
        result.append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
(r/'control'/'recovery_live_verification.json').write_text(json.dumps({'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'results':result},ensure_ascii=False,indent=2),encoding='utf-8')
