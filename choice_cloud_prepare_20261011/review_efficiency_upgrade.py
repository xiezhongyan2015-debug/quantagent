import sys,json,hashlib,zipfile,unittest,io
from pathlib import Path
p=Path('C:/ChoiceBridge/staging/efficiency_upgrade.zip');assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[1]
stage=Path('C:/ChoiceBridge/staging/efficiency_review');stage.mkdir(exist_ok=True)
with zipfile.ZipFile(p) as z:z.extractall(stage)
sys.path[:0]=[str(stage/'research/choice_pcf_automation'),'C:/ChoiceBridge/executor/research/choice_pcf_automation']
s=unittest.defaultTestLoader.loadTestsFromNames(['test_cloud_pilot','test_workflow_rebalance','test_executor_efficiency']);stream=io.StringIO();r=unittest.TextTestRunner(verbosity=1,stream=stream).run(s);assert r.testsRun==119 and r.wasSuccessful(),stream.getvalue();print(json.dumps(dict(cloud_offline_tests=119,installed_executor_unchanged=True,financial_actions_started=False)))
