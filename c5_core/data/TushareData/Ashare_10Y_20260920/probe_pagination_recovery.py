import sys,json,gzip,datetime,hashlib
from pathlib import Path
from direct_client import DirectClient

sys.stdout.reconfigure(encoding='utf-8')
root=Path(__file__).resolve().parent
token=sys.stdin.readline().strip()
client=DirectClient(token,json.loads((root/'network_config.json').read_text(encoding='utf-8')),max_rps=None)
catalog=json.loads((root/'fundamental_catalog.json').read_text(encoding='utf-8'))
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
out=root/'control'/('pagination_probe_'+stamp);out.mkdir()
specs=[
 ('namechange',{'ts_code':'000505.SZ','limit':1,'offset':0}),
 ('namechange',{'ts_code':'000505.SZ','limit':1000,'offset':0}),
 ('namechange',{'ts_code':'000505.SZ','limit':1000,'offset':1000}),
 ('namechange',{'ts_code':'000505.SZ','start_date':'20140101','end_date':'20141231'}),
 ('fina_mainbz',{'ts_code':'000001.SZ','type':'P','start_date':'20110101','end_date':'20260918','limit':100,'offset':0}),
 ('fina_mainbz',{'ts_code':'000001.SZ','type':'P','start_date':'20110101','end_date':'20260918','limit':1000,'offset':0}),
 ('fina_mainbz',{'ts_code':'000001.SZ','type':'P','start_date':'20140101','end_date':'20141231','limit':1000,'offset':0}),
 ('fina_mainbz',{'ts_code':'000001.SZ','type':'P','start_date':'20240101','end_date':'20241231','limit':1000,'offset':0}),
 ('fina_mainbz',{'ts_code':'000001.SZ','type':'P','period':'20240630','limit':1000,'offset':0}),
 ('fina_mainbz',{'ts_code':'000333.SZ','type':'D','start_date':'20110101','end_date':'20260918','limit':1000,'offset':0}),
 ('fina_mainbz',{'ts_code':'000333.SZ','type':'D','start_date':'20180101','end_date':'20181231','limit':1000,'offset':0}),
 ('fina_mainbz',{'ts_code':'000333.SZ','type':'D','period':'20181231','limit':1000,'offset':0}),
]
results=[]
for i,(api,params) in enumerate(specs):
 fields=[f['field'] for f in catalog[api]['fields']]
 obj,meta=client.call(api,params,fields)
 (out/f'{i:02d}.json.gz').write_bytes(gzip.compress(json.dumps({'api':api,'params':params,'response':obj,'meta':meta},ensure_ascii=False).encode()))
 data=obj.get('data') or {}; names=data.get('fields') or []; rows=data.get('items') or []
 result={'i':i,'api':api,'params':params,'code':obj.get('code'),'message':obj.get('msg'),'rows':len(rows),'hash':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),'route':meta['route']}
 for col in ('end_date','start_date','ann_date','bz_code'):
  if col in names: result[col+'_values']=sorted({str(x[names.index(col)]) for x in rows if x[names.index(col)]})
 results.append(result); print(json.dumps(result,ensure_ascii=False),flush=True)
 if obj.get('code')!=0: break
(out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print('saved',str(out),flush=True)
