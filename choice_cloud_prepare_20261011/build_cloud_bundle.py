from pathlib import Path
import zipfile,json,hashlib,ast
prep=Path(__file__).resolve().parent;root=prep/'full_executor';app=Path('research/choice_pcf_automation');lib=Path('references/quantagent-pr/src/quanta_agents')
apps=['verify_readonly','cloud_account','console_core','console_runtime','console_app','choice_auth','watch_engine','batch_engine','position_floor','receipt_recovery','refresh_stopped_ledger','reconcile_previous_receipt','pilot_one_lot','readonly_cloud','test_cloud_pilot']
paths=[app/(n+'.py') for n in apps]+[lib/'__init__.py',lib/'factor_lab_a/__init__.py']+[lib/'factor_lab_a'/(n+'.py') for n in ['pcf','competition_checks','pcf_automation','choice_pcf','choice_quotes','security_identity']]+[app/'auth_local/identity.json']+[app/'daily_public_pcf'/n for n in ['calendar.json','csi500_members.json','563030_20261009_official.xml']]
for p in paths:
 if p.suffix=='.py':ast.parse((root/p).read_text())
manifest={'version':1,'source_zip_sha256':'aa0e0a0959e1667351579cfe98b635f3064cd4e993d9ab218eb6201247298785','scope':'complete console worker plus all runtime/reconciliation/receipt/auth/strategy dependencies; dated launchers and unrelated research excluded','files':{p.as_posix():hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths},'orders_submitted':0}
(root/'deployment_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
p=prep/'executor_cloud_pilot.zip'
with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for name in paths+[Path('deployment_manifest.json')]:z.write(root/name,name.as_posix())
print('package files',len(paths),'bytes',p.stat().st_size,'sha',hashlib.sha256(p.read_bytes()).hexdigest())
data=p.read_bytes()
for start in range(0,len(data),48000):p.with_name('executor_cloud_pilot.part'+str(start//48000)).write_bytes(data[start:start+48000])
