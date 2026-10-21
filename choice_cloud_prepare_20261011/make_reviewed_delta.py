"""Build a bounded reviewed delta from the currently deployed local manifest."""
from pathlib import Path
import json,zipfile,hashlib
prep=Path(__file__).resolve().parent;root=prep/'full_executor';manifest=root/'deployment_manifest.json';original=manifest.read_bytes();old=json.loads(original);new=json.loads(original);changed=[]
for name,sha in old['files'].items():
 actual=hashlib.sha256((root/name).read_bytes()).hexdigest()
 if actual!=sha:changed.append(name);new['files'][name]=actual
assert changed,'No changes to deploy'
new['adapter_revision']=old.get('adapter_revision',1)+1
manifest.write_text(json.dumps(new,ensure_ascii=False,indent=2))
delta={'base_manifest_sha256':hashlib.sha256(original).hexdigest(),'files':changed,'before':{n:old['files'][n] for n in changed},'after':{n:new['files'][n] for n in changed},'new_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
p=prep/'executor_reviewed_delta.zip'
with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for name in changed+['deployment_manifest.json']:z.write(root/name,name)
 z.writestr('delta.json',json.dumps(delta))
assert p.stat().st_size<=65536
receipt=dict(changed_files=changed,bytes=p.stat().st_size,package_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),manifest_sha256=delta['new_manifest_sha256'])
(prep/'pending_delta_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
