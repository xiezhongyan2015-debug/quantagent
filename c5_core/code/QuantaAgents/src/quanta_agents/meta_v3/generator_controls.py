"""Controller-owned fixed/random parameter search, with no outcome access.

This is a narrow shared-template search control, not unrestricted program
generation or evidence of model superiority. Only the explicit registered
real-fixed contract permits exposed development execution and selection.
"""
from copy import deepcopy
from decimal import Decimal, localcontext
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import time

from .kernel import module
from .ledger import digest, need, worker_lease
from . import research_batch
from .research_iteration import executable_program_hash

VERSION = 'independent_parameter_search_controls_v1'
ARMS = ('fixed', 'random')
SELECTION_VERSION = 'full_cash_rf2_development_selection_v1'


def selection_policy():
    return {'version': SELECTION_VERSION, 'metric': 'sharpe_rf2', 'minimum_strict': '0',
        'annualization': 252, 'sample_ddof': 1, 'annual_risk_free': '0.02',
        'tie_break': 'frozen_candidate_order', 'require_full_frozen_calendar': True,
        'require_funded_fills': True, 'require_all_candidates_settled': True}


def validate_selection(value):
    need(type(value) is dict and value == selection_policy() and digest(value) == digest(selection_policy()),
         'unknown frozen fixed development selection')


def validate_development_case(case):
    from .source_admission import preflight_case
    need(case['research_class'] == 'real_saved_development', 'fixed development requires exposed real-saved class')
    need(case.get('execution_backend') in ('v3_structural_001', 'v3_streamed_001'),
         'fixed development requires a supported complete daily cash backend')
    preflight_case(case)


def _space(space):
    need(type(space) is dict and set(space) == {'program_template', 'parameters', 'axis_order'}, 'exact shared search space')
    template, axes = space['program_template'], space['parameters']
    need(type(template) is dict and type(axes) is dict and 1 <= len(axes) <= 4, 'one to four numeric axes')
    order = space['axis_order']
    need(type(order) is list and len(order) == len(axes) and all(type(n) is str for n in order)
         and len(set(order)) == len(order) and set(order) == set(axes), 'explicit complete axis order')
    total = 1
    for name, values in axes.items():
        need(type(name) is str and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,39}', name), 'axis identity')
        need(type(values) is list and 1 <= len(values) <= 8, 'bounded axis values')
        need(all(type(v) in (int, float) and math.isfinite(v) for v in values), 'finite numeric axes only')
        total *= len(values)
    need(type(template.get('factors')) is list and len(template['factors']) <= 4, 'bounded factor list')
    expressions = [f['expression'] for f in template['factors']] + [template['target_weight_expression']]
    need(all(type(e) is str and len(e) <= 4096 for e in expressions), 'bounded template expressions')
    used = {m.group(1) for e in expressions for m in research_batch.PLACEHOLDER.finditer(e)}
    need(used == set(axes), 'every axis occurs only in controlled expressions')
    # Numeric values and insertion order are part of the search domain. Equal
    # values are retained as separate slots, never silently deduplicated.
    return total


def indices(space, count, seed, arm):
    total = _space(space)
    need(type(count) is int and 1 <= count <= min(32, total), 'candidate count exceeds domain')
    need(type(seed) is str and re.fullmatch('[a-f0-9]{64}', seed), 'explicit 256bit seed required')
    need(arm in ARMS, 'controller arm must be fixed or random')
    if arm == 'fixed':
        return [total // 2] if count == 1 else [i * (total - 1) // (count - 1) for i in range(count)]
    # A versioned hash permutation avoids dependence on Python's RNG version.
    # No sampling by validity, price, return, or prior arm performance.
    domain = digest(space)
    key = lambda i: (hashlib.sha256((VERSION + ':' + seed + ':' + domain + ':' + str(i)).encode()).digest(), i)
    return sorted(range(total), key=key)[:count]


def _rows(plan, arm):
    space, case = plan['space'], plan['case']
    axes = space['parameters']; names = space['axis_order']
    combinations = list(itertools.product(*(axes[n] for n in names)))
    chosen = indices(space, plan['budget']['candidates_per_arm'], plan['seed'], arm)
    if 'development_selection' in plan:
        from .real_program import validate_program as validate
    else:
        validate = module('meta.factor_strategy_program').validate_program
    stockdays = len(case['decision_fixture']['codes']) * len(case['decision_fixture']['calendar'])
    rows = []
    for index in chosen:
        values = dict(zip(names, combinations[index])); program = deepcopy(space['program_template'])
        substitute = lambda e: research_batch.PLACEHOLDER.sub(lambda m: '(' + json.dumps(values[m.group(1)], allow_nan=False) + ')', e)
        for factor in program['factors']:
            factor['expression'] = substitute(factor['expression'])
        program['target_weight_expression'] = substitute(program['target_weight_expression'])
        error = None; cost = None
        try:
            validate(program, public_field_contract=case['decision_fixture']['fields'])
            cost = research_batch.complexity(program)
        except (ValueError, TypeError, KeyError, SyntaxError) as exc:
            error = type(exc).__name__ + ': ' + str(exc)[:2000]
        rows.append({'candidate_id': f'c{len(rows)+1:03d}', 'family_id': arm, 'parameters': values,
            'domain_index': index, 'program': program, 'program_hash': digest(program),
            'executable_program_hash': executable_program_hash(program) if error is None else None,
            'validation_error': error, 'complexity': cost,
            'reserved_expression_and_execution_scan_cells': stockdays * (len(program['factors']) + 2),
            'parameter_axis_count': len(names), 'parameter_grid_size': len(combinations)})
    return rows


def _frozen_plan(case, space, *, count, seed, deadline_epoch, created_at, source_pins,
                 requires_model_proposal=False, only_arm=None, development_selection=None):
    """Derive every plan field from declared inputs; no candidate enumeration."""
    limits = research_batch.policy(case)
    total = _space(space)
    stockdays = len(case['decision_fixture']['codes']) * len(case['decision_fixture']['calendar'])
    cells = count * stockdays * (len(space['program_template']['factors']) + 2)
    plan = {'kind': VERSION, 'case': deepcopy(case), 'case_hash': digest(case),
        'requires_model_proposal': requires_model_proposal,
        'space': deepcopy(space), 'space_hash': digest(space), 'domain_size': total, 'seed': seed,
        'source_pins': deepcopy(source_pins), 'created_at': created_at, 'deadline_epoch': deadline_epoch,
        'budget': {'candidates_per_arm': count, 'scan_cells_per_arm': cells,
            'wall_seconds_per_arm': limits['max_wall_seconds'],
            'output_stop_threshold_bytes_per_arm': limits['output_stop_threshold_bytes']},
        'algorithms': {'fixed': 'evenly_spaced_mixed_radix_indices_in_declared_axis_order; singleton midpoint',
            'random': 'sha256_version_seed_domain_index_order_without_replacement'},
        'selection_protocol': 'No winner or selected strategy; retain every candidate in frozen arm order.',
        'model_arm_status': 'not_registered; no authentic model comparison admitted',
        'scope': 'shared_template_parameter_search_only', 'formal_target_success': False}
    if only_arm is not None:plan['materialized_arms']=[only_arm]
    if development_selection is not None:
        validate_selection(development_selection);validate_development_case(case)
        need(only_arm=='fixed' and not requires_model_proposal,'real development selection is fixed-only')
        plan.update(development_selection=deepcopy(development_selection),
            selection_protocol='Frozen full-calendar full-cash RF2 Sharpe > 0; all slots settled, funded fills, finite sample deviation; tie by original candidate order.',
            model_arm_status='separately_registered_architecture_trials; no claim of model superiority',
            scope='exposed_real_development_fixed_template_search_only')
    return plan


def freeze(root, case, space, *, count, seed, deadline_epoch, requires_model_proposal=False, only_arm=None, development_selection=None):
    """Freeze code/domain/budget BEFORE materializing either arm, no execution."""
    from .runtime import source_pins
    from .source_admission import preflight_case
    from .research_tools import save_once
    root = Path(root)
    need(not root.exists(), 'new controller scope required; cannot overwrite or reroll')
    need(type(requires_model_proposal) is bool, 'explicit control admission identity required')
    need(only_arm is None or only_arm in ARMS,'supported materialized control arm')
    need(only_arm is None or not requires_model_proposal,'proposal controls require their original shared arms')
    preflight_case(case); limits = research_batch.policy(case)
    indices(space, count, seed, 'fixed')
    stockdays = len(case['decision_fixture']['codes']) * len(case['decision_fixture']['calendar'])
    cells = count * stockdays * (len(space['program_template']['factors']) + 2)
    need(count <= limits['max_candidates_total'] and cells <= limits['max_scan_cells_total'], 'per-arm opportunity budget exceeded')
    need(type(deadline_epoch) in (int, float) and math.isfinite(deadline_epoch)
         and time.time() < deadline_epoch <= time.time() + 7200, 'bounded absolute scope deadline')
    plan = _frozen_plan(case,space,count=count,seed=seed,deadline_epoch=deadline_epoch,created_at=time.time(),
                        source_pins=source_pins(),requires_model_proposal=requires_model_proposal,only_arm=only_arm,
                        development_selection=development_selection)
    save_once(root / 'plan.json', plan)
    controls = _controls(plan)
    save_once(root / 'controls.json', controls)
    return controls


def _controls(plan):
    count = plan['budget']['candidates_per_arm']; cells = plan['budget']['scan_cells_per_arm']
    arms=plan.get('materialized_arms',list(ARMS))
    need(arms in (list(ARMS),['fixed'],['random']),'materialized control arms changed')
    return {'plan_hash': digest(plan), 'arms': {arm: _rows(plan, arm) for arm in arms},
        'reserved_candidates_total': count * len(arms), 'reserved_scan_cells_total': cells * len(arms),
        'new_model_calls': 0, 'generator_reads_outcomes': False,
        'enumerated_index_count_for_hash_order': plan['domain_size'], 'candidate_validation_count_at_freeze': count * len(arms),
        'all_arms_complete_for_model_comparison': False}


def verify(root):
    from .runtime import source_pins
    root = Path(root); plan = research_batch.read(root / 'plan.json'); controls = research_batch.read(root / 'controls.json')
    need(plan['kind'] == VERSION and controls['plan_hash'] == digest(plan), 'control plan identity drift')
    need(type(plan.get('requires_model_proposal')) is bool, 'control admission identity missing')
    binding_path = root / 'model_proposal_binding.json'
    need(binding_path.is_file() == plan['requires_model_proposal'], 'required model proposal binding missing or unexpected')
    if plan['requires_model_proposal']:
        binding = research_batch.read(binding_path)
        need(binding['control_plan_hash'] == digest(plan) and
             Path(binding['proposal_root']).resolve() / 'controls' == root.resolve(), 'proposal control identity drift')
    need(plan['source_pins'] == source_pins(), 'control generator or shared kernel source drift')
    need(digest(plan['case']) == plan['case_hash'] and digest(plan['space']) == plan['space_hash'], 'case or domain drift')
    need(controls == _controls(plan), 'generated candidate order or provenance drift')
    return plan, controls


def run_control(root, arm, *, execute_new=True):
    """Exercise each frozen control through the ordinary/batch shared kernel.

Only generated engineering cases may execute before the authentic model arm
is implemented. This guard also prevents later relabeling of exposed controls
as a prospective comparison. Separate arm roots prevent cross-arm cache gain.
"""
    from .research_tools import save_once
    from .runtime import verify_case_sources
    root = Path(root)
    with worker_lease(root):
        if execute_new:
            from .registered_producer import authorize_control
            authorize_control(root,arm)
        plan, controls = verify(root); case = plan['case']
        bound = plan['requires_model_proposal']
        need(arm in ARMS or (arm == 'model' and bound), 'unknown control arm')
        if bound:
            from .generator_proposal import check_control_release
            check_control_release(root)
        if 'development_selection' in plan:
            validate_selection(plan['development_selection']);validate_development_case(case)
            need(arm=='fixed' and not bound,'real development selection is fixed-only')
        else:
            need(case['research_class'] == 'synthetic_calibration', 'authentic model arm and prospective selection admission missing; real comparative execution closed')
        verify_case_sources({'case': case, 'case_hash': plan['case_hash']})
        execution_arms = ARMS + ('model',) if bound else tuple(plan.get('materialized_arms',ARMS))
        for name in execution_arms:
            research_batch.reconcile_registry(root / 'arms' / name / 'batches', plan['case_hash'], save_once)
        need(not execute_new or not any(research_batch.unresolved(root / 'arms' / name / 'batches') for name in execution_arms),
             'unresolved control worker blocks new scans in every arm; reconcile saved work only')
        if arm == 'model':
            from .generator_proposal import model_rows
            rows = model_rows(root)
        else:
            rows = controls['arms'][arm]
        budget = plan['budget']
        folder = root / 'arms' / arm / 'batches' / 'frozen'
        registration = {'kind': 'frozen_generator_control_batch_v1', 'case_hash': plan['case_hash'],
            'control_plan_hash': digest(plan), 'control_arm': arm, 'source_pins': plan['source_pins'],
            'initial_cash': case['initial_cash'], 'data_and_cost_contract_hash': plan['case_hash'],
            'policy': {**case['batch_policy'], 'max_candidates_total': budget['candidates_per_arm'],
                'max_scan_cells_total': budget['scan_cells_per_arm'], 'max_wall_seconds': budget['wall_seconds_per_arm'],
                'output_stop_threshold_bytes': budget['output_stop_threshold_bytes_per_arm']},
            'candidates': rows, 'reserved_candidates': len(rows), 'reserved_scan_cells': budget['scan_cells_per_arm'],
            'declaration': {'stop_rule': 'continue_settled_failures', 'selection_rule': plan['selection_protocol'],
                'comparisons': [], 'generator_arm': arm}}
        path = folder / 'registration.json'
        if path.exists():
            need(research_batch.read(path) == registration, 'control execution registration drift')
        else:
            need(execute_new, 'saved-only recovery requires an existing registration')
            save_once(path, registration)
        result = research_batch.run(folder, registration, case, save_once,
            deadline_epoch=plan['deadline_epoch'], source_pins=plan['source_pins'], execute_new=execute_new)
        if arm == 'model':
            for result_row, registered_row in zip(result['candidates'], rows):
                result_row['proposal_status'] = registered_row['proposal_status']
                result_row['proposal_slot_id'] = registered_row['proposal_slot_id']
        result.update({'control_arm': arm, 'control_plan_hash': digest(plan),
            'shared_executor': 'research_batch.run -> batch_worker -> program_execution.develop',
            'actual_project_model_calls': 0, 'model_comparison_admitted': False,
            'limitations': 'Generated accounting calibration only; no authentic model arm, winner, real strategy or OOS evidence.'})
        if 'development_selection' in plan:
            result['development_selection'] = select_development(folder, registration, case, result,
                plan['development_selection'])
            result['limitations'] = 'Exposed development in the frozen template space only; simulated complete accounts are not certified historical fills, capacity, OOS or open mechanism discovery.'
        # Each observation is append-only. The underlying intents prevent
        # restarting unknown workers or renewing a previously started deadline.
        reports = root / 'arms' / arm / 'observations'
        save_once(reports / f'{len(list(reports.glob("*.json"))) + 1:03d}.json', result)
        return result


def _number(value):
    number = Decimal(str(value))
    need(number.is_finite(), 'nonfinite fixed-search accounting value')
    return number


def development_metrics(body, case):
    """Full saved NAV calendar, including inactive days and initial capital.

    This is a mechanical development metric, not historical fill validation.
    Callers must first bind the body to its original reconciled raw ledger.
    """
    calendar = case['decision_fixture']['calendar']
    days = body['daily']; initial = _number(case['initial_cash'])
    need(initial > 0 and _number(body['initial_cash']) == initial, 'fixed-search full initial capital differs')
    need([r['date'] for r in days] == calendar and len(days) >= 2, 'fixed-search incomplete daily calendar')
    need(_number(body['final_snapshot']['external_cash_flow']) == initial, 'fixed-search external capital changed')
    with localcontext() as ctx:
        ctx.prec = 40
        previous = high = initial; maximum_dd = Decimal(0); returns = []
        for row in days:
            nav = _number(row.get('simulated_net_asset_value', row.get('valuation', {}).get('simulated_net_asset_value')))
            need(previous > 0 and nav >= 0, 'fixed-search undefined daily NAV return')
            returns.append(nav / previous - 1)
            high = max(high, nav); maximum_dd = max(maximum_dd, 1 - nav / high); previous = nav
        mean = sum(returns) / len(returns)
        std = (sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)).sqrt()
        rf = Decimal('1.02') ** (Decimal(1) / 252) - 1
        sharpe2 = (mean - rf) * Decimal(252).sqrt() / std if std else None
        sharpe0 = mean * Decimal(252).sqrt() / std if std else None
    fills = [(t.get('receipt', {}).get('filled_quantity', t.get('quantity', 0)),
              t.get('order', {}).get('side', t.get('side'))) for t in body.get('trades', [])]
    return {'initial_cash': str(initial), 'final_nav': str(previous), 'daily_return_count': len(returns),
        'return_on_full_initial_cash': str(previous / initial - 1), 'maximum_drawdown': str(maximum_dd),
        'sharpe_rf2': str(sharpe2) if sharpe2 is not None else None,
        'sharpe_rf0': str(sharpe0) if sharpe0 is not None else None,
        'daily_return_hash': digest([str(r) for r in returns]),
        'filled_trade_count': sum(quantity > 0 for quantity, _ in fills),
        'exit_fill_batches': sum(quantity > 0 and side == 'sell' for quantity, side in fills),
        'full_calendar_verified': True, 'formal_target_success': False}


def _reconciled_candidate(folder, registration, candidate_id, case, status):
    def no_write(*_):
        need(False, 'fixed selection requires existing candidate receipts; no replay execution')
    artifact = research_batch.artifact_for(folder, registration, candidate_id, no_write)
    if (artifact.get('raw') or {}).get('status') != 'completed_mechanical':
        need(status == 'reused', 'fixed candidate completion status differs from original artifact')
        return artifact, None, {'saved_raw_ledger_reconciled': False,
            'reason': 'reused_original_failure_is_not_an_eligible_account'}
    current = candidate_id; seen = set()
    while not (folder / 'candidates' / current / 'intent.json').exists():
        need(current not in seen, 'fixed-search reused candidate cycle')
        seen.add(current)
        disposition = research_batch.read(folder / 'candidates' / current / 'disposition.json')
        reference = disposition.get('reference', {})
        need(disposition['status'] == 'reused' and reference.get('batch_id') == folder.name,
             'fixed-search selection cannot import another arm or ordinary account')
        current = reference['candidate_id']
    raw_root = folder / 'candidates' / current / 'workbench/raw_children/program'
    raw_plan = research_batch.read(raw_root / 'plan.json')
    need(raw_plan['calendar'] == case['decision_fixture']['calendar'] and
         raw_plan['codes'] == case['decision_fixture']['codes'] and
         _number(raw_plan['initial_cash']) == _number(case['initial_cash']), 'fixed selection raw scope differs')
    if case['execution_backend'] == 'v3_structural_001':
        from . import structural_execution as backend
    else:
        from . import saved_execution as backend
    reconciled = backend.SavedRawResearch(raw_root).reconcile_saved_only(expected_plan_sha256=raw_plan['plan_sha256'])
    need(reconciled == artifact['raw'], 'fixed selection artifact differs from its saved raw ledger')
    need(reconciled['status'] == 'completed_mechanical' and reconciled.get('error') is None,
         'fixed selection requires a complete mechanical account')
    return artifact, reconciled['result'], {'raw_plan_sha256': raw_plan['plan_sha256'],
        'raw_result_hash': digest(reconciled), 'saved_raw_ledger_reconciled': True}


def select_development(folder, registration, case, result, policy):
    """Deterministic choice from every frozen candidate; never rerun or resample."""
    validate_selection(policy); validate_development_case(case)
    need(result['registration_hash'] == digest(registration), 'fixed selection registration differs')
    need([r['candidate_id'] for r in result['candidates']] ==
         [r['candidate_id'] for r in registration['candidates']], 'fixed selection omitted or reordered candidates')
    all_settled = all(r['status'] in ('completed', 'reused', 'failed', 'invalid') for r in result['candidates'])
    reviews = []; best = None; best_score = None
    for row, declared in zip(result['candidates'], registration['candidates']):
        review = {'candidate_id': row['candidate_id'], 'status': row['status'],
            'program_hash': declared['program_hash'], 'eligible': False}
        if row['status'] in ('completed', 'reused'):
            artifact, body, proof = _reconciled_candidate(folder, registration, row['candidate_id'], case, row['status'])
            if body is None:
                review.update(proof=proof, artifact_hash=digest(artifact), reason='reused_original_failure')
                reviews.append(review)
                continue
            metrics = development_metrics(body, case)
            score = _number(metrics['sharpe_rf2']) if metrics['sharpe_rf2'] is not None else None
            eligible = metrics['filled_trade_count'] > 0 and score is not None and score > 0
            review.update(metrics=metrics, proof=proof, artifact_hash=digest(artifact), eligible=eligible,
                reason='positive_finite_full_cash_rf2_with_fills' if eligible else 'no_fills_or_nonpositive_or_undefined_rf2')
            if eligible and (best_score is None or score > best_score):
                best, best_score = review, score
        else:
            review['reason'] = 'original_candidate_not_complete'
        reviews.append(review)
    selected = best if all_settled else None
    return {'kind': SELECTION_VERSION, 'policy': deepcopy(policy), 'all_candidates_settled': all_settled,
        'outcome': 'development_candidate' if selected is not None else 'abstain',
        'selected_candidate_id': selected['candidate_id'] if selected is not None else None,
        'selected_program_hash': selected['program_hash'] if selected is not None else None,
        'selected_metrics': selected['metrics'] if selected is not None else None,
        'candidate_reviews': reviews, 'selection_changes_candidate_records': False,
        'reason': 'highest_eligible_rf2_original_order_tie' if selected is not None else
            'unsettled_or_unstarted_frozen_candidate' if not all_settled else 'no_eligible_development_candidate',
        'model_final': False, 'formal_target_success': False, 'full_stack_comparison_admitted': False,
        'scope': 'Exposed development within one frozen template; not open mechanism discovery or formal strategy success.'}
