"""Apply three reviewed Python files only while all execution workers are stopped."""
import sys,json,hashlib,zipfile,shutil,os,subprocess
from pathlib import Path
ROOT=Path('C:/ChoiceBridge/executor');BASE=ROOT/'research/choice_pcf_automation'
sys.path.insert(0,str(BASE))
from console_runtime import active_session,mutex,APP_DIR
from cloud_account import verify_deployment
from quanta_agents.factor_lab_a.pcf_automation import now_cn
PACKAGE=Path('C:/ChoiceBridge/staging/mark_snapshot_fix.zip')
FILES={'research/choice_pcf_automation/console_runtime.py', 'research/choice_pcf_automation/test_workflow_rebalance.py', 'research/choice_pcf_automation/workflow_rebalance.py'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
with mutex(APP_DIR/'launch.lock'),mutex(APP_DIR/'account_execution.lock'):
 assert now_cn().date().isoformat()=='2026-10-12'
 assert not active_session(),'Worker is active; no hot update'
 verify_deployment();assert sha(PACKAGE)==sys.argv[1]
 protected=[p for p in BASE.rglob('*.json') if any(x in p.parts for x in ('runs','cloud_ledger','cloud_ledger_dividend_20261012'))]
 seals={str(p):sha(p) for p in protected}
 old=json.loads((ROOT/'deployment_manifest.json').read_text(encoding='utf8'));assert old['adapter_revision']==12
 with zipfile.ZipFile(PACKAGE) as z:
  assert set(z.namelist())==FILES|{'deployment_manifest.json'}
  new=json.loads(z.read('deployment_manifest.json'))
  assert set(old['files'])==set(new['files'])
  assert all(old['files'][n]==new['files'][n] for n in old['files'] if n not in FILES)
  for n in FILES:assert hashlib.sha256(z.read(n)).hexdigest()==new['files'][n]
  backup=Path('C:/ChoiceBridge/staging/mark_snapshot_before_'+now_cn().strftime('%H%M%S'));backup.mkdir()
  changed=[]
  try:
   for n in sorted(FILES)+['deployment_manifest.json']:
    dst=ROOT/n;b=backup/n;b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dst,b)
    tmp=dst.with_suffix('.reviewed.tmp');tmp.write_bytes(z.read(n));os.replace(tmp,dst);changed.append(n)
   verify_deployment()
   code="import sys,unittest;sys.path.insert(0,r'C:/ChoiceBridge/executor/research/choice_pcf_automation');s=unittest.defaultTestLoader.loadTestsFromNames(['test_cloud_pilot','test_workflow_rebalance']);r=unittest.TextTestRunner(verbosity=1).run(s);assert r.testsRun==107 and r.wasSuccessful()"
   result=subprocess.run([sys.executable,'-X','utf8','-c',code],capture_output=True,text=True,timeout=30)
   assert result.returncode==0,result.stderr
   assert not active_session() and all(sha(Path(p))==h for p,h in seals.items())
  except BaseException:
   for n in reversed(changed):shutil.copy2(backup/n,ROOT/n)
   raise
 print(json.dumps(dict(at=now_cn().isoformat(),changed_files=3,manifest_sha256=sha(ROOT/'deployment_manifest.json'),offline_tests_passed=107,all_run_files_unchanged=True,backup=str(backup),new_orders_submitted=0,automatic_trading_started=False)))
