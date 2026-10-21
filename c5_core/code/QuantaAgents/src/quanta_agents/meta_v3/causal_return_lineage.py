"""Bounded saved-price/action arithmetic, without real-source certification.

No file reads, network, trading, or source discovery occur here. Diagnostics are
conditional on the supplied, visible saved-action scenario. Unknown coverage
never becomes evidence that no other action occurred. Qualified returns remain
unavailable until a separate real-source authentication protocol exists.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re
from types import MappingProxyType


VERSION = 'saved_causal_return_lineage_v1'
PRICE_FIELDS = {'session', 'symbol', 'close', 'reference_previous_close', 'source_id'}
COVERAGE_FIELDS = {'session', 'symbol', 'status', 'source_id'}
ACTION_FIELDS = {'action_id', 'symbol', 'kind', 'announcement_date', 'record_date', 'ex_date',
    'payment_date', 'listing_date', 'available_at', 'availability_basis',
    'share_increment_per_original_share', 'gross_cash_per_original_share', 'basis', 'status', 'source_id'}
_FIXED_POLICY = MappingProxyType({'version': VERSION, 'maximum_symbols': 16, 'maximum_sessions': 512,
    'maximum_action_versions': 256, 'maximum_decimal': '1000000000000', 'maximum_decimal_places': 12,
    'arithmetic_precision': 50, 'formula': '(q * current_actual_close + gross_cash) / previous_actual_close - 1',
    'action_basis': 'per_original_pre_ex_share', 'scenario': 'supplied_visible_saved_actions_only',
    'qualification': 'unavailable_no_real_source_authentication_protocol'})
POLICY = dict(_FIXED_POLICY)


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _day(value, *, nullable=False):
    if value is None and nullable:
        return None
    _need(type(value) is str and len(value) == 10, 'ISO calendar date required')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('invalid ISO calendar date') from exc
    _need(parsed.isoformat() == value, 'canonical calendar date required')
    return value


def _time(value, *, nullable=False):
    if value is None and nullable:
        return None
    _need(type(value) is str and len(value) <= 40, 'bounded timezone-aware timestamp required')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, OverflowError) as exc:
        raise ValueError('invalid timestamp') from exc
    _need(result.tzinfo is not None and result.utcoffset() is not None, 'timestamp timezone required')
    return result


def _money(value):
    if value is None:
        return None
    _need(type(value) is str and 0 < len(value) <= 64, 'bounded decimal text or null required')
    _need(re.fullmatch(r'(?:0|[1-9]\d*)(?:\.\d{1,12})?', value) is not None, 'nonnegative plain decimal text with at most twelve places required')
    try:
        number = Decimal(value)
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise ValueError('invalid bounded decimal') from exc
    _need(number.is_finite() and 0 <= number <= Decimal('1000000000000'), 'decimal exceeds nonnegative input bound')
    return number


def _text(value):
    if value is None:
        return None
    if value == 0:
        return '0'
    return format(value, 'f').rstrip('0').rstrip('.') if value.as_tuple().exponent < 0 else format(value, 'f')


def _source(value):
    _need(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None, 'source identity must be a SHA-256-shaped string; it is not authentication')


def _price(row, symbols):
    _need(type(row) is dict and set(row) == PRICE_FIELDS, 'exact price row fields required')
    _day(row['session'])
    _need(row['symbol'] in symbols, 'price symbol outside original universe')
    _money(row['close']); _money(row['reference_previous_close']); _source(row['source_id'])


def _actions(actions, symbols):
    _need(type(actions) is list and len(actions) <= 256, 'bounded action-version list required')
    for action in actions:
        _need(type(action) is dict and set(action) == ACTION_FIELDS, 'exact action-version fields required')
        _need(type(action['action_id']) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,80}', action['action_id']) is not None,
              'bounded action identity required')
        _need(action['symbol'] in symbols, 'action outside original universe')
        _need(action['kind'] in ('cash', 'stock_distribution', 'unsupported'), 'unknown action kind')
        _need(action['status'] in ('implementation', 'unresolved'), 'unknown action status')
        _need(action['availability_basis'] in ('declared_simulated', 'unverified', 'generated'), 'unknown availability basis')
        _need(type(action['basis']) is str and 0 < len(action['basis']) <= 80, 'bounded action basis required')
        for key in ('announcement_date', 'record_date', 'payment_date', 'listing_date'):
            _day(action[key], nullable=True)
        _day(action['ex_date'])
        _time(action['available_at'], nullable=True)
        _money(action['share_increment_per_original_share']); _money(action['gross_cash_per_original_share'])
        _source(action['source_id'])


def _visible(actions, cutoff):
    # Unknown-time and known-time versions cannot silently overrule each other.
    groups = {}
    future_count = 0
    for action in actions:
        available = _time(action['available_at'], nullable=True)
        if available is not None and available > cutoff:
            future_count += 1
            continue
        key = (action['symbol'], action['action_id'])
        groups.setdefault(key, []).append(action)
    selected, conflicts = [], []
    for key in sorted(groups):
        versions = groups[key]
        unknown = [a for a in versions if a['available_at'] is None]
        ordered = [a for a in versions if a['available_at'] is not None]
        if unknown and (ordered or len(unknown) > 1):
            conflicts.extend({'action': a, 'reason': 'unordered_action_versions'} for a in versions)
        elif unknown:
            selected.append(unknown[0])
        else:
            latest = max(_time(a['available_at']) for a in ordered)
            current = [a for a in ordered if _time(a['available_at']) == latest]
            if len(current) != 1:
                conflicts.extend({'action': a, 'reason': 'same_time_action_version_ambiguity'} for a in current)
            else:
                selected.append(current[0])
    selected.sort(key=lambda a: (a['symbol'], a['ex_date'], a['action_id']))
    conflicts.sort(key=lambda x: (x['action']['symbol'], x['action']['ex_date'], x['action']['action_id'], x['reason']))
    return selected, conflicts, future_count


def _action_problem(action, previous_day):
    if action['kind'] == 'unsupported':
        return 'unsupported_action_kind'
    if action['status'] != 'implementation':
        return 'unresolved_action'
    if action['basis'] != 'per_original_pre_ex_share':
        return 'unsupported_action_basis'
    if action['record_date'] is None or action['record_date'] != previous_day:
        return 'record_date_not_previous_original_session'
    if action['announcement_date'] is None or action['announcement_date'] > action['record_date']:
        return 'unresolved_announcement_chronology'
    if any(action[key] is not None and action[key] < action['ex_date'] for key in ('payment_date', 'listing_date')):
        return 'payment_or_listing_before_ex_date'
    increment = _money(action['share_increment_per_original_share'])
    cash = _money(action['gross_cash_per_original_share'])
    if increment is None or cash is None:
        return 'action_terms_unknown'
    if action['kind'] == 'cash' and increment != 0:
        return 'cash_action_has_stock_increment'
    return None


def analyze(*, calendar, symbols, prices, actions, coverage, input_kind, as_of, previous_anchors=None):
    """Analyze complete saved inputs, preserving every original coordinate.

    Action visibility is retrospective at one audit cutoff. Future versions do
    not change the effective input hash or existing rows. submitted_input_sha256
    separately binds all submitted versions, including those not yet visible.
    """
    _need(input_kind in ('generated_engineering', 'caller_bound_exposed_development'), 'input kind not admitted')
    _need(type(symbols) is list and 1 <= len(symbols) <= 16 and
          all(type(s) is str and re.fullmatch(r'(?:sh[69]|sz[023])\d{5}', s) for s in symbols) and
          len(set(symbols)) == len(symbols), 'one to sixteen original A-share symbols required')
    _need(type(calendar) is list and 3 <= len(calendar) <= 512, 'three to 512 original sessions required')
    for day in calendar:
        _day(day)
        _need('2017-01-01' <= day <= '2021-12-31', 'sealed or non-development calendar refused before input traversal')
    _need(calendar == sorted(set(calendar)), 'original calendar must be unique and ordered')
    cutoff = _time(as_of)
    market_date = cutoff.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    _need(calendar[-1] <= market_date, 'audit cutoff precedes requested observation calendar')
    anchors = [] if previous_anchors is None else previous_anchors
    _need(type(anchors) is list and len(anchors) <= len(symbols), 'at most one explicit previous anchor per symbol')
    expected = [(day, symbol) for day in calendar for symbol in symbols]
    _need(type(prices) is list and len(prices) == len(expected), 'complete original price grid required')
    _need(type(coverage) is list and len(coverage) == len(expected), 'complete original coverage grid required')
    price_map, coverage_map, anchor_map = {}, {}, {}
    for coordinate, row in zip(expected, prices):
        _price(row, symbols)
        _need((row['session'], row['symbol']) == coordinate, 'price rows cannot skip, duplicate, reorder or replace original coordinates')
        price_map[coordinate] = row
    for coordinate, row in zip(expected, coverage):
        _need(type(row) is dict and set(row) == COVERAGE_FIELDS, 'exact coverage row fields required')
        _need((row['session'], row['symbol']) == coordinate, 'coverage rows cannot skip, duplicate, reorder or replace original coordinates')
        _need(row['status'] in ('declared_complete', 'unknown', 'generated_complete'), 'unknown coverage status')
        _need(input_kind == 'generated_engineering' or row['status'] != 'generated_complete', 'generated coverage cannot certify exposed source inputs')
        _source(row['source_id']); coverage_map[coordinate] = row
    for row in anchors:
        _price(row, symbols)
        _need('2017-01-01' <= row['session'] < calendar[0], 'previous anchor must be explicit exposed pre-calendar price')
        _need(row['symbol'] not in anchor_map, 'duplicate previous anchor for original symbol')
        anchor_map[row['symbol']] = row
    _actions(actions, symbols)
    selected, conflicts, future_count = _visible(actions, cutoff)
    # Full physical input identity remains distinct from effective visible input.
    submitted = {'version': VERSION, 'calendar': calendar, 'symbols': symbols, 'prices': prices,
        'actions': actions, 'coverage': coverage, 'input_kind': input_kind, 'as_of': as_of, 'previous_anchors': anchors}
    effective = {**submitted, 'actions': selected, 'ambiguous_action_versions': conflicts}
    rows, arithmetic_rows = [], 0
    with localcontext() as context:
        context.prec = 50
        for index, day in enumerate(calendar):
            for symbol in symbols:
                current = price_map[(day, symbol)]
                previous = price_map[(calendar[index-1], symbol)] if index else anchor_map.get(symbol)
                previous_day = previous['session'] if previous else None
                cov = coverage_map[(day, symbol)]
                applicable = [a for a in selected if a['symbol'] == symbol and a['ex_date'] == day]
                ambiguous = [x for x in conflicts if x['action']['symbol'] == symbol and x['action']['ex_date'] == day]
                local_inputs = {'current': current, 'previous': previous, 'actions': applicable,
                    'ambiguous_versions': ambiguous, 'coverage': cov, 'policy_version': VERSION}
                reasons = ['diagnostic_conditional_on_supplied_visible_actions', 'byte_origin_unverified',
                    'historical_arrival_unverified', 'action_coverage_not_independently_authenticated']
                if cov['status'] == 'unknown':
                    reasons.append('action_coverage_unknown_no_absence_claim')
                blocked = []
                if previous is None:
                    blocked.append('actual_previous_close_missing_no_reference_substitution')
                current_close = _money(current['close'])
                previous_close = _money(previous['close']) if previous else None
                reference = _money(current['reference_previous_close'])
                if current_close is None or current_close <= 0:
                    blocked.append('current_actual_close_unusable')
                if previous is not None and (previous_close is None or previous_close <= 0):
                    blocked.append('previous_actual_close_unusable')
                if ambiguous:
                    blocked.extend(sorted({x['reason'] for x in ambiguous}))
                if len(applicable) > 1:
                    blocked.append('multiple_actions_on_one_ex_date')
                q, cash = Decimal(1), Decimal(0)
                if len(applicable) == 1:
                    action = applicable[0]
                    problem = _action_problem(action, previous_day)
                    if problem:
                        blocked.append(problem)
                    else:
                        q += _money(action['share_increment_per_original_share'])
                        cash = _money(action['gross_cash_per_original_share'])
                    if action['available_at'] is None:
                        reasons.append('unknown_action_arrival_retrospective_scenario_only')
                    if action['availability_basis'] == 'declared_simulated':
                        reasons.append('action_availability_is_declared_simulation')
                price_only = current_close / previous_close - 1 if current_close is not None and current_close > 0 and previous_close is not None and previous_close > 0 else None
                delta = reference - previous_close if reference is not None and previous_close is not None else None
                diagnostic, counterfactual = None, None
                if not blocked:
                    numerator = q * current_close + cash
                    diagnostic = numerator / previous_close - 1
                    counterfactual = numerator / reference - 1 if reference is not None and reference > 0 else None
                    arithmetic_rows += 1
                formula_inputs = {'scenario': 'supplied_visible_saved_actions_only', 'q_per_original_pre_ex_share': _text(q) if not blocked else None,
                    'gross_cash_per_original_pre_ex_share': _text(cash) if not blocked else None,
                    'current_actual_close': _text(current_close), 'previous_actual_close': _text(previous_close),
                    'reference_previous_close_not_used_as_actual': _text(reference)}
                action_refs = [{'action_id': a['action_id'], 'source_id': a['source_id'], 'available_at': a['available_at']}
                    for a in applicable]
                action_refs += [{'action_id': x['action']['action_id'], 'source_id': x['action']['source_id'],
                    'available_at': x['action']['available_at'], 'ambiguity': x['reason']} for x in ambiguous]
                rows.append({'session': day, 'symbol': symbol, 'previous_session': previous_day,
                    'price_refs': {'current': {'session': day, 'source_id': current['source_id']},
                        'previous': {'session': previous_day, 'source_id': previous['source_id']} if previous else None},
                    'action_refs': action_refs, 'coverage_ref': {'source_id': cov['source_id'], 'status': cov['status']},
                    'formula_inputs': formula_inputs, 'inputs_sha256': _hash(local_inputs),
                    'diagnostic_return': _text(diagnostic), 'qualified_return': None,
                    'status': 'not_evaluable' if blocked else 'diagnostic_only', 'reasons': reasons + blocked,
                    'reference_previous_close_delta': _text(delta), 'raw_price_only_return': _text(price_only),
                    'reference_denominator_counterfactual': _text(counterfactual)})
    return {'version': VERSION, 'input_kind': input_kind, 'as_of': as_of, 'policy': dict(_FIXED_POLICY),
        'input_sha256': _hash(effective), 'submitted_input_sha256': _hash(submitted), 'rows': rows,
        'rows_sha256': _hash(rows), 'work_units': {'input_price_rows': len(prices), 'input_action_versions': len(actions),
            'input_coverage_rows': len(coverage), 'previous_anchor_rows': len(anchors), 'output_rows': len(rows),
            'arithmetic_rows': arithmetic_rows, 'selected_action_versions': len(selected),
            'ambiguous_action_versions': len(conflicts), 'future_action_versions_excluded': future_count},
        'byte_origin_authenticated': False, 'source_arrival_verified': False, 'action_coverage_verified': False,
        'signal_admissible': False, 'formal_target_success': False,
        'interpretation': 'Retrospective numerical lineage conditional on supplied visible saved actions. Hashes and caller metadata do not authenticate raw origin, historical arrival or absence of other actions. No qualified causal signal is released.'}
