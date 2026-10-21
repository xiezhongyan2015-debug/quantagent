"""Freeze a fresh, three-call evidence review using already admitted real inputs."""
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.controller_plan import VERSION, HARNESS_VERSION
from quanta_agents.meta_v3.ledger import Ledger, digest, serial
from quanta_agents.meta_v3.runtime import ResearchRuntime, source_pins, verify_case_sources


def main():
    case_path = ROOT / 'experiment_traces/meta_framework_v3/year_inputs_001/case.json'
    case = json.loads(case_path.read_text(encoding='utf-8'))
    case['report_policy'] = {'version': 'claim_support_v1'}
    task_id = 'source_review'
    question = ('Review the admitted 2019 single-stock saved-data case. Use the available '
        'input-evidence tools to identify which aspects of full-capital execution can be '
        'reproduced and which historical arrival, coverage, fee or capacity claims remain '
        'unsupported. Prioritize the most consequential unresolved evidence rather than '
        'listing everything. This is a fresh bounded evidence-review task using exposed '
        'historical inputs, not a new trading experiment or formal architecture comparison. '
        'Return a concise Chinese report with cited evidence and the frozen structured '
        'claims contract. Do not infer new OOS results from source hashes or declarations.')
    tasks = {task_id: {'idea': question, 'documents': [], 'case': case,
        'case_hash': digest(case), 'permitted_actions': ['inspect_inputs', 'read_evidence',
                                                        'submit_research_report']}}
    verify_case_sources(tasks[task_id])
    controller = {'version': VERSION, 'work': {task_id: {'question': question,
        'necessary_evidence': ['Saved coverage and execution assumption evidence with source IDs'],
        'depends_on': [], 'unlocks': 'Rank the next evidence acquisition by its execution-validation consequence',
        'stop_condition': 'At most two evidence reads then one model-authored report; stop on unresolved call or identity conflict'}}}
    job = Ledger.create(ROOT / 'experiment_traces/meta_framework_v4/account_research_trial_001',
        tasks=tasks, policy=ClosingPolicy(task_calls=3, stage_calls=3, task_tokens=240000,
            stage_tokens=240000, closing_seconds=60), deadline_epoch=time.time()+1800,
        provenance={'source_pins': source_pins(), 'controller_policy': controller,
            'harness_version': HARNESS_VERSION,
            'runtime_identity_route': {'version': 'codex_session_v1'},
            'exposure': 'Already exposed 2019 real_saved_development; no new market samples',
            'source_case_path': str(case_path), 'new_strategy_execution': False,
            'old_scope_resume_authorized': False, 'formal_architecture_comparison': False,
            'acceptance': {'runtime_identity': 'Each original local runtime session binds Astra/xhigh and completed output',
                'research_loop': 'Model reads actual admitted evidence and submits a cited report',
                'report_quality': 'Assess structured claims and prose separately; no automatic truth certification'},
            'prior_account_diagnostic': 'account_capture_001; 8691 known tokens counted separately',
            'provider_currency_cost': None, 'controller_currency_cost': None})
    runtime = ResearchRuntime(job.root)
    state = runtime.inspect_work()
    print(serial({'root': str(job.root), 'work_schedule': state['work_schedule']}))


if __name__ == '__main__':
    main()
