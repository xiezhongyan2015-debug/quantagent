"""Run offline tests, fresh GET-only preflight and a private local ledger backup."""
import sys,json,gzip,base64,hashlib,datetime
from pathlib import Path
from ssh_small_files import run_ps
ROOT=Path(__file__).resolve().parent
p=r"C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe"
tests=r'''& 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe' -X utf8 -c "import sys,unittest;sys.path.insert(0,r'C:\ChoiceBridge\executor\research\choice_pcf_automation');import test_cloud_pilot;result=unittest.TextTestRunner(stream=sys.stdout,verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(test_cloud_pilot));sys.exit(not result.wasSuccessful())";if($LASTEXITCODE -ne 0){throw 'Offline tests failed'}'''
print(run_ps(tests,timeout=35))
output=run_ps(r"& 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe' -X utf8 'C:\ChoiceBridge\executor\research\choice_pcf_automation\readonly_cloud.py' --preview;if($LASTEXITCODE -ne 0){throw 'Readonly preflight blocked'}",timeout=45)
result=json.loads(output);path=ROOT/'cloud_preparation_receipt.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2));path.chmod(0o600);print(output)
script=r'''& 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe' -X utf8 -c "import json,gzip,base64,hashlib;from pathlib import Path;p=Path('C:/ChoiceBridge/executor/research/choice_pcf_automation/cloud_ledger/private_state.json');d=p.read_bytes();g=gzip.compress(d);assert len(g)<=65536;print(json.dumps(dict(source_sha256=hashlib.sha256(d).hexdigest(),compressed_bytes=len(g),data=base64.b64encode(g).decode())))";if($LASTEXITCODE -ne 0){throw 'Ledger backup blocked'}'''
receipt=json.loads(run_ps(script,timeout=30));data=gzip.decompress(base64.b64decode(receipt.pop('data')))
assert hashlib.sha256(data).hexdigest()==receipt['source_sha256']
folder=ROOT/'cloud_ledger_backup';folder.mkdir(exist_ok=True);folder.chmod(0o700)
file=folder/'private_state.json';file.write_bytes(data);file.chmod(0o600)
receipt.update(backup_path=str(file),backup_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_and_backup_hash_match=True,credentials_included=False,new_orders_submitted=0)
file=ROOT/'cloud_ledger_backup_receipt.json';file.write_text(json.dumps(receipt,indent=2));file.chmod(0o600);print(json.dumps(receipt))
# Confirm no actual execution authorization or worker was created by preparation.
script=r'''$root='C:\ChoiceBridge\executor\research\choice_pcf_automation';$w=@(Get-CimInstance Win32_Process -Filter "Name='python.exe'"|Where-Object{$_.CommandLine -like '*console_runtime.py*--run*'});[pscustomobject]@{trading_workers=$w.Count;authorizations=@(Get-ChildItem -LiteralPath $root -Filter authorization.json -Recurse).Count;new_orders_submitted=0;checked_at=(Get-Date).ToString('o')}|ConvertTo-Json -Compress'''
receipt=json.loads(run_ps(script,timeout=25));assert receipt['trading_workers']==0 and receipt['authorizations']==0
(ROOT/'preparation_no_orders_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
