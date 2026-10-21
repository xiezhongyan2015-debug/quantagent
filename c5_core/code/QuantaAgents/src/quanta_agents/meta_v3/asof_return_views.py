"""Bounded per-signal views of versioned prices and supplied corporate actions.

Generated receipts prove internal content binding only. This module has no real
source attestation authority, file/network access, account or study dispatcher.
Real qualification is always unavailable. The submitted inventory hash is kept
outside each view so a later-arriving version cannot rewrite an earlier view.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta, timezone
from decimal import Decimal, localcontext
import re

from . import causal_return_lineage as arithmetic
from .ledger import digest, need, serial

VERSION = 'per_signal_saved_return_versions_v1'
GENERATED = 'generated_engineering'
EXPOSED = 'caller_bound_exposed_development'
INDEX = '000905.SH'
CLOCK = '15:10:00+08:00'
COMMON = {'version_id', 'symbol', 'effective_at', 'observed_arrival_at',
          'arrival_basis', 'source_id', 'evidence_id'}
PRICE_FIELDS = COMMON | {'session', 'close', 'reference_previous_close'}
COVERAGE_FIELDS = COMMON | {'session', 'status', 'action_ids'}
ACTION_FIELDS = COMMON | (arithmetic.ACTION_FIELDS - {'available_at', 'availability_basis'})
RECEIPT_FIELDS = {'receipt_id', 'evidence_class', 'version_sha256', 'source_sha256',
                  'observed_arrival_at', 'arrival_basis'}


def _hash(value):
    arithmetic._source(value)


def _id(value):
    need(type(value) is str and 0 < len(value) <= 80 and
         re.fullmatch(r'[A-Za-z0-9_.:/-]+', value) is not None, 'bounded version/action identifier required')


def _time(value):
    return arithmetic._time(value, nullable=True)


def version_hash(version):
    return digest({k: v for k, v in version.items() if k != 'evidence_id'})


def bind_generated(version):
    """Return a copied version and generated content receipt, never real trust."""
    need(type(version) is dict and version.get('arrival_basis') == 'generated',
         'generated receipt helper requires explicit generated arrival basis')
    value = deepcopy(version)
    receipt = {'evidence_class': GENERATED, 'version_sha256': version_hash(value),
        'source_sha256': value['source_id'], 'observed_arrival_at': value['observed_arrival_at'],
        'arrival_basis': 'generated'}
    receipt['receipt_id'] = digest(receipt)
    value['evidence_id'] = receipt['receipt_id']
    return value, receipt


def _validate_version(row, fields, symbols, calendar_set, evidence_class):
    need(type(row) is dict and set(row) == fields, 'exact bounded version fields required')
    _id(row['version_id'])
    need(type(row['symbol']) is str and row['symbol'] in symbols, 'version outside original instrument set')
    _time(row['effective_at']); _time(row['observed_arrival_at'])
    need(row['arrival_basis'] in ('generated', 'unverified', 'declared_simulated'), 'unknown arrival basis')
    need(evidence_class == GENERATED or row['arrival_basis'] != 'generated',
         'generated timing cannot authenticate exposed inputs')
    _hash(row['source_id'])
    if row['evidence_id'] is not None:
        _hash(row['evidence_id'])
    if fields == PRICE_FIELDS:
        need(type(row['session']) is str and row['session'] in calendar_set, 'price version outside original calendar')
        arithmetic._money(row['close']); arithmetic._money(row['reference_previous_close'])
    elif fields == COVERAGE_FIELDS:
        need(type(row['session']) is str and row['session'] in calendar_set, 'coverage version outside original calendar')
        need(row['status'] in ('generated_complete', 'unknown', 'not_applicable_price_index'), 'unsupported coverage status')
        need(row['status'] != 'not_applicable_price_index' or row['symbol'] == INDEX, 'price-index exception cannot cover a stock')
        need(evidence_class == GENERATED or row['status'] != 'generated_complete', 'generated coverage cannot certify exposed inputs')
        ids = row['action_ids']
        need(type(ids) is list and len(ids) <= 16 and all(type(v) is str for v in ids) and len(set(ids)) == len(ids), 'bounded unique covered action identities required')
        for value in ids:
            _id(value)
    else:
        need(row['symbol'] != INDEX, 'company actions cannot be applied to the fixed price index')
        _id(row['action_id'])
        need(row['kind'] in ('cash', 'stock_distribution', 'unsupported'), 'unsupported action encoding')
        need(row['status'] in ('implementation', 'unresolved', 'withdrawn'), 'unsupported action status')
        need(type(row['basis']) is str and len(row['basis']) <= 80, 'bounded action basis required')
        for key in ('announcement_date', 'record_date', 'payment_date', 'listing_date'):
            arithmetic._day(row[key], nullable=True)
        arithmetic._day(row['ex_date'])
        need('2017-01-01' <= row['ex_date'] <= '2021-12-31', 'action outside exposed development range')
        arithmetic._money(row['share_increment_per_original_share'])
        arithmetic._money(row['gross_cash_per_original_share'])


def _receipt_table(receipts):
    need(type(receipts) is list and len(receipts) <= 20000, 'bounded evidence receipt list required')
    table = defaultdict(list)
    for row in receipts:
        need(type(row) is dict and set(row) == RECEIPT_FIELDS, 'exact evidence receipt fields required; caller verification flags are not authority')
        for field in ('receipt_id', 'version_sha256', 'source_sha256'):
            _hash(row[field])
        need(row['evidence_class'] in (GENERATED, EXPOSED), 'unknown evidence receipt class')
        need(row['arrival_basis'] in ('generated', 'unverified', 'declared_simulated'), 'unknown receipt timing basis')
        _time(row['observed_arrival_at'])
        table[row['receipt_id']].append(row)
    for versions in table.values():
        versions.sort(key=serial)
    return table


def _choose(versions, cutoff):
    """Return selected version, visible candidates and local ambiguity reasons."""
    visible = []
    for version in versions:
        available, effective = _time(version['observed_arrival_at']), _time(version['effective_at'])
        if (available is not None and available > cutoff) or (effective is not None and effective > cutoff):
            continue
        visible.append(version)
    # Canonical sorting makes input list order irrelevant, including conflicts.
    visible.sort(key=lambda v: (v['version_id'], version_hash(v), str(v['evidence_id'])))
    if not visible:
        return None, [], ['version_unavailable_at_signal']
    unknown = [v for v in visible if v['observed_arrival_at'] is None]
    if unknown:
        if len(visible) != 1:
            return None, visible, ['unordered_visible_versions']
        return unknown[0], visible, []
    latest = max(_time(v['observed_arrival_at']) for v in visible)
    current = [v for v in visible if _time(v['observed_arrival_at']) == latest]
    if len(current) != 1:
        return None, current, ['same_time_version_conflict']
    return current[0], current, []


def _evidence(version, receipts, evidence_class, cutoff, component):
    if version is None:
        return [], ['missing_' + component]
    failures = []
    effective, arrived = _time(version['effective_at']), _time(version['observed_arrival_at'])
    if effective is None or arrived is None:
        failures.append(component + '_time_unknown')
    elif effective > arrived or arrived > cutoff or effective > cutoff:
        failures.append(component + '_time_order_invalid')
    if 'session' in version and effective is not None and effective < _time(version['session'] + 'T15:00:00+08:00'):
        failures.append(component + '_effective_before_observation')
    if 'announcement_date' in version and effective is not None and version['announcement_date'] is not None:
        if effective < _time(version['announcement_date'] + 'T00:00:00+08:00'):
            failures.append(component + '_effective_before_announcement')
    if version['arrival_basis'] != 'generated':
        failures.append(component + '_arrival_not_authenticated')
    matched = receipts.get(version['evidence_id'], [])
    if len(matched) != 1:
        failures.append(component + '_evidence_missing_or_ambiguous')
    else:
        receipt = matched[0]
        payload = {k: v for k, v in receipt.items() if k != 'receipt_id'}
        if receipt['receipt_id'] != digest(payload) or receipt['version_sha256'] != version_hash(version) or \
                receipt['source_sha256'] != version['source_id'] or receipt['observed_arrival_at'] != version['observed_arrival_at'] or \
                receipt['arrival_basis'] != version['arrival_basis']:
            failures.append(component + '_evidence_content_mismatch')
        if evidence_class != GENERATED or receipt['evidence_class'] != GENERATED:
            failures.append(component + '_no_real_attestation_authority')
    return matched, failures


def _reference(version):
    if version is None:
        return None
    return {k: version[k] for k in ('version_id', 'source_id', 'evidence_id', 'effective_at', 'observed_arrival_at')} | \
        {'version_sha256': version_hash(version)}


def _row(day, previous_day, symbol, cutoff, prices, coverage, chosen_actions, receipts, evidence_class, enough_history):
    selected, candidates, blockers, evidence = {}, {}, [], {}
    for component, group in (('current_price', prices.get((day, symbol), [])),
                             ('previous_price', prices.get((previous_day, symbol), [])),
                             ('coverage', coverage.get((day, symbol), []))):
        chosen, causal, ambiguity = _choose(group, cutoff)
        selected[component], candidates[component] = chosen, causal
        blockers.extend(component + '_' + reason for reason in ambiguity)
        matched, failures = _evidence(chosen, receipts, evidence_class, cutoff, component)
        evidence[component] = matched; blockers.extend(failures)
    actions, ambiguous_actions = [], []
    for action_id, chosen, causal, ambiguity in chosen_actions.get(symbol, []):
        if chosen is not None:
            if chosen['ex_date'] == day:
                actions.append(chosen)
        elif any(v['ex_date'] == day for v in causal):
            ambiguous_actions.append({'action_id': action_id, 'versions': causal, 'reasons': ambiguity})
            blockers.extend('action_' + reason for reason in ambiguity)
    actions.sort(key=lambda v: (v['action_id'], v['version_id']))
    action_evidence = []
    for action in actions:
        matched, failures = _evidence(action, receipts, evidence_class, cutoff, 'action')
        action_evidence.extend(matched); blockers.extend(failures)
    evidence['actions'] = action_evidence
    cov = selected['coverage']
    if cov is not None:
        expected_status = 'not_applicable_price_index' if symbol == INDEX else 'generated_complete'
        if cov['status'] != expected_status:
            blockers.append('coverage_not_complete')
        expected_ids = {v['action_id'] for v in actions} | {v['action_id'] for v in ambiguous_actions}
        if set(cov['action_ids']) != expected_ids:
            blockers.append('coverage_action_inventory_mismatch')
    if not enough_history:
        blockers.append('insufficient_original_history')
    if evidence_class != GENERATED:
        blockers.append('real_source_attestation_unavailable')

    current, previous = selected['current_price'], selected['previous_price']
    close = arithmetic._money(current['close']) if current else None
    prior = arithmetic._money(previous['close']) if previous else None
    reference = arithmetic._money(current['reference_previous_close']) if current else None
    numerical_blockers = []
    if close is None or close <= 0:
        numerical_blockers.append('current_actual_close_unusable')
    if prior is None or prior <= 0:
        numerical_blockers.append('previous_actual_close_unusable_no_reference_substitution')
    if ambiguous_actions:
        numerical_blockers.append('ambiguous_action_entitlement')
    if len(actions) > 1:
        numerical_blockers.append('multiple_actions_on_one_ex_date')
    q, cash = Decimal(1), Decimal(0)
    if len(actions) == 1:
        action = actions[0]
        problem = 'withdrawn_action_requires_resolved_coverage' if action['status'] == 'withdrawn' else arithmetic._action_problem(action, previous_day)
        if problem:
            numerical_blockers.append(problem)
        else:
            q += arithmetic._money(action['share_increment_per_original_share'])
            cash = arithmetic._money(action['gross_cash_per_original_share'])
    blockers.extend(numerical_blockers)
    with localcontext() as context:
        context.prec = 50
        diagnostic = (q * close + cash) / prior - 1 if not numerical_blockers else None
        price_only = close / prior - 1 if close is not None and close > 0 and prior is not None and prior > 0 else None
        counterfactual = (q * close + cash) / reference - 1 if diagnostic is not None and reference is not None and reference > 0 else None
    all_selected = [v for v in selected.values() if v is not None] + actions
    times = [t for v in all_selected for t in (_time(v['effective_at']), _time(v['observed_arrival_at']))]
    maximum = max(times).astimezone(timezone(timedelta(hours=8))).isoformat() if times and all(t is not None for t in times) and \
        all(v is not None for v in selected.values()) and not ambiguous_actions else None
    blockers = sorted(set(blockers))
    inputs = {'version': VERSION, 'session': day, 'previous_session': previous_day, 'symbol': symbol,
              'selected': selected, 'causal_candidates': candidates, 'actions': actions,
              'ambiguous_actions': ambiguous_actions, 'evidence': evidence,
              'evidence_class': evidence_class, 'enough_history': enough_history}
    return {'session': day, 'symbol': symbol, 'previous_session': previous_day,
        'diagnostic_return': arithmetic._text(diagnostic),
        'generated_qualified_return': arithmetic._text(diagnostic) if evidence_class == GENERATED and not blockers else None,
        'qualified_return': None, 'maximum_available_at': maximum,
        'selected_versions': {**{k: _reference(v) for k, v in selected.items()}, 'actions': [_reference(v) for v in actions]},
        'blockers': blockers, 'inputs_sha256': digest(inputs),
        'formula_inputs': {'current_actual_close': arithmetic._text(close), 'previous_actual_close': arithmetic._text(prior),
            'reference_previous_close_not_used_as_actual': arithmetic._text(reference),
            'q_per_original_pre_ex_share': arithmetic._text(q) if not numerical_blockers else None,
            'gross_cash_per_original_pre_ex_share': arithmetic._text(cash) if not numerical_blockers else None},
        'raw_price_only_return': arithmetic._text(price_only),
        'reference_denominator_counterfactual': arithmetic._text(counterfactual)}


def build(*, calendar, symbols, signal_dates, price_versions, action_versions, coverage_versions,
          evidence_receipts, evidence_class):
    """Produce original 60-return windows at each original signal's cutoff.

    Short histories are retained explicitly, never filled or counted as complete.
    A historical return may use a revision that arrived by the later signal.
    No generated receipt, local hash or caller flag releases a real input.
    """
    need(evidence_class in (GENERATED, EXPOSED), 'unsupported view evidence class')
    need(type(calendar) is list and 3 <= len(calendar) <= 572, 'three to 572 original close sessions required')
    for day in calendar:
        arithmetic._day(day)
        need('2017-01-01' <= day <= '2021-12-31', 'sealed or non-development calendar refused before input traversal')
    need(calendar == sorted(set(calendar)), 'original calendar cannot reorder, duplicate or shrink internally')
    need(type(symbols) is list and 1 <= len(symbols) <= 17 and all(type(s) is str for s in symbols) and len(set(symbols)) == len(symbols) and
         all(s == INDEX or re.fullmatch(r'(?:sh[69]|sz[023])\d{5}', s) for s in symbols) and
         sum(s != INDEX for s in symbols) <= 16, 'bounded original stocks and fixed price-index identity required')
    need(type(signal_dates) is list and 1 <= len(signal_dates) <= 103 and all(type(d) is str for d in signal_dates) and signal_dates == sorted(set(signal_dates)) and
         all(d in calendar[1:] for d in signal_dates), 'bounded original signal coordinates required')
    calendar_set = set(calendar)
    groups = []
    for rows, fields, maximum in ((price_versions, PRICE_FIELDS, 20000), (coverage_versions, COVERAGE_FIELDS, 20000),
                                   (action_versions, ACTION_FIELDS, 1024)):
        need(type(rows) is list and len(rows) <= maximum, 'version inventory exceeds bound')
        grouped = defaultdict(list)
        for row in rows:
            _validate_version(row, fields, symbols, calendar_set, evidence_class)
            coordinate = (row['symbol'], row['action_id']) if fields == ACTION_FIELDS else (row['session'], row['symbol'])
            grouped[coordinate].append(row)
            need(len(grouped[coordinate]) <= 8, 'at most eight versions per coordinate')
        groups.append(grouped)
    prices, coverage, actions = groups
    receipt_table = _receipt_table(evidence_receipts)
    submitted = {'version': VERSION, 'calendar': calendar, 'symbols': symbols, 'signal_dates': signal_dates,
        'price_versions': price_versions, 'action_versions': action_versions, 'coverage_versions': coverage_versions,
        'evidence_receipts': evidence_receipts, 'evidence_class': evidence_class}
    need(len(serial(submitted).encode('utf-8')) <= 32 * 1024**2, 'submitted version inventory exceeds thirty-two MiB')
    views, row_count = [], 0
    for signal in signal_dates:
        index = calendar.index(signal)
        start = max(1, index - 59)
        window = calendar[start:index+1]
        cutoff_text = signal + 'T' + CLOCK
        cutoff = _time(cutoff_text)
        chosen_actions = defaultdict(list)
        for (symbol, action_id), versions in sorted(actions.items()):
            chosen, causal, ambiguity = _choose(versions, cutoff)
            chosen_actions[symbol].append((action_id, chosen, causal, ambiguity))
        rows = [_row(day, calendar[calendar.index(day)-1], symbol, cutoff, prices, coverage, chosen_actions,
                     receipt_table, evidence_class, index >= 60) for day in window for symbol in symbols]
        view = {'signal_date': signal, 'decision_cutoff': cutoff_text, 'window_sessions': window,
            'first_previous_session': calendar[start-1], 'required_return_sessions': 60,
            'available_return_sessions': len(window), 'status': 'complete_window' if index >= 60 else 'insufficient_history',
            'rows': rows, 'source_authenticated': False, 'formal_target_success': False}
        view['view_sha256'] = digest(view)
        views.append(view); row_count += len(rows)
    need(len(serial(views).encode('utf-8')) <= 64 * 1024**2, 'saved signal views exceed sixty-four MiB')
    return {'version': VERSION, 'evidence_class': evidence_class, 'submitted_input_sha256': digest(submitted),
        'views': views, 'views_sha256': digest(views),
        'work_units': {'price_versions': len(price_versions), 'action_versions': len(action_versions),
            'coverage_versions': len(coverage_versions), 'evidence_receipts': len(evidence_receipts),
            'signal_views': len(views), 'output_rows': row_count},
        'source_authenticated': False, 'formal_target_success': False}
