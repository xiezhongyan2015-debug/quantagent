"""Record actual completed source and remote model checks before assembly."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parent
    art = root / 'artifacts'
    remote = (art / 'remote_training_tests.log').read_text()
    if not re.search(r'Ran 9 tests', remote) or '\nOK\n' not in remote or 'REMOTE_TEST_EXIT=0' not in remote or re.search(r'\.\.\. skipped|OK \(skipped=', remote):
        raise ValueError('The remote model checks have not all passed')
    uploaded = json.loads((art / 'training_code_upload_manifest.json').read_text())
    for name in ['model.py', 'train.py', 'evaluate.py', 'config.json', 'tests/test_training.py']:
        if sha(root / name) != uploaded['gru_research/' + name]:
            raise ValueError('Training code changed since successful remote checks: ' + name)
    result = subprocess.run([sys.executable, '-m', 'unittest',
                             'gru_research.tests.test_minute_data',
                             'gru_research.tests.test_slow_features',
                             'gru_research.tests.test_assembly', '-v'],
                            cwd=root.parent, capture_output=True, text=True)
    log = result.stdout + result.stderr
    (art / 'local_source_tests.log').write_text(log)
    count = re.search(r'Ran (\d+) tests', log)
    if result.returncode or not count or '\nOK\n' not in log or re.search(r'\.\.\. skipped|OK \(skipped=', log):
        raise ValueError('Local source correctness checks failed; read local_source_tests.log')
    for name, key in [('minute_actual_data_verification.json', 'status'),
                      ('slow_independent_pit_spotcheck.json', 'all_comparisons_passed')]:
        value = json.loads((art / name).read_text())[key]
        if value not in [True, 'passed']:
            raise ValueError('Real data checks did not pass: ' + name)
    receipt = {'passed': True, 'checked_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'local_tests': int(count[1]), 'remote_model_tests': 9,
               'local_returncode': result.returncode, 'remote_returncode': 0,
               'local_test_log_sha256': sha(art / 'local_source_tests.log'),
               'remote_test_log_sha256': sha(art / 'remote_training_tests.log'),
               'source_code_sha256': {n: sha(root / n) for n in ['minute_data.py', 'slow_features.py',
                 'assemble.py', 'model.py', 'train.py', 'evaluate.py', 'config.json',
                 'tests/test_minute_data.py', 'tests/test_slow_features.py',
                 'tests/test_assembly.py', 'tests/test_training.py']}}
    (art / 'source_tests_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
