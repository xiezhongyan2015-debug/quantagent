"""Prospective model proposal intake, before any control-account outcome.

Uses the existing bounded research runtime and saved-completion verifier.
The proposal task has no execution actions. This does not yet implement a
three-arm performance comparison or unrestricted candidate generation.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import itertools
import json
from pathlib import Path
import time

from . import generator_controls as controls, research_batch
from .closing import ClosingPolicy
from .kernel import module
from .ledger import Ledger, digest, need, worker_lease
from .research_iteration import NEW_ACTIONS, executable_program_hash
from .research_tools import LIMITS, save_once
from .runtime import ResearchRuntime, source_pins

TASK = 'proposal'
BINDING = 'model_proposal_binding.json'
PHASE_ACTIONS = {'inspect_inputs', 'register_batch', 'inspect_batch', 'submit_research_report'}


def prepare(root, case, space, *, count, seed, idea, documents, policy, deadline_epoch):
    """Create an isolated proposal task; no model invocation or strategy run."""
    root = Path(root).resolve()
    need(not root.exists(), 'new prospective scope required')
    need(type(policy) is ClosingPolicy and 3 <= policy.task_calls == policy.stage_calls <= 6
         and policy.task_tokens == policy.stage_tokens <= 600000, 'bounded single-task proposal policy')
    need(type(idea) is str and 0 < len(idea) <= 4000 and type(documents) is list, 'ordinary input and documents required')
    need(len(documents) <= 8 and all(type(d) is dict and set(d) == {'name', 'text'} and
         type(d['name']) is str and type(d['text']) is str and len(d['text']) <= 20000 for d in documents), 'bounded normal documents')
    case = deepcopy(case)
    actions = dict.fromkeys(set(LIMITS) | set(NEW_ACTIONS), 0)
    actions.update(inspect_inputs=1, register_batch=1, inspect_batch=1)
    case['research_policy'] = {'version': 'structural_research_v1', 'action_limits': actions}
    cells = count * len(case['decision_fixture']['codes']) * len(case['decision_fixture']['calendar']) * (len(space['program_template']['factors']) + 2)
    # Tighten the same case for all methods; never expand the caller's grant.
    original = research_batch.policy(case)
    need(count <= original['max_candidates_total'] and cells <= original['max_scan_cells_total'], 'proposal opportunity exceeds original case budget')
    case['batch_policy'] = {**original, 'max_candidates_total': count, 'max_scan_cells_total': cells}
    controls.freeze(root/'controls', case, space, count=count, seed=seed, deadline_epoch=deadline_epoch,
        requires_model_proposal=True)
    control_plan = research_batch.read(root/'controls/plan.json')
    public = {'scope': 'prospective common-template parameter proposals, no executed results',
        'space': space, 'maximum_candidate_slots': count, 'reserved_scan_cells': cells,
        'instructions': [
            'Use register_batch to propose at most the reserved number of candidates in this exact common template and numeric domain. Keep template executable syntax; prose may differ.',
            'Use origin=model_intuition for your own proposal. Fixed/random candidates and their seeds or selected indices are not supplied.',
            'Do not claim tested performance. No execution, outcome diagnostics, extra source, candidate retry or budget extension is available.',
            'A final abstention is legal. Missing, duplicate, failed, invalid or outside-domain proposals remain counted; none receive free replacements.',
            'This is an engineering proposal stage, not a strategy success or full architecture comparison.']}
    task = {'idea': idea, 'documents': deepcopy(documents) + [{'name': 'Common proposal contract', 'text': json.dumps(public, ensure_ascii=False)}],
        'case': case, 'case_hash': digest(case)}
    link = {'proposal_root': str(root), 'control_plan_hash': digest(control_plan), 'case_hash': digest(case),
        'source_pins': source_pins(), 'phase': 'proposal_only_before_control_execution'}
    save_once(root/'controls'/BINDING, link)
    ledger = Ledger.create(root/'model_stage', policy=policy, tasks={TASK: task}, deadline_epoch=deadline_epoch,
        provenance={'source_pins': source_pins(), 'generator_proposal_binding': link,
            'old_v2_known_tokens': 491954, 'old_v2_unknown_reserve': 80000,
            'exposure': case['research_class'] + '; prospective proposal only, no performance claim',
            'formal_target_success': False})
    save_once(root/'admission.json', {'binding_hash': digest(link), 'control_plan_hash': digest(control_plan),
        'model_plan_hash': digest(research_batch.read(ledger.root/'plan.json')), 'policy': asdict(policy),
        'created_at': time.time(), 'no_control_outcomes_at_creation': True,
        'full_stack_comparison_admitted': False})
    ResearchRuntime(ledger.root).verify_inputs()
    return ledger


def verify(root):
    root = Path(root).resolve(); plan, frozen = controls.verify(root/'controls')
    need(plan['requires_model_proposal'] is True, 'prospective proposal admission required')
    binding = research_batch.read(root/'controls'/BINDING); admission = research_batch.read(root/'admission.json')
    model_plan = research_batch.read(root/'model_stage/plan.json')
    need(binding['proposal_root'] == str(root) and binding['control_plan_hash'] == digest(plan), 'prospective control binding drift')
    need(digest(binding) == admission['binding_hash'] and digest(model_plan) == admission['model_plan_hash'], 'proposal admission drift')
    need(model_plan['provenance']['generator_proposal_binding'] == binding and binding['case_hash'] == plan['case_hash'], 'proposal case binding drift')
    need(model_plan['tasks'][TASK]['case'] == plan['case'] and model_plan['policy'] == admission['policy'], 'shared case or proposal budget changed')
    ResearchRuntime(root/'model_stage').verify_inputs()
    return plan, frozen, model_plan


def run(root):
    """Invoke the existing Astra/xhigh bounded loop, with no outcome actions."""
    root = Path(root).resolve()
    verify(root)
    need(not (root/'controls/arms').exists(), 'control outcomes preceded model proposal')
    return ResearchRuntime(root/'model_stage').run()


def _domain(plan):
    space = plan['space']; names = space['axis_order']; hashes = {}
    for i, values in enumerate(itertools.product(*(space['parameters'][n] for n in names))):
        p = deepcopy(space['program_template']); params = dict(zip(names, values))
        sub = lambda e: research_batch.PLACEHOLDER.sub(lambda m: '('+json.dumps(params[m.group(1)], allow_nan=False)+')', e)
        for f in p['factors']: f['expression'] = sub(f['expression'])
        p['target_weight_expression'] = sub(p['target_weight_expression'])
        hashes.setdefault(executable_program_hash(p), []).append(i)
    return hashes


def _rebuild_receipt(root, plan, model_plan):
    """Replay saved completion evidence only; no writes or model invocation."""
    ledger = Ledger(root/'model_stage'); state = ledger.status(TASK)
    need(state['terminal'] == 'submitted' and state['unknown_or_pending_reserve'] == 0, 'proposal task must finish with all calls resolved')
    calls = [ledger.call(x['id']) for x in state['calls']]
    need(calls and all(c['status'] in ('applied', 'failed') and c['receipt'] is not None for c in calls), 'unsettled proposal history')
    history_hash = digest(calls)
    intent = {'model_plan_hash': digest(model_plan), 'control_plan_hash': digest(plan), 'call_history_hash': history_hash,
        'call_ids': [c['id'] for c in calls], 'registered_slots': plan['budget']['candidates_per_arm']}
    gateway = module('meta.codex_gateway'); proof_files = {}; verified = []; registered = []
    for call in calls:
        folder = ledger.root/'calls'/call['id']; requested = call['intent']
        receipt = gateway.verify_saved_completion(folder, expected_prompt_hash=requested['prompt_hash'],
            expected_schema=requested['schema'], expected_artifact_sha256=call['receipt']['artifact_sha256'])
        need(receipt['request_identity']['intent_id'] == call['id'] and
             requested['model'] == receipt['model'] == 'gpt-6-astra' and requested['effort'] == receipt['effort'] == 'xhigh', 'model request identity mismatch')
        need(all(receipt[k] == call['receipt'][k] for k in ('response', 'usage', 'artifact_sha256')), 'paid completion or usage drift')
        need(call['known_tokens'] == receipt['usage']['input_tokens'] + receipt['usage']['output_tokens'], 'recorded token total differs from completion usage')
        response = receipt['response']; action = response['action']
        need(action in PHASE_ACTIONS, 'outcome or extra research action before model proposal')
        for name, pin in receipt['artifact_sha256'].items(): proof_files[str(folder/name)] = pin
        verified.append({'call_id': call['id'], 'action': action, 'status': call['status'],
            'known_tokens': call['known_tokens'], 'usage': receipt['usage'], 'duration_seconds': receipt['duration_seconds'],
            'model_effort_confirmed_by_runtime_events': receipt['model_verified'],
            'identity_verification': receipt['identity_verification']})
        if action == 'register_batch' and call['status'] == 'applied':
            p = ledger.root/'tools'/TASK/call['id']/'artifact.json'; registration = research_batch.read(p)
            need(digest(registration) == call['result']['artifact_hash'] and registration['case_hash'] == plan['case_hash'], 'model registration identity drift')
            need(registration['source_pins'] == plan['source_pins'], 'model registration source drift')
            proof_files[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
            registered.append(registration)
    need(len(registered) <= 1, 'multiple proposal batches exceeded frozen opportunity')
    domain = _domain(plan); proposed = registered[0]['candidates'] if registered else []
    count = plan['budget']['candidates_per_arm']; need(len(proposed) <= count, 'model candidate slot budget exceeded')
    slots = []
    for i in range(count):
        row = proposed[i] if i < len(proposed) else None
        matches = domain.get(row['executable_program_hash'], []) if row else []
        status = 'not_proposed' if row is None else 'invalid' if row['validation_error'] else 'inside_domain' if matches else 'outside_domain'
        slots.append({'slot_id': f'm{i+1:03d}', 'proposal_status': status, 'source_candidate': row,
            'actual_origin': 'verified_model_response' if row else 'no_proposal',
            'matching_domain_indices': matches, 'reserved_scan_cells': plan['budget']['scan_cells_per_arm'] // count})
    result = {'kind': 'authenticated_generator_proposal_intake_v1', **intent, 'slots': slots,
        'verified_calls': verified, 'known_tokens': sum(x['known_tokens'] for x in verified),
        'unknown_model_reserve': 0, 'call_count': len(calls), 'proof_files': proof_files,
        'provider_model_effort_confirmed': all(x['model_effort_confirmed_by_runtime_events'] is True for x in verified),
        'local_completion_verified': True, 'provider_request_binding_independently_attested': False,
        'model_task_terminal': state['terminal'], 'full_stack_comparison_admitted': False,
        'model_candidate_execution_implemented': True, 'formal_target_success': False}
    need(result['known_tokens'] == state['known_tokens'], 'proposal cost ledger mismatch')
    return result


def _release(receipt, plan):
    return {'proposal_receipt_hash': digest(receipt), 'control_plan_hash': digest(plan),
        'allowed': receipt['provider_model_effort_confirmed'] is True,
        'scope': 'generated control execution only, no performance comparison'}


def seal(root):
    """Authenticate ALL paid calls and preserve all candidate slots."""
    root = Path(root).resolve()
    with worker_lease(root):
        plan, _, model_plan = verify(root)
        need(not (root/'controls/arms').exists(), 'control outcomes preceded proposal sealing')
        result = _rebuild_receipt(root, plan, model_plan)
        intent = {k: result[k] for k in ('model_plan_hash', 'control_plan_hash', 'call_history_hash', 'call_ids', 'registered_slots')}
        for name, value in [('intake_intent.json', intent), ('proposal_receipt.json', result),
                            ('control_release.json', _release(result, plan))]:
            path = root/name
            if path.exists(): need(digest(research_batch.read(path)) == digest(value), 'sealed proposal or control release drift')
            else: save_once(path, value)
        return result


def check_control_release(control_root):
    """Called by the shared control entry before any pending model arm is exposed."""
    control_root = Path(control_root).resolve()
    binding = research_batch.read(control_root/BINDING); root = Path(binding['proposal_root']).resolve()
    need(control_root == root/'controls', 'control release path mismatch')
    plan, _, model_plan = verify(root)
    need((root/'control_release.json').is_file(), 'model proposal must be sealed before control execution')
    release = research_batch.read(root/'control_release.json'); receipt = research_batch.read(root/'proposal_receipt.json')
    ledger = Ledger(root/'model_stage')
    calls = [ledger.call(x['id']) for x in ledger.status(TASK)['calls']]
    need(digest(calls) == receipt['call_history_hash'], 'model history changed after sealing')
    for p, pin in receipt['proof_files'].items():
        need(hashlib.sha256(Path(p).read_bytes()).hexdigest() == pin, 'model completion proof drift')
    rebuilt = _rebuild_receipt(root, plan, model_plan)
    need(digest(receipt) == digest(rebuilt), 'sealed model proposal differs from saved completion evidence')
    intent = {k: rebuilt[k] for k in ('model_plan_hash', 'control_plan_hash', 'call_history_hash', 'call_ids', 'registered_slots')}
    need(digest(research_batch.read(root/'intake_intent.json')) == digest(intent), 'proposal intake history changed')
    need(digest(release) == digest(_release(rebuilt, plan)) and
         rebuilt['provider_model_effort_confirmed'] is True, 'unverified model or changed control release')


def model_rows(control_root):
    """Use the sealed slots as-is; invalid/missing slots never gain replacements."""
    root = Path(research_batch.read(Path(control_root)/BINDING)['proposal_root'])
    receipt = research_batch.read(root/'proposal_receipt.json'); rows = []
    for slot in receipt['slots']:
        source = slot['source_candidate']; valid = slot['proposal_status'] == 'inside_domain'
        program = source['program'] if source else None
        rows.append({'candidate_id': f'c{len(rows)+1:03d}', 'family_id': 'model',
            'parameters': source['parameters'] if source else {}, 'program': program,
            'program_hash': digest(program), 'executable_program_hash': executable_program_hash(program) if valid else None,
            'validation_error': None if valid else 'sealed model slot: ' + slot['proposal_status'],
            'complexity': source['complexity'] if source else None,
            'reserved_expression_and_execution_scan_cells': slot['reserved_scan_cells'],
            'parameter_axis_count': source['parameter_axis_count'] if source else 0,
            'parameter_grid_size': source['parameter_grid_size'] if source else 0,
            'proposal_status': slot['proposal_status'], 'proposal_slot_id': slot['slot_id']})
    return rows


def run_model(root, *, execute_new=True):
    return controls.run_control(Path(root)/'controls', 'model', execute_new=execute_new)
