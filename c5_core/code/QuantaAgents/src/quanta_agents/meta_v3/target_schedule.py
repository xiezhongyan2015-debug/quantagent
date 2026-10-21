"""Explicit five-session target dispatch; cash accounts still run every day.

This adapter schedules controller-derived targets. It does not authenticate a
selector, input receipts, source coverage, or real execution. No source is read.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re

from .ledger import digest, need


VERSION = 'fixed_five_session_targets_v1'
KEY = 'target_schedule_policy'
FIELDS = {'version', 'interval_sessions', 'anchor_signal_dates', 'anchor_inputs',
          'off_anchor', 'unavailable_action', 'terminal'}


def validate_case_policy(case):
    """Validate declared metadata before any market/source byte is opened."""
    if KEY not in case:
        return None
    policy = case[KEY]
    need(type(policy) is dict and set(policy) == FIELDS, 'exact target schedule contract required')
    need(case.get('research_class') == 'real_saved_development'
         and case.get('execution_backend') in ('v3_streamed_001', 'v3_structural_001'),
         'target schedule requires explicit daily streamed or structural development backend')
    from .real_program import fixture_axes
    table = case['decision_fixture']
    need(table.get('kind') == 'exposed_real_decision_table', 'target schedule requires exposed daily development')
    _, days, _ = fixture_axes(table)
    need(policy['version'] == VERSION and type(policy['interval_sessions']) is int
         and policy['interval_sessions'] == 5, 'fixed five-session target schedule required')
    need(policy['anchor_signal_dates'] == days[::5], 'original calendar anchors cannot move or be dropped')
    need(policy['off_anchor'] == 'hold_actual_shares'
         and policy['unavailable_action'] == 'zero_targets_at_original_anchor'
         and policy['terminal'] == 'penultimate_signal_zero_next_session', 'target schedule action policy changed')
    rows = policy['anchor_inputs']
    need(type(rows) is list and len(rows) == len(days[::5]), 'complete original anchor input declarations required')
    for day, row in zip(days[::5], rows):
        need(type(row) is dict and set(row) == {'signal_date', 'status', 'reason', 'input_receipt_sha256'},
             'exact anchor input declaration required')
        need(row['signal_date'] == day and row['status'] in ('ready', 'unavailable'), 'anchor input identity/status changed')
        need((row['status'] == 'ready' and row['reason'] is None) or
             (row['status'] == 'unavailable' and type(row['reason']) is str and 0 < len(row['reason']) <= 1000),
             'unavailable common input requires its retained reason')
        need(type(row['input_receipt_sha256']) is str and re.fullmatch('[a-f0-9]{64}', row['input_receipt_sha256']),
             'anchor input receipt identity required')
    return policy


def public_contract(case):
    policy = validate_case_policy(case)
    if policy is None:
        return None
    return {'policy': deepcopy(policy), 'policy_sha256': digest(policy),
            'scope': 'Controller-fixed dispatch schedule; does not grant researcher-defined selectors.',
            'orders': 'Complete targets only at the original five-session anchors and mandatory terminal liquidation; other sessions have no target orders and retain actual shares.',
            'unavailable': 'Common input failure at an original anchor requests all cash; locked, rejected or partially filled positions remain in the actual account.',
            'accounting': 'Every original session remains in full-cash NAV, fees, taxes and execution accounting. Target cash never proves actual liquidation.',
            'input_receipt_authentication': False, 'source_arrival_verified': False,
            'formal_target_success': False}


def apply_schedule(target_artifact, *, case):
    """Schedule a complete ordinary target artifact without reading outcomes."""
    policy = validate_case_policy(case)
    if policy is None:
        return target_artifact
    from .real_program import POLICY
    table = case['decision_fixture']; days, codes = table['calendar'], table['codes']
    need(type(target_artifact) is dict and target_artifact.get('kind') == 'real_saved_strategy_target_artifact'
         and target_artifact.get('policy') == POLICY and target_artifact.get('decision_table_hash') == digest(table)
         and 'target_schedule' not in target_artifact, 'original complete target artifact binding required')
    targets = target_artifact.get('targets'); decisions = target_artifact.get('decisions')
    expected = [(days[i], days[i + 1], code) for i in range(len(days) - 1) for code in codes]
    need(type(targets) is list and len(targets) == len(expected)
         and type(decisions) is list and len(decisions) == len(expected), 'complete target and decision grids required')
    for (signal, trade, code), target, decision in zip(expected, targets, decisions):
        need(type(target) is dict and set(target) == {'symbol', 'signal_date', 'trade_date', 'available_at', 'target_weight'},
             'exact unscheduled target fields required')
        need((target['signal_date'], target['trade_date'], target['symbol']) == (signal, trade, code)
             and target['available_at'] == signal + 'T' + POLICY['decision_clock'], 'target schedule cannot change original clocks or axes')
        need(type(target['target_weight']) is str, 'target weight must preserve decimal text')
        try:
            weight = Decimal(target['target_weight'])
        except InvalidOperation:
            need(False, 'invalid original decimal target weight')
        need(weight.is_finite() and 0 <= weight <= 1, 'invalid original target weight')
        need(type(decision) is dict and all(decision.get(key) == value for key, value in target.items())
             and type(decision.get('reasons')) is list, 'target decision binding changed')
        if signal == days[-2]:
            need(weight == 0, 'original terminal liquidation must already be zero')
    for i in range(len(days) - 1):
        need(sum((Decimal(row['target_weight']) for row in targets[i * len(codes):(i + 1) * len(codes)]), Decimal(0)) <= 1,
             'original targets exceed full capital')
    result = deepcopy(target_artifact)
    result['targets'], result['decisions'] = [], []
    result['omitted_target_rows'], result['omitted_decisions'] = [], []
    anchors = {row['signal_date']: row for row in policy['anchor_inputs']}
    for target, decision in zip(targets, decisions):
        signal = target['signal_date']; anchor = anchors.get(signal)
        if anchor is None and signal != days[-2]:
            result['omitted_target_rows'].append(deepcopy(target))
            result['omitted_decisions'].append({**deepcopy(decision), 'schedule_reason': 'off_anchor_hold_actual_shares'})
            continue
        target, decision = deepcopy(target), deepcopy(decision)
        decision['schedule_original_target_weight'] = target['target_weight']
        if anchor is not None:
            decision['schedule_input_receipt_sha256'] = anchor['input_receipt_sha256']
            if anchor['status'] == 'unavailable':
                target['target_weight'] = decision['target_weight'] = '0'
                decision['reasons'].append('common_inputs_unavailable_at_original_anchor')
                decision['schedule_input_failure_reason'] = anchor['reason']
        if signal == days[-2]:
            target['target_weight'] = decision['target_weight'] = '0'
            decision['reasons'].append('mandatory_terminal_liquidation')
        result['targets'].append(target); result['decisions'].append(decision)
    result['target_schedule'] = {'version': VERSION, 'policy': deepcopy(policy), 'policy_sha256': digest(policy),
        'ordinary_target_artifact_sha256': digest(target_artifact), 'ordinary_target_rows': len(targets),
        'scheduled_target_rows': len(result['targets']), 'omitted_target_rows': len(result['omitted_target_rows']),
        'full_account_calendar_sha256': digest(days), 'initial_cash': case['initial_cash'],
        'input_receipt_authentication': False, 'source_arrival_verified': False,
        'selector_implemented_by_this_adapter': False, 'actual_liquidation_verified': False,
        'execution_valid': False, 'formal_target_success': False}
    return result
