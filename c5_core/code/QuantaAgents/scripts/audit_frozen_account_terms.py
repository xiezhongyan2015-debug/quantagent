"""One exposed v1 candidate: saved fee/tax audit, never a new final selection.

build_spec only binds old files and code identities. audit_saved_candidate is
read-only and returns a result; callers own one-shot dispatch custody and output.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.frozen_account_terms import POLICY, RULE_SOURCES, recompute_terms
from quanta_agents.meta_v3.kernel import FROZEN
from quanta_agents.meta_v3.ledger import digest, need

STAGE = ROOT / 'experiment_traces/v4s1/v1'
CALL = 'research_011_c7700a001b27'
FINAL = 'research_014_c8a281224dcf'
FOLDER = STAGE / 'tools/research' / CALL
RAW = FOLDER / 'workbench/raw_children/program'
PRIOR = ROOT / 'docs/research/meta_framework_v4_handoff/v4s1_v1_model_account_audit_001.json'
PRIOR_SHA256 = '24a6f86093bfeed9b6aed1745fdb200b7e723fa707ec5ed5d3cb3546475d7421'
VERSION = 'v4s1_v1_frozen_account_terms_spec_v1'


def sha(path):
    path = Path(path)
    need(path.is_file() and path.resolve() == path.absolute() and not path.is_symlink() and
         path.stat().st_size <= 128 * 1024**2, 'missing/redirected/oversized saved input')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    sha(path)
    return json.loads(Path(path).read_text(encoding='utf-8'))


def code_pins():
    """Complete project-source dependency set for this fixed reader/recomputer."""
    paths = [Path(__file__).resolve(), ROOT / 'src/quanta_agents/__init__.py',
             ROOT / 'src/quanta_agents/meta_v3/__init__.py']
    paths += [ROOT / 'src/quanta_agents/meta_v3' / (name + '.py') for name in
              ('frozen_account_terms', 'kernel', 'ledger', 'closing', 'saved_execution', 'archive_codec')]
    paths += [FROZEN / name for name in (*RULE_SOURCES, 'raw_saved_research.py')]
    return {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}


def _prior_binding():
    need(sha(PRIOR) == PRIOR_SHA256, 'original prior audit bytes differ')
    prior = read(PRIOR)
    need(prior['trial_id'] == 'v1' and Path(prior['root']).resolve() == STAGE.resolve(), 'prior audit scope differs')
    reports = [row for row in prior['reports'] if row['call_id'] == FINAL]
    need(len(reports) == 1 and reports[0]['status'] == 'applied' and
         reports[0]['result']['model_report']['outcome'] == 'abstain' and
         reports[0]['result']['model_report']['program_evidence_id'] is None, 'original abstention binding differs')
    accounts = [row for row in prior['accounts'] if row['opportunity_id'] == 'ordinary/' + CALL]
    need(len(accounts) == 1, 'fixed candidate absent/ambiguous in prior audit')
    wanted = {}
    for relative, pin in prior['original_file_hashes'].items():
        normalized = relative.replace('\\', '/')
        if normalized in ('plan.json', 'ledger.sqlite3', 'status.json') or any(normalized.startswith(prefix) for prefix in
            ('tools/research/' + CALL + '/', 'calls/' + CALL + '/', 'calls/' + FINAL + '/')):
            path = STAGE / normalized
            need(path.resolve().is_relative_to(STAGE.resolve()), 'prior audit path escaped fixed stage')
            need(sha(path) == pin, 'original saved file differs: ' + normalized)
            wanted[normalized] = pin
    need({'plan.json', 'ledger.sqlite3', 'status.json',
          'tools/research/' + CALL + '/artifact.json',
          'tools/research/' + CALL + '/workbench/raw_children/program/plan.json',
          'tools/research/' + CALL + '/workbench/raw_children/program/research.sqlite3'} <= set(wanted), 'prior custody incomplete')
    return accounts[0], reports[0], wanted


def build_spec():
    """Freeze metadata/hashes only; no ledger replay, fees or tax audit occurs."""
    account, final, originals = _prior_binding()
    artifact = read(FOLDER / 'artifact.json'); raw_plan = read(RAW / 'plan.json')
    raw = artifact['raw']
    need(raw['status'] == 'completed_mechanical' and raw['pending_event_intent'] is None, 'complete saved candidate required')
    need(raw['plan_sha256'] == raw_plan['plan_sha256'] and raw['record_head_sha256'] == account['raw_record_head_sha256'], 'raw identity differs from original audit')
    need(digest(artifact) == account['artifact_hash'] and digest(artifact['program']) == account['program_hash']
         == raw_plan['identity']['strategy_hash'], 'candidate/program binding differs')
    return {'version': VERSION, 'study_id': 'v4s1', 'trial_id': 'v1', 'task_id': 'research', 'call_id': CALL,
        'stage_root': str(STAGE), 'prior_audit_path': str(PRIOR), 'prior_audit_sha256': PRIOR_SHA256,
        'input_file_sha256': originals, 'code_pins': code_pins(), 'policy': POLICY, 'policy_sha256': digest(POLICY),
        'case_sha256': raw_plan['identity']['data_hash'], 'program_sha256': account['program_hash'],
        'artifact_sha256': account['artifact_hash'], 'plan_sha256': raw_plan['plan_sha256'],
        'record_head_sha256': raw['record_head_sha256'], 'result_sha256': digest(raw['result']),
        'calendar_sha256': digest(raw_plan['calendar']), 'original_initial_cash': raw_plan['initial_cash'],
        'original_final_call_id': FINAL, 'original_final_report_sha256': digest(final['result']['model_report']),
        'original_final_outcome': 'abstain', 'selection_scope': 'explicit_old_candidate_audit_not_final_selection',
        'formal_target_success': False}


def audit_saved_candidate(spec):
    """Return one saved audit. No write, registration, strategy, or market read."""
    need(digest(spec) == digest(build_spec()), 'frozen candidate specification drift')
    before = dict(spec['input_file_sha256'])
    from quanta_agents.meta_v3.saved_execution import SavedRawResearch
    raw_plan = read(RAW / 'plan.json'); artifact = read(FOLDER / 'artifact.json')
    replay = SavedRawResearch(RAW).reconcile_saved_only(expected_plan_sha256=spec['plan_sha256'])
    need(digest(replay) == digest(artifact['raw']) and replay['record_head_sha256'] == spec['record_head_sha256'], 'saved SQL replay differs from bound artifact')
    need(replay['status'] == 'completed_mechanical' and replay.get('pending_event_intent') is None and
         replay.get('error') is None, 'saved account not complete')
    review = recompute_terms(raw_plan, replay['result'], expected_plan_sha256=spec['plan_sha256'],
        expected_result_sha256=spec['result_sha256'], input_kind='caller_bound_saved_records')
    after = {name: sha(STAGE / name) for name in before}
    need(after == before and code_pins() == spec['code_pins'] and sha(PRIOR) == PRIOR_SHA256, 'audit input/source changed')
    return {'kind': 'v4s1_v1_saved_frozen_terms_audit_v1', 'observed_at': datetime.now(timezone.utc).isoformat(),
        'spec_sha256': digest(spec), 'policy_sha256': digest(POLICY), 'case_sha256': spec['case_sha256'],
        'program_sha256': spec['program_sha256'], 'plan_sha256': spec['plan_sha256'],
        'result_sha256': spec['result_sha256'], 'record_head_sha256': spec['record_head_sha256'],
        'prior_audit_sha256': PRIOR_SHA256, 'code_pins': spec['code_pins'],
        'original_input_sha256_before': before, 'original_input_sha256_after': after,
        'original_artifacts_unchanged': True, 'original_final_outcome': 'abstain', 'original_final_call_id': FINAL,
        'candidate_promoted_or_reselected': False, 'same_frozen_rule_review': review,
        'source_market_files_reopened': False, 'new_model_calls': 0, 'new_strategy_executions': 0,
        'new_formal_slots_registered': 0, 'formal_success_denominator_contribution': 0,
        'formal_target_success': False,
        'limitations': ['Exposed 2019 candidate and same frozen simulation rules only; original trial abstention remains unchanged.',
            'Original audit SHA and file bindings establish this local saved custody, not authenticated external market or tax facts.']}


def save_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-spec')
    parser.add_argument('--spec')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.freeze_spec:
        need(args.spec is None and args.output is None, 'spec freeze is separate from audit')
        save_once(args.freeze_spec, build_spec())
    else:
        need(args.spec and args.output and not Path(args.output).exists(), 'frozen spec and new output required')
        save_once(args.output, audit_saved_candidate(read(args.spec)))


if __name__ == '__main__':
    main()
