"""Install the explicitly requested GRU tilt release only with no active worker."""
import sys,json,hashlib,zipfile,os,shutil
from pathlib import Path
ROOT=Path('C:/ChoiceBridge/executor');BASE=ROOT/'research/choice_pcf_automation';sys.path.insert(0,str(BASE))
from console_runtime import active_session,mutex,APP_DIR
from cloud_account import verify_deployment
from workflow_rebalance import inputs
from quanta_agents.factor_lab_a.pcf_automation import now_cn
TARGET='research/choice_pcf_automation/daily_public_pcf/v5_tilt_rebalance_target_20261012.json';SETTINGS='research/choice_pcf_automation/console/settings.json'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
with mutex(APP_DIR/'launch.lock'),mutex(APP_DIR/'account_execution.lock'):
 assert not active_session(),'Cannot replace live strategy'
 assert now_cn().date().isoformat()=='2026-10-12'
 verify_deployment();oldpath=ROOT/'deployment_manifest.json';assert sha(oldpath)=='125592a570f9e8a1bb2cccbe2f347f9fba5983117481a2064ac6d39152c1ffef'
 package=Path('C:/ChoiceBridge/staging/gru_tilt_release.zip');archive=Path('C:/ChoiceBridge/staging/gru_tilt_inputs.zip')
 assert sha(package)==sys.argv[1] and sha(archive)==sys.argv[2]
 protected={str(p):sha(p) for p in BASE.rglob('*.json') if any(x in p.parts for x in ('runs','cloud_ledger','cloud_ledger_dividend_20261012'))}
 old=json.loads(oldpath.read_text(encoding='utf8'));backup=Path('C:/ChoiceBridge/staging/gru_tilt_before_'+now_cn().strftime('%H%M%S'));backup.mkdir()
 with zipfile.ZipFile(package) as z:
  assert set(z.namelist())=={TARGET,SETTINGS,'deployment_manifest.json'} and len(z.namelist())==3
  new=json.loads(z.read('deployment_manifest.json'));assert set(new['files'])==set(old['files'])|{TARGET}
  assert all(old['files'][k]==new['files'][k] for k in old['files'])
  assert hashlib.sha256(z.read(TARGET)).hexdigest()==new['files'][TARGET]
  assert not (ROOT/TARGET).exists()
  config=json.loads(z.read(SETTINGS));assert config['strategy']=='pilot_then_targets' and config['rebalance_after_pilot']['target_sha256']==new['files'][TARGET]
  assert config['pilot_shares']==100 and config['pilot_total_cap_cny']==10000 and config['cash_drift_tolerance_cny']==.03
  for n in (SETTINGS,'deployment_manifest.json'):
   b=backup/n;b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/n,b)
  try:
   for n in (TARGET,SETTINGS,'deployment_manifest.json'):
    dst=ROOT/n;tmp=dst.with_suffix('.reviewed.tmp');tmp.write_bytes(z.read(n));os.replace(tmp,dst)
   verify_deployment();target,m=inputs(config);assert target['strategy_layer']=='V5_tilt' and len(target['targets'])==108
   assert not active_session() and all(sha(Path(k))==h for k,h in protected.items())
  except BaseException:
   for n in (SETTINGS,'deployment_manifest.json'):shutil.copy2(backup/n,ROOT/n)
   (ROOT/TARGET).unlink(missing_ok=True);raise
 with zipfile.ZipFile(archive) as z:
  names={'target_original.json','precheck_original.json','gru_z_20261009.csv','blend_20261009.csv','signal_report.md','provenance.json'}
  assert set(z.namelist())==names and len(z.namelist())==len(names)
  provenance=json.loads(z.read('provenance.json'))
  for n in names-{'provenance.json'}:assert hashlib.sha256(z.read(n)).hexdigest()==provenance['files'][n]['sha256']
  dest=Path('C:/ChoiceBridge/signals/v5_tilt_20261012');dest.mkdir(exist_ok=False)
  for n in names:(dest/n).write_bytes(z.read(n))
 receipt=dict(at=now_cn().isoformat(),prepared=True,strategy_layer='V5_tilt',target_names=108,target_path=str(ROOT/TARGET),target_sha256=sha(ROOT/TARGET),manifest_sha256=sha(oldpath),source_inputs=str(dest),all_prior_run_files_unchanged=True,original_C5_target_preserved=True,orders_submitted=0,automatic_trading_started=False,requires_operator_start=True)
 (dest/'deployment_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(receipt,ensure_ascii=False))
