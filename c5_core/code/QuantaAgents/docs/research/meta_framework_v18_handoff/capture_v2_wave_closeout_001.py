"""Capture a bounded closeout; no research or database mutation entrypoints."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parents[3]
OUT = PROJECT / 'experiment_traces/meta_v2_wave_closeout/20260907_001'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def main():
    # Inputs are review artifacts, never instructions or model requests.
    paths = [Path(value).resolve() for value in sys.argv[1:]]
    require(bool(paths), 'final evidence paths are required')
    require(all(path.is_relative_to(PROJECT) and path.is_file() for path in paths), 'evidence outside project or absent')
    OUT.mkdir(parents=True, exist_ok=False)
    pins = {str(path.relative_to(PROJECT)): {'sha256': sha(path), 'bytes': path.stat().st_size} for path in paths}
    preservation = PROJECT / 'experiment_traces/meta_casebank_budget_preparations/reference_v15_original4_001_preservation_001/receipt.json'
    require(sha(preservation) == '8e4b815f09b695ed045b0e71b45d9c83ca1397525c165c5b1c9cb534f0a7aeea', 'preservation receipt changed')
    prior = json.loads(preservation.read_text(encoding='utf-8'))
    db_checks = []
    for saved in prior['backups']:
        path = Path(saved['source']).resolve()
        require(path.is_relative_to(PROJECT), 'source outside project')
        observed = sha(path)
        require(observed == saved['source_sha256_before_after'], 'actual database bytes changed')
        require(path.stat().st_size == saved['source_bytes'], 'actual database size changed')
        for suffix in ('-wal', '-journal'):
            sidecar = Path(str(path) + suffix)
            require(not sidecar.exists() or sidecar.stat().st_size == 0, 'unaccounted database journal')
        db_checks.append({'path': str(path.relative_to(PROJECT)), 'sha256': observed, 'bytes': path.stat().st_size})
    require(len(db_checks) == 5, 'five database coverage required')
    with urlopen('http://192.0.2.10:8776/api/status', timeout=15) as response:
        raw_status = response.read(2 * 1024 * 1024 + 1)
    require(len(raw_status) <= 2 * 1024 * 1024, 'monitor response exceeds bound')
    status = json.loads(raw_status)
    matches = [row for row in status['campaigns'] if row.get('campaign_id') == 'reference_v15_original4_001']
    require(len(matches) == 1, 'campaign not unique')
    campaign = matches[0]
    require(status['paid_dispatch_permitted'] is False and status['formal_target_success'] is False, 'controller gate changed')
    require(campaign['status'] == 'paused' and campaign['calls_reserved'] == 15 and campaign['completed_calls'] == 14, 'research state changed')
    require(campaign['usage']['reported_tokens'] == 491954 and campaign['usage']['reserved_tokens'] == 80000, 'research accounting changed')
    first, *others = campaign['cases']
    require(len(others) == 3 and first['case_id'] == 'case_01', 'original case order changed')
    require((first['queries_completed'], first['candidates_reserved'], first['final_submissions']) == (14, 12, 0), 'first task changed')
    require(all(row['status'] == 'not_started' and row['transport_calls_reserved'] == 0 and row['final_submissions'] == 0 for row in others), 'successor task changed')
    require(all(sha(PROJECT / path) == pin['sha256'] for path, pin in pins.items()), 'evidence changed during capture')
    (OUT / 'monitor_status.json').write_bytes(raw_status)
    receipt = {
        'kind': 'user_authorized_engineering_and_records_only_closeout',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'evidence_pins': pins,
        'actual_database_identity': db_checks,
        'monitor_status_sha256': hashlib.sha256(raw_status).hexdigest(),
        'monitor_checkpoint': status['checkpoint'],
        'research_summary': campaign,
        'actual_management_applied': False,
        'new_research_gateway_calls': 0,
        'remaining_cases_started': False,
        'formal_target_success': False,
        'research_result_files_rewritten': False,
        'source_scope': 'File identity bridges to the already independently verified five-database preservation; no SQLite connections or table rescan.',
        'liveness_scope': 'Monitor API is readable; this capture does not certify worker liveness or a supplier bill.',
    }
    (OUT / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'receipt': str(OUT / 'receipt.json'), 'sha256': sha(OUT / 'receipt.json'), 'actual_databases_unchanged': 5, 'new_research_calls': 0}, ensure_ascii=True))


if __name__ == '__main__':
    main()
