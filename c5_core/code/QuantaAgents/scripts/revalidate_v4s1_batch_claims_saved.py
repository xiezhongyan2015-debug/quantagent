"""One offline revalidation of two original exposed-development final reports.

Reads the closed ledgers in SQLite read-only mode; invokes only final report
validation on already saved artifacts. Never instantiates a runtime, executes
a strategy, resumes an old opportunity, or changes original delivery status.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.ledger import digest, need
from quanta_agents.meta_v3.research_tools import ResearchTools, save_once
from quanta_agents.meta_v3.report_arguments import decode, STRUCTURED_VERSION


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def revalidate(trial):
    root = ROOT / 'experiment_traces/v4s1' / trial
    plan, status = read(root / 'plan.json'), read(root / 'status.json')
    case = plan['tasks']['research']['case']
    need(case['research_class'] == 'real_saved_development', 'exposed development only')
    with sqlite3.connect((root / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        history = [{'id': row['id'], 'status': row['status'],
                    'response': json.loads(row['receipt'])['response'] if row['receipt'] else None,
                    'result': json.loads(row['result']) if row['result'] else None}
                   for row in db.execute('SELECT id,status,receipt,result FROM calls WHERE task_id=? ORDER BY ordinal', ('research',))]
    final_id = status['tasks']['research']['calls'][-1]['id']
    final_row = next(row for row in history if row['id'] == final_id)
    need(final_row['response']['action'] == 'submit_research_report', 'original final response required')
    args, proof = decode(final_row['response'], {'version': STRUCTURED_VERSION})
    need(args['outcome'] == 'abstain', 'offline abstention validation only')
    originals = [root / name for name in ('plan.json', 'status.json', 'ledger.sqlite3')]
    originals += [root / 'calls' / final_id / name for name in ('response.json', 'application.json')]
    for row in history:
        if row['id'] in args['evidence_ids'] and row['result'] and row['result'].get('artifact_hash'):
            originals.append(root / 'tools/research' / row['id'] / 'artifact.json')
    pins = {str(path): sha(path) for path in originals}
    # Bypass constructor initialization: final() needs only these existing
    # values for an abstention; it does not need input matrices or a worker.
    checker = ResearchTools.__new__(ResearchTools)
    checker.root, checker.task_id, checker.case = root, 'research', case
    checker.history, checker.imported = history, None
    checker.folder = root / 'tools/research'
    checker.report_policy = case['report_policy']
    before_history = digest(history)
    result = checker.final(args)
    need(digest(history) == before_history, 'report checker mutated original history')
    need(all(sha(Path(path)) == pin for path, pin in pins.items()), 'original source drift')
    need(result['model_report'] == args and result['formal_target_success'] is False
         and result['substantive_report_accepted'] is None
         and result['claim_support']['evidence_support_accepted'] is False,
         'structured consistency must not promote original prose or research success')
    return {'trial': trial, 'original_final_id': final_id,
            'original_terminal': status['tasks']['research']['terminal'],
            'original_submission_result': final_row['result'],
            'argument_decoding': proof, 'claim_status_counts': dict(Counter(row['status'] for row in result['claim_support']['claims'])),
            'new_checker_result': result, 'source_pins': pins, 'original_sources_unchanged': True,
            'original_delivery_status_changed': False, 'original_history_mutated': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New append-only audit JSON path')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    need(not output.exists(), 'refuse overwrite')
    code = [Path(__file__).resolve()] + [ROOT / 'src/quanta_agents/meta_v3' / name
            for name in ('claim_support.py', 'batch_claim_support.py', 'research_tools.py', 'report_arguments.py')]
    pins = {str(path): sha(path) for path in code}
    rows = [revalidate(trial) for trial in ('s1', 's2')]
    need(all(sha(Path(path)) == pin for path, pin in pins.items()), 'checker source changed during audit')
    result = {'kind': 'saved_batch_claim_custody_revalidation_v1',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'rows': rows, 'checker_source_pins': pins,
        'acceptance_scope': 'Original cited saved complete account scalar consistency. No raw-account re-execution, prose entailment, causal identification, execution certification or formal strategy acceptance.',
        'new_model_calls': 0, 'new_strategy_executions': 0, 'new_market_samples': 0,
        'old_scope_resumed': False, 'formal_target_success': False,
        'provider_bill': None, 'controller_provider_tokens': None, 'unknown_cost_is_not_zero': True}
    save_once(output, result)
    print(json.dumps({'output': str(output), 'counts': {row['trial']: row['claim_status_counts'] for row in rows}}, ensure_ascii=True))


if __name__ == '__main__':
    main()
