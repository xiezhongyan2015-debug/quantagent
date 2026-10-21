"""One serial, identity-bound offline regression of the assembled sixth revision."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / 'experiment_traces/meta_ashare_revision6'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(package):
    files = {str(p.relative_to(package)): sha(p) for p in sorted(package.rglob('*'))
             if p.is_file() and p.suffix in {'.py', '.yaml', '.html'}}
    return {'files': files, 'hash': hashlib.sha256(json.dumps(files,
        ensure_ascii=False, allow_nan=False, default=str).encode('utf-8')).hexdigest()}


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    sys.path.insert(0, str(STAGE / 'src'))
    import pytest
    output = STAGE / 'validation_attempts' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    before = manifest(STAGE / 'src/quanta_agents')
    references = {}
    for revision in (3, 4, 5):
        directory = ROOT / f'experiment_traces/meta_ashare_revision{revision}'
        expected = json.loads((directory / 'validation_receipt.json').read_text(encoding='utf-8'))['source_manifest']
        assert manifest(directory / 'src/quanta_agents') == expected, f'Frozen revision {revision} drift'
        references[str(revision)] = expected['hash']
    old = json.loads((ROOT / 'experiment_traces/meta_ashare_v2/meta-20260906-040054-403409/manifest.json').read_text(encoding='utf-8'))['source_manifest']
    assert manifest(ROOT / 'src/quanta_agents') == old, 'Main frozen source drift'
    selected = sorted((STAGE / 'tests').glob('test_meta_*.py'))
    selected += [STAGE / 'tests' / name for name in (
        'test_local_parquet_backtest.py', 'test_raw_share_ledger.py',
        'test_raw_share_commitment.py', 'test_raw_daily_data.py',
        'test_corporate_action_adapter.py', 'test_raw_portfolio_backtest.py')]
    test_files = {str(p.relative_to(STAGE)): sha(p) for p in selected}
    save(output / 'plan.json', {'source_manifest': before, 'test_files': test_files,
        'runner_sha256': sha(Path(__file__)), 'verified_frozen_revisions': references,
        'verified_main_source': old['hash'], 'real_model_calls_permitted': 0})
    started = time.monotonic()
    code = pytest.main([str(p) for p in selected] + ['-q', '--disable-warnings', '--junitxml=' + str(output / 'junit.xml')])
    after = manifest(STAGE / 'src/quanta_agents')
    suites = list(ET.parse(output / 'junit.xml').getroot().iter('testsuite'))
    counts = {key: sum(int(s.get(key, '0')) for s in suites) for key in ('tests', 'errors', 'failures', 'skipped')}
    stable = after == before and test_files == {str(p.relative_to(STAGE)): sha(p) for p in selected}
    receipt = {'status': 'passed' if code == 0 and stable else 'failed', 'exit_code': code,
        'source_stable': stable, 'source_hash': before['hash'], 'counts': counts,
        'elapsed_seconds': time.monotonic() - started, 'junit_sha256': sha(output / 'junit.xml'),
        'real_model_calls': 0, 'execution_valid': False, 'formal_target_success': False}
    save(output / 'receipt.json', receipt)
    print(json.dumps({'output': str(output), **receipt}, ensure_ascii=False), flush=True)
    return 0 if receipt['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
