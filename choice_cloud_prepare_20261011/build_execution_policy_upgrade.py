import json,hashlib,zipfile
from pathlib import Path
R=Path(__file__).resolve().parent;E=R/'full_executor'
files={'research/choice_pcf_automation/'+n for n in ('workflow_rebalance.py','console_runtime.py','test_execution_policy.py')}
old=json.loads((R/'efficiency_manifest.json').read_text())
assert old['adapter_revision']==14
new=json.loads(json.dumps(old));new['adapter_revision']=15
new['execution_policy']='Formal phase only: directional adverse 3 percent guard (buy <=103 percent reference, sell >=97 percent); executor-origin terminal status8 zero-filled cancellations may retry twice at fresh quotes only after two fresh complete consistent audits; all partial/rejected/unknown still blocked; no target/config/auth/seed edits.'
for n in files:new['files'][n]=hashlib.sha256((E/n).read_bytes()).hexdigest()
raw=(json.dumps(new,ensure_ascii=False,indent=2)+'\n').encode();(R/'execution_policy_manifest.json').write_bytes(raw)
with zipfile.ZipFile(R/'execution_policy_upgrade.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
 for n in sorted(files):z.write(E/n,n)
 z.writestr('deployment_manifest.json',raw)
base=(R/'apply_efficiency_upgrade.py').read_text()
base=base.replace('Apply three reviewed Python files only while all execution workers are stopped.','Apply reviewed execution policy only after the operator stops the existing worker.')
base=base.replace("efficiency_upgrade.zip","execution_policy_upgrade.zip")
start=base.index('FILES=');end=base.index('\ndef sha',start)
base=base[:start]+'FILES='+repr(files)+base[end:]
base=base.replace("old['adapter_revision']==13","old['adapter_revision']==14")
base=base.replace(" verify_deployment();assert sha(PACKAGE)==sys.argv[1]"," verify_deployment();assert sha(ROOT/'deployment_manifest.json')=='"+hashlib.sha256((R/'efficiency_manifest.json').read_bytes()).hexdigest()+"';assert sha(PACKAGE)==sys.argv[1]")
base=base.replace("assert set(new['files'])==set(old['files'])|FILES","assert new['adapter_revision']==15;assert set(new['files'])==set(old['files'])|FILES")
base=base.replace("efficiency_before_","policy_before_")
base=base.replace("assert r.testsRun==119 and r.wasSuccessful()\"", "assert r.testsRun==119 and r.wasSuccessful();p=unittest.defaultTestLoader.loadTestsFromName('test_execution_policy');v=unittest.TextTestRunner(verbosity=1).run(p);assert v.testsRun==24 and v.wasSuccessful()\"")
base=base.replace('changed_files=5','changed_files=3').replace('offline_tests_passed=119,all_run_files_unchanged','offline_tests_passed=119,policy_tests_passed=24,total_tests_passed=143,all_run_files_unchanged')
(R/'apply_execution_policy_upgrade.py').write_text(base)
review=(R/'review_efficiency_upgrade.py').read_text().replace('efficiency_upgrade.zip','execution_policy_upgrade.zip').replace('efficiency_review','execution_policy_review')
review=review.replace("['test_cloud_pilot','test_workflow_rebalance','test_executor_efficiency']","['test_cloud_pilot','test_workflow_rebalance','test_executor_efficiency','test_execution_policy']").replace('==119','==143').replace('cloud_offline_tests=119','cloud_offline_tests=143')
(R/'review_execution_policy_upgrade.py').write_text(review)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
meta=dict(package_sha256=sha(R/'execution_policy_upgrade.zip'),applier_sha256=sha(R/'apply_execution_policy_upgrade.py'),expected_before_manifest_sha256=sha(R/'efficiency_manifest.json'),new_manifest_sha256=sha(R/'execution_policy_manifest.json'),target_sha256='6034463bec5a7dcb09d8acbed45041a832cbda602b0880a1d04de5e05fa6eee8',offline_tests_passed=119,policy_tests_passed=24,total_tests_passed=143,activation_requires_one_operator_button=True,prepared_policy_revision=15,already_installed=False)
(R/'execution_policy_upgrade_pending.json').write_text(json.dumps(meta,indent=2))
print(json.dumps(meta));print('package_bytes', (R/'execution_policy_upgrade.zip').stat().st_size)
