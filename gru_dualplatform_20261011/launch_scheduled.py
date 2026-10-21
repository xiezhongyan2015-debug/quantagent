"""Boot entry point for the six budgeted Aliyun models."""
from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import sys


def main():
    schedule = json.loads(Path('scheduled_run.json').read_text())
    if datetime.now(timezone.utc).timestamp() < datetime.fromisoformat(schedule['not_before']).timestamp():
        print('Prepared; scheduled research start has not arrived', flush=True)
        return
    result = subprocess.run([sys.executable, '-u', '-m', 'gru_dualplatform_20261011.run_daily_queue',
                             '--mode', 'GRU', '--seeds', '11', '--deadline', schedule['local_deadline'],
                             '--platform-stop-receipt', 'platform_stop_receipt.json'])
    expected = [Path(f'outputs/gru_dualplatform_20261011/B/GRU/T{year}/seed11/export.json')
                for year in range(2021,2027)]
    complete = result.returncode == 0 and all(p.exists() and json.loads(p.read_text())['status']=='complete' for p in expected)
    receipt = {'finished_at': datetime.now(timezone.utc).isoformat(), 'exit_code':result.returncode,
               'all_six_models_complete':complete, 'billing_stop_requires_platform_StopCharging':True}
    Path('outputs/gru_dualplatform_20261011/aliyun_queue_result.json').write_text(json.dumps(receipt,indent=2))
    raise SystemExit(result.returncode if result.returncode else (0 if complete else 124))


if __name__ == '__main__':
    main()
