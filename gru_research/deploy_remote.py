"""Wait for completed browser uploads, verify the dataset, then submit training."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ACCOUNT_ROOT = Path('/home/users/s1155284034')
CODE_ROOT = ACCOUNT_ROOT / 'gru_hourly_20261001'
DATA_ROOT = Path('/data/users/s1155284034/gru_hourly_20261001')
MANIFEST = ACCOUNT_ROOT / 'gru_dataset_transfer.json'
STATUS = ACCOUNT_ROOT / 'gru_deployment_status.json'
EXPECTED = 'aa25c22ba29db27517a53c4580002fe6d3a55712f1c32b408629dcb2e5b3ac9d'


def record(state):
    state['at_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    temporary = STATUS.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n')
    temporary.replace(STATUS)
    print(json.dumps(state), flush=True)


def main():
    manifest = json.loads(MANIFEST.read_text())
    if manifest['data_fingerprint'] != EXPECTED:
        raise ValueError('Uploaded manifest does not identify the selected audited data')
    started, next_log = time.monotonic(), 0.
    while True:
        sizes = [(ACCOUNT_ROOT / p['name']).stat().st_size if (ACCOUNT_ROOT / p['name']).exists() else 0 for p in manifest['parts']]
        complete = sum(n == p['bytes'] for n, p in zip(sizes, manifest['parts']))
        if any(n > p['bytes'] for n, p in zip(sizes, manifest['parts'])):
            raise ValueError('An uploaded data part is larger than the verified original')
        elapsed = time.monotonic() - started
        if elapsed >= next_log or complete == len(sizes):
            record({'state': 'uploading' if complete < len(sizes) else 'verifying_transfer',
                    'complete_parts': complete, 'total_parts': len(sizes),
                    'received_bytes': sum(sizes), 'total_bytes': manifest['bytes'],
                    'elapsed_seconds': round(elapsed, 1)})
            next_log = elapsed + 15
        if complete == len(sizes):
            break
        if elapsed > 1800:
            raise TimeoutError('Browser data upload has not completed after 30 minutes')
        time.sleep(5)
    subprocess.run([sys.executable, '-m', 'gru_research.receive_dataset', '--manifest', str(MANIFEST), '--destination', str(DATA_ROOT)], cwd=CODE_ROOT, check=True)
    result = subprocess.check_output(['sbatch', 'gru_research/submit.sbatch'], cwd=CODE_ROOT, text=True)
    match = re.fullmatch(r'Submitted batch job (\d+)\s*', result)
    if not match:
        raise ValueError('Slurm did not return an identifiable training job: ' + result)
    record({'state': 'submitted', 'job_id': int(match[1]), 'data_fingerprint': EXPECTED,
            'job_log': str(CODE_ROOT / f'outputs/slurm-{match[1]}.log'),
            'formal_output': str(CODE_ROOT / 'outputs/formal')})


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        record({'state': 'failed', 'error': repr(error)})
        raise
