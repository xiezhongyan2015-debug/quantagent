"""Apply reviewed GUI/integrity changes only; preserve every live ledger file."""
import hashlib,json,zipfile,os,sys
from pathlib import Path
ROOT=Path('C:/ChoiceBridge/executor');PACKAGE=Path('C:/ChoiceBridge/staging/executor_reviewed_delta.zip')
assert hashlib.sha256(PACKAGE.read_bytes()).hexdigest()==sys.argv[1]
with zipfile.ZipFile(PACKAGE) as z:
 delta=json.loads(z.read('delta.json'));old=ROOT/'deployment_manifest.json'
 assert hashlib.sha256(old.read_bytes()).hexdigest()==delta['base_manifest_sha256']
 before=json.loads(old.read_text())
 for name,sha in before['files'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha
 assert set(z.namelist())==set(delta['files'])|{'deployment_manifest.json','delta.json'}
 for name in delta['files']:
  path=(ROOT/name).resolve();assert path.is_relative_to(ROOT.resolve()) and (name.endswith('.py') or name=='research/choice_pcf_automation/daily_public_pcf/csi500_members.json')
  assert hashlib.sha256(path.read_bytes()).hexdigest()==delta['before'][name]
  assert hashlib.sha256(z.read(name)).hexdigest()==delta['after'][name]
 assert hashlib.sha256(z.read('deployment_manifest.json')).hexdigest()==delta['new_manifest_sha256']
 for name in delta['files']+['deployment_manifest.json']:
  path=ROOT/name;temp=path.with_suffix(path.suffix+'.reviewed.tmp');temp.write_bytes(z.read(name));os.replace(temp,path)
print(json.dumps(dict(changed_files=len(delta['files']),manifest_sha256=delta['new_manifest_sha256'],ledger_unchanged=True,orders_submitted=0)))
