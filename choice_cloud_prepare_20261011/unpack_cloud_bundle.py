"""Validate and extract the bounded executor package; never execute its contents."""
import hashlib,json,zipfile,io
from pathlib import Path,PurePosixPath
BASE=Path('C:/ChoiceBridge');parts=[BASE/'staging'/('executor_cloud_pilot.part'+str(i)) for i in range(3)]
data=b''.join(p.read_bytes() for p in parts)
EXPECTED='8f4359d76469e0deebee85112f41054eb0462dc5b0324ed215a3fc9c93e97a80'
if hashlib.sha256(data).hexdigest()!=EXPECTED:raise ValueError('Package checksum mismatch')
DEST=BASE/'executor'
if DEST.exists():raise ValueError('Existing executor is not overwritten')
with zipfile.ZipFile(io.BytesIO(data)) as z:
 if sum(x.file_size for x in z.infolist())>3000000:raise ValueError('Archive size limit')
 names=z.namelist()
 if len(names)!=len(set(names)):raise ValueError('Duplicate archive path')
 manifest=json.loads(z.read('deployment_manifest.json'))
 if set(names)!=set(manifest['files'])|{'deployment_manifest.json'}:raise ValueError('Unexpected package file')
 for n in names:
  q=PurePosixPath(n)
  if q.is_absolute() or '..' in q.parts or '\\' in n:raise ValueError('Unsafe archive path')
  b=z.read(n)
  if n!='deployment_manifest.json' and hashlib.sha256(b).hexdigest()!=manifest['files'][n]:raise ValueError('Source manifest mismatch')
 DEST.mkdir()
 for n in names:
  p=DEST/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(z.read(n))
receipt=dict(package_sha256=EXPECTED,files_checked=len(manifest['files']),destination=str(DEST),manifest_verified=True,orders_submitted=0)
(BASE/'staging/executor_deployment_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
