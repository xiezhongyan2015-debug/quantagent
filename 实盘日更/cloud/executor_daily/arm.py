"""Arm the executor for the next session exactly as the console's Start button does (headless). Allowed the evening before.
Run with the executor's interpreter: C:/ChoiceBridge/runtime/choice-venv/Scripts/python.exe arm.py [--status]"""
import sys, json, os, time
from pathlib import Path
ROOT = Path('C:/ChoiceBridge/executor'); BASE = ROOT / 'research/choice_pcf_automation'; sys.path.insert(0, str(BASE)); os.chdir(str(ROOT))
from console_runtime import start_console, active_session, APP_DIR
from cloud_account import verify_deployment
from quanta_agents.factor_lab_a.pcf_automation import read_json
verify_deployment()
if '--status' in sys.argv:
    d = active_session(); print(json.dumps(dict(active=str(d) if d else None, status=read_json(d / 'status_redacted.json') if d else None), ensure_ascii=False)); sys.exit(0)
cfg = read_json(BASE / 'console/settings.json')
result = start_console(cfg); time.sleep(3)
d = Path(result['directory']); status = read_json(d / 'status_redacted.json') if (d / 'status_redacted.json').exists() else {}
print(json.dumps(dict(result=result, status=status, execution_date=cfg['execution_date'], pilot_symbol=cfg['pilot_symbol'], target_sha256=cfg['rebalance_after_pilot']['target_sha256']), ensure_ascii=False))
if result['action'] not in ('pipeline_scheduled', 'scheduled', 'started', 'attached') or status.get('status') not in ('scheduled_waiting', 'starting', 'waiting_open'): sys.exit(3)
