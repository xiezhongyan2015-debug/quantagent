"""Build an exact code/data delta; original P0 target remains pinned."""
import hashlib,json,zipfile
from pathlib import Path
P=Path(__file__).resolve().parent;R=P/'full_executor';M=R/'deployment_manifest.json';before=M.read_bytes();old=json.loads(before);new=json.loads(before);base='research/choice_pcf_automation/'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
extra=base+'daily_public_pcf/v5_rebalance_target_20261012.json'
for n in {extra}|set(old['files']):new['files'][n]=sha(R/n)
changed=sorted(n for n,h in new['files'].items() if old['files'].get(n)!=h);assert changed
new['adapter_revision']=old['adapter_revision']+1;new['workflow_scope']='one 100-share pilot at Oct12 09:30; after full fill and two verified audits, sealed V5 targets, formal fee-inclusive 50000 CNY order cap; previous P0 authorization and target preserved'
newbytes=json.dumps(new,ensure_ascii=False,indent=2).encode();delta=dict(base_manifest_sha256=hashlib.sha256(before).hexdigest(),files=changed,before={n:old['files'].get(n) for n in changed},after={n:new['files'][n] for n in changed},new_manifest_sha256=hashlib.sha256(newbytes).hexdigest());p=P/'executor_v5_delta.zip'
with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for n in changed:z.write(R/n,n)
 z.writestr('deployment_manifest.json',newbytes);z.writestr('delta.json',json.dumps(delta))
assert p.stat().st_size<=65536
out=P/'v5_workflow_release_20261012';(out/'before_manifest.json').write_bytes(before);(out/'new_manifest.json').write_bytes(newbytes)
receipt=dict(files=changed,bytes=p.stat().st_size,package_sha256=sha(p),manifest_sha256=delta['new_manifest_sha256']);(P/'v5_pending_delta_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
