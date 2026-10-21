"""Reviewed Sunday-only code deployment. Never start, stop, or authorize trading."""
import datetime as dt,hashlib,json,os,shutil,sys,zipfile
from pathlib import Path
ROOT=Path('C:/ChoiceBridge/executor');PACKAGE=Path('C:/ChoiceBridge/staging/executor_workflow_delta.zip');BASE='research/choice_pcf_automation/';NOW=dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
assert NOW.date().isoformat()=='2026-10-11' and NOW.hour<24,'Deployment requires the nontrading preparation day'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(PACKAGE)==sys.argv[1]
protected=[ROOT/(BASE+'cloud_ledger/private_state.json')]
for p in (ROOT/(BASE+'console/runs')).glob('*/*/authorization.json'):
 protected.extend(p.parent/name for name in ('authorization.json','config.json','seed.json','source_seal.json'))
 c=json.loads((p.parent/'config.json').read_text(encoding='utf8'));s=json.loads((p.parent/'status_redacted.json').read_text(encoding='utf8'))
 assert c['strategy']=='pilot_one_lot' and s['status']=='scheduled_waiting' and not (p.parent/'private_state.json').exists() and not (p.parent/'STOP').exists()
 assert not json.loads((p.parent/'seed.json').read_text(encoding='utf8'))['attempts']
seals={str(p):sha(p) for p in protected}
extras={BASE+'workflow_rebalance.py',BASE+'test_workflow_rebalance.py',BASE+'daily_public_pcf/active_rebalance_target.json',BASE+'daily_public_pcf/current_membership_union.json'}
with zipfile.ZipFile(PACKAGE) as z:
 delta=json.loads(z.read('delta.json'));old=ROOT/'deployment_manifest.json';assert sha(old)==delta['base_manifest_sha256'];before=json.loads(old.read_text(encoding='utf8'));new=json.loads(z.read('deployment_manifest.json'))
 assert set(new['files'])==set(before['files'])|extras
 assert len(z.namelist())==len(set(z.namelist())) and set(z.namelist())==set(delta['files'])|{'deployment_manifest.json','delta.json'}
 for name,h in before['files'].items():assert sha(ROOT/name)==h,name
 for name in delta['files']:
  path=(ROOT/name).resolve();assert path.is_relative_to(ROOT.resolve())
  assert name in extras or (name in before['files'] and name.endswith('.py'))
  if name in before['files']:assert sha(path)==delta['before'][name]
  else:assert not path.exists() and delta['before'][name] is None
  assert hashlib.sha256(z.read(name)).hexdigest()==delta['after'][name]==new['files'][name]
 for name in set(before['files'])-set(delta['files']):assert new['files'][name]==before['files'][name]
 assert hashlib.sha256(z.read('deployment_manifest.json')).hexdigest()==delta['new_manifest_sha256']
 backup=Path('C:/ChoiceBridge/staging/workflow_before_'+NOW.strftime('%H%M%S'));backup.mkdir()
 existed=[];applied=[]
 try:
  for name in delta['files']+['deployment_manifest.json']:
   path=ROOT/name
   if path.exists():
    b=backup/name;b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,b);existed.append(name)
   temp=path.with_suffix(path.suffix+'.reviewed.tmp');temp.write_bytes(z.read(name));os.replace(temp,path);applied.append(name)
  for name,h in new['files'].items():assert sha(ROOT/name)==h
  assert all(sha(Path(p))==h for p,h in seals.items())
 except BaseException:
  for name in reversed(applied):
   if name in existed:shutil.copy2(backup/name,ROOT/name)
   else:(ROOT/name).unlink(missing_ok=True)
  raise
print(json.dumps(dict(changed_files=len(delta['files']),manifest_sha256=sha(ROOT/'deployment_manifest.json'),protected_files_unchanged=len(seals),orders_submitted=0,cancellations_submitted=0,full_workflow_armed=False)))
