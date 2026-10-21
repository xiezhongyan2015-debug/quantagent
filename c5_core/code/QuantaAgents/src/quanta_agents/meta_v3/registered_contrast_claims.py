"""Bind report facts to original preregistered checks and saved account facts.

This is a check of a declared observation, never a verdict on its mechanism,
the surrounding prose, strategy selection or financial qualification.
"""
from collections import Counter
from decimal import Decimal, localcontext
import re

from . import experiment_contrast as ec
from .ledger import digest, need


STATES = ('matched', 'contradicted', 'unevaluable')
METRIC_UNITS = {
    'net_pnl': 'CNY', 'fees_on_recorded_trades': 'CNY',
    'recorded_slippage_in_prices': 'CNY',
    'turnover_multiple_of_initial_cash': 'multiple',
    'maximum_drawdown': 'fraction',
    'mean_marked_share_fraction_of_gross_assets': 'fraction',
    'all_cash_days': 'count', 'trade_count': 'count',
    'next_session_or_same_session_reentry_count': 'count',
}


def bound_results(saved, evidence, research_class):
    """Validate tool-owned public results against the separately cited original."""
    need(research_class in ('real_saved_development', 'synthetic_calibration'),
         'registered contrasts do not admit a new financial research class')
    need(saved.get('action') == 'develop_strategy', 'contrast must come from executed revision evidence')
    public = saved['public']; experiment = public['experiment']
    original = evidence[experiment['registration_evidence_id']]
    need(original['action'] == 'register_experiment', 'original registration must be cited')
    registration = original['public']; declaration = registration['declaration']
    result = experiment['declared_check_results']
    need(registration['kind'] == 'preregistered_revision_v1'
         and result['kind'] == ec.VERSION
         and result['registration_hash'] == digest(registration), 'original registration binding differs')
    checks = declaration['contrast_checks']
    ec.validate(checks, len(declaration['mechanism_hypotheses']))
    need(result['checks_hash'] == digest(checks), 'frozen check hash differs')
    need(result['causal_mechanism_identified'] is False and result['formal_target_success'] is False
         and experiment['formal_target_success'] is False, 'contrast cannot certify mechanism or financial success')
    baseline_id = declaration['baseline_evidence_id']
    need(experiment['baseline_evidence_id'] == baseline_id
         and result['account_evidence_ids'] == [baseline_id, 'registered_revision'], 'contrast account identities differ')
    comparison = experiment['comparison']; accounts = comparison['accounts']
    need(comparison['kind'] == 'saved_execution_attribution_v1' and len(accounts) == 2,
         'saved two-account attribution required')
    left, right = accounts
    need([a['evidence_id'] for a in accounts] == result['account_evidence_ids']
         and left['program_hash'] == registration['baseline_program_hash']
         and right['program_hash'] == registration['revision_program_hash'], 'saved account program binding differs')
    if 'program_hash' in public:
        need(public['program_hash'] == registration['revision_program_hash'], 'revision result program differs')
    pairs = comparison['pairs']
    need(len(pairs) == 1 and pairs[0]['reference_evidence_id'] == baseline_id
         and pairs[0]['comparison_evidence_id'] == 'registered_revision', 'saved account comparison differs')
    capital = ec._value(result['frozen_initial_cash'])
    if 'full_initial_cash' in public:
        need(capital is not None and capital == ec._value(public['full_initial_cash']),
             'contrast capital differs from the executed revision public result')
    calendar_hash = result['frozen_calendar_hash']
    calendar_hashes = result['account_calendar_hashes']
    need(type(calendar_hash) is str and re.fullmatch('[a-f0-9]{64}', calendar_hash)
         and type(calendar_hashes) is list and len(calendar_hashes) == 2
         and all(type(h) is str and re.fullmatch('[a-f0-9]{64}', h) for h in calendar_hashes),
         'original frozen and observed account calendar bindings required')
    aligned = (capital is not None and capital > 0
        and all(a['complete_account'] is True and ec._value(a['initial_cash']) == capital for a in accounts)
        and calendar_hashes == [calendar_hash, calendar_hash]
        and pairs[0]['same_cash_calendar_and_initial_capital'] is True)
    rows = result['checks']
    need(len(rows) == len(checks), 'declared check rows differ')
    for check, row in zip(checks, rows):
        need(all(row[k] == v for k, v in check.items())
             and row['unit'] == ec.METRICS[check['metric']], 'original check identity, threshold or unit differs')
        a, b = ec._value(left.get(check['metric'])), ec._value(right.get(check['metric']))
        need(ec._value(row['baseline_value']) == a and ec._value(row['revision_value']) == b,
             'check values differ from saved accounts')
        evaluable = aligned and a is not None and b is not None
        if not evaluable:
            need(row['status'] == 'unevaluable' and row['revision_minus_baseline'] is None
                 and type(row['reason']) is str and bool(row['reason']), 'incomplete comparison cannot match a prediction')
            continue
        with localcontext() as ctx:
            ctx.prec = 260
            delta = b - a
        status = 'matched' if ec.OPERATORS[check['operator']](delta, Decimal(check['threshold'])) else 'contradicted'
        need(ec._value(row['revision_minus_baseline']) == delta and row['status'] == status
             and row['reason'] is None, 'saved delta or frozen threshold judgment differs')
    counts = Counter(row['status'] for row in rows)
    need(result['counts'] == {k: counts[k] for k in STATES}, 'check status counts differ')
    return result


def numeric_unit(path, result):
    prefix = ['experiment', 'declared_check_results']
    if path[:2] != prefix:
        return None
    if len(path) == 4 and path[2] == 'counts' and path[3] in STATES:
        return 'count'
    if (len(path) == 5 and path[2] == 'checks' and type(path[3]) is int
            and 0 <= path[3] < len(result['checks'])
            and path[4] in ('baseline_value', 'revision_value', 'revision_minus_baseline', 'threshold')):
        unit = METRIC_UNITS[result['checks'][path[3]]['metric']]
        return 'fraction_difference' if unit == 'fraction' and path[4] in ('revision_minus_baseline', 'threshold') else unit
    return None
