"""Recompute the deployment manifest hashes of the patched executor code files (data entries untouched)."""
import json, hashlib
from pathlib import Path
ROOT=Path('C:/ChoiceBridge/executor'); BASE=ROOT/'research/choice_pcf_automation'; mp=ROOT/'deployment_manifest.json'; m=json.loads(mp.read_text(encoding='utf-8'))
for f in ['pilot_one_lot.py','workflow_rebalance.py','console_runtime.py']: m['files']['research/choice_pcf_automation/'+f]=hashlib.sha256((BASE/f).read_bytes()).hexdigest()
mp.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8'); print('manifest refreshed')
