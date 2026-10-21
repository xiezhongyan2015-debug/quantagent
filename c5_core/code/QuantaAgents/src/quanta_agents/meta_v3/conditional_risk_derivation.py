"""Inline generated-only derivation custody; never authenticates a real source.

No file/network reads occur here. The frozen runtime source pins bind this code.
The real adapter is an engineering stand-in, not a sixth real research case.
"""
from copy import deepcopy
from datetime import date
import math

from .conditional_risk import evaluate_window
from .ledger import digest, need, serial
from . import real_program, target_schedule


VERSION = 'generated_conditional_risk_derivation_v1'
ASOF_VERSION = 'generated_conditional_risk_derivation_v2'
KEY = 'conditional_risk_derivation'
FIELD = 'conditional_risk_target_weight'
CLASS = 'generated_engineering'
BUNDLE_FIELDS = {'version', 'evidence_class', 'original_calendar', 'symbols', 'market_rows', 'stock_rows'}
ASOF_BUNDLE_FIELDS = BUNDLE_FIELDS | {'asof_sources'}
ASOF_SOURCE_FIELDS = {'price_versions', 'action_versions', 'coverage_versions', 'evidence_receipts'}
ASOF_VIEW_FIELDS = {'signal_date', 'decision_cutoff', 'window_sessions', 'first_previous_session',
    'required_return_sessions', 'available_return_sessions', 'status',
    'rows', 'view_sha256', 'source_authenticated', 'formal_target_success'}
ASOF_ROW_FIELDS = {'session', 'symbol', 'previous_session', 'diagnostic_return', 'generated_qualified_return',
    'qualified_return', 'maximum_available_at', 'selected_versions', 'blockers', 'inputs_sha256',
    'formula_inputs', 'raw_price_only_return', 'reference_denominator_counterfactual'}
MARKET_FIELDS = {'session', 'previous_session', 'value', 'effective_at', 'available_at', 'source_status', 'source_evidence_id'}
STOCK_FIELDS = MARKET_FIELDS | {'symbol', 'return_basis', 'action_coverage_status', 'eligibility'}
ELIGIBILITY_FIELDS = {'eligible', 'effective_at', 'available_at', 'source_status', 'source_evidence_id'}
RECORD_FIELDS = {'version', 'evidence_class', 'target_field', 'bundle', 'bundle_sha256',
    'base_fixture_sha256', 'output_fixture_sha256', 'schedule_policy_sha256', 'numeric_policy_sha256',
    'anchor_receipts', 'anchor_receipts_sha256', 'source_authenticated', 'formal_target_success'}


def _enabled(case):
    if KEY in case:
        return True
    table = case.get('decision_fixture', {})
    reserved = any(type(row) is dict and row.get('name') == FIELD for row in table.get('fields', []))
    reserved = reserved or any(type(row) is dict and row.get('field') == FIELD for row in table.get('field_rows', []))
    source_policy = case.get('source_policy')
    declared = type(source_policy) is dict and source_policy.get('version') in (VERSION, ASOF_VERSION)
    need(not reserved and not declared, 'derived target field or policy cannot lose its derivation record')
    return False


def generated_row_id(row):
    """Bind generated row content only; this is not external source evidence."""
    need(type(row) is dict, 'generated row must be a dictionary')
    return digest({'evidence_class': CLASS, 'row': {k: v for k, v in row.items() if k != 'source_evidence_id'}})


def _time(value):
    if value is None:
        return None
    need(type(value) is str and len(value) <= 40, 'bounded nullable source timestamp required')
    return real_program.base._time(value)


def _row_metadata(row, fields):
    need(type(row) is dict and set(row) == fields, 'exact generated source row fields required')
    need(row['source_status'] in ('generated_known', 'unknown'), 'unsupported generated source status')
    effective, available = _time(row['effective_at']), _time(row['available_at'])
    need(effective is None or available is None or effective <= available, 'source arrival precedes effective time')
    for key in ('session', 'previous_session', 'symbol'):
        if key in row:
            need(type(row[key]) is str and len(row[key]) <= 24, 'bounded source row coordinate required')
    if 'value' in row:
        _value(row['value'])
    need(type(row['source_evidence_id']) is str and len(row['source_evidence_id']) == 64,
         'bounded generated row identity required')
    need(row['source_evidence_id'] == generated_row_id(row), 'generated row content hash changed')


def _header(case, bundle):
    # Reject real/sealed claims before inspecting inline observation values.
    need(case.get('research_class') == 'real_saved_development' and case.get('evidence_class') == CLASS,
         'conditional derivation supports generated engineering only; real source authentication is not implemented')
    need(case.get('execution_backend') in ('v3_streamed_001', 'v3_structural_001'), 'generated daily execution backend required')
    codes, days, _ = real_program.fixture_axes(case['decision_fixture'])
    need(type(bundle) is dict and bundle.get('version') in (VERSION, ASOF_VERSION), 'unsupported generated bundle version')
    fields = ASOF_BUNDLE_FIELDS if bundle['version'] == ASOF_VERSION else BUNDLE_FIELDS
    need(set(bundle) == fields, 'exact bounded inline generated bundle required')
    need(bundle['evidence_class'] == CLASS, 'real/unknown bundle class is not admitted')
    if bundle['version'] == ASOF_VERSION:
        sources = bundle['asof_sources']
        need(type(sources) is dict and set(sources) == ASOF_SOURCE_FIELDS and
             all(type(v) is list for v in sources.values()), 'exact bounded inline as-of sources required')
        need('000905.SH' not in codes, 'fixed market index must not replace a stock coordinate')
    calendar = bundle['original_calendar']
    need(type(calendar) is list and 63 <= len(calendar) <= 572 and calendar == sorted(set(calendar)),
         'bounded original calendar must preserve every session')
    need(all(type(d) is str and '2017-01-01' <= d <= '2021-12-31' and date.fromisoformat(d).isoformat() == d
             for d in calendar), 'sealed or non-development calendar rejected before observation processing')
    need(type(bundle['symbols']) is list and bundle['symbols'] == codes, 'original symbol order/set cannot change')
    need(days[0] in calendar, 'decision calendar absent from original calendar')
    start = calendar.index(days[0])
    need(start >= 60 and calendar[start:start + len(days)] == days, 'decision calendar must be one original contiguous segment with sixty prior closes')
    return codes, days, calendar


def _inputs(case, bundle):
    codes, days, calendar = _header(case, bundle)
    market_rows, stock_rows = bundle['market_rows'], bundle['stock_rows']
    need(type(market_rows) is list and len(market_rows) == len(calendar) - 1, 'complete original market-return grid required')
    need(type(stock_rows) is list and len(stock_rows) == (len(calendar) - 1) * len(codes), 'complete original stock-return grid required')
    market, stocks = {}, {}
    for index, row in enumerate(market_rows, 1):
        _row_metadata(row, MARKET_FIELDS)
        need((row['session'], row['previous_session']) == (calendar[index], calendar[index-1]), 'market return cannot skip an original session')
        _value(row['value'])
        if bundle['version'] == ASOF_VERSION:
            need(row['value'] is None and row['source_status'] == 'unknown', 'as-of market grid cannot supply fallback returns')
        market[row['session']] = row
    expected = [(calendar[i], calendar[i-1], code) for i in range(1, len(calendar)) for code in codes]
    for coordinate, row in zip(expected, stock_rows):
        need(type(row) is dict and set(row) == STOCK_FIELDS, 'exact generated stock row fields required')
        need(row['return_basis'] in ('generated_causal_total_economic_return', 'unverified_adjusted_price', 'unknown'), 'unsupported generated return basis')
        need(row['action_coverage_status'] in ('generated_complete', 'unknown'), 'unsupported action coverage declaration')
        eligibility = row['eligibility']
        need(type(eligibility) is dict and set(eligibility) == ELIGIBILITY_FIELDS, 'exact generated eligibility fields required')
        need(eligibility['eligible'] is None or type(eligibility['eligible']) is bool, 'nullable explicit eligibility required')
        _row_metadata(eligibility, ELIGIBILITY_FIELDS)
        _row_metadata(row, STOCK_FIELDS)
        if bundle['version'] == ASOF_VERSION:
            need(row['value'] is None and row['source_status'] == 'unknown' and
                 row['return_basis'] == 'unknown' and row['action_coverage_status'] == 'unknown',
                 'as-of stock grid cannot supply fallback returns or coverage')
        need((row['session'], row['previous_session'], row['symbol']) == coordinate, 'stock return cannot reorder, skip or replace original coordinates')
        stocks[(row['session'], row['symbol'])] = row
    need(len(serial(bundle).encode('utf-8')) <= 16 * 1024**2, 'generated bundle exceeds sixteen MiB')
    # This validates the ordinary grid, but its masked False eligibility is NOT
    # used as evidence of known ineligibility. Original rows are checked below.
    real_program.matrices(case['decision_fixture'])
    eligibility = {(r['session'], r['symbol']): r for r in case['decision_fixture']['eligibility_rows']}
    return codes, days, calendar, market, stocks, eligibility


def _value(value):
    need(value is None or _finite_number(value), 'finite JSON number or explicit null required')


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _availability(row, cutoff):
    if row.get('source_status') == 'unknown':
        return 'source_unknown'
    effective, available = _time(row['effective_at']), _time(row['available_at'])
    if effective is None or available is None:
        return 'source_time_unknown'
    if effective > cutoff or available > cutoff:
        return 'source_after_decision_cutoff'
    return None


def _anchor(day, codes, calendar, market, stocks, original_eligibility):
    index = calendar.index(day)
    window = calendar[index-59:index+1]
    cutoff_text = day + 'T' + real_program.POLICY['decision_clock']
    cutoff = _time(cutoff_text)
    failures = []
    def fail(coordinate, reason):
        failures.append({'coordinate': coordinate, 'reason': reason})
    m, returns, eligible = [], {code: [] for code in codes}, {}
    original_input = {'market': [], 'stocks': [], 'decision_eligibility': []}
    for session in window:
        row = market[session]; original_input['market'].append(row)
        reason = _availability(row, cutoff)
        if row['value'] is None:
            reason = reason or 'market_value_unknown'
        if reason:
            fail([session, 'market'], reason)
        m.append(None if reason else row['value'])
    for code in codes:
        known, signal_eligible = True, None
        for session in window:
            row = stocks[(session, code)]; original_input['stocks'].append(row)
            reason = _availability(row, cutoff)
            if row['action_coverage_status'] != 'generated_complete':
                reason = reason or 'action_coverage_unknown'
            if row['return_basis'] != 'generated_causal_total_economic_return':
                reason = reason or 'return_basis_unverified'
            if row['value'] is None:
                reason = reason or 'stock_return_unknown'
            if reason:
                fail([session, code, 'return'], reason)
            returns[code].append(None if reason else row['value'])
            eligibility = row['eligibility']
            eligibility_reason = _availability(eligibility, cutoff)
            if eligibility['eligible'] is None:
                eligibility_reason = eligibility_reason or 'eligibility_unknown'
            if eligibility_reason:
                fail([session, code, 'eligibility'], eligibility_reason); known = False
            if session == day:
                signal_eligible = eligibility['eligible']
        current = original_eligibility[(day, code)]
        original_input['decision_eligibility'].append(current)
        # Check the unmasked original receipt; a late True must stay unknown.
        reason = _availability(current, cutoff)
        if reason:
            fail([day, code, 'decision_eligibility'], reason); known = False
        elif known and signal_eligible != current['eligible']:
            fail([day, code, 'decision_eligibility'], 'conflicting_signal_eligibility_receipts'); known = False
        eligible[code] = signal_eligible if known else None
    measured = evaluate_window(m, returns, eligible=eligible, single_name_cap='0.08')
    need(not failures or measured['status'] == 'target_cash', 'unknown source cannot become a ready generated target')
    return {'signal_date': day, 'window_sessions': window, 'first_previous_session': calendar[index-60],
        'decision_cutoff': cutoff_text, 'input_rows_sha256': digest(original_input),
        'source_state': 'unavailable' if failures else 'generated_declared_complete', 'source_failures': failures,
        'measurement': measured, 'measurement_sha256': digest(measured),
        'source_authenticated': False, 'formal_target_success': False}


def _validate_asof_view(view, day, codes, calendar):
    """Check the saved view envelope without selecting versions or recomputing returns."""
    index = calendar.index(day)
    window = calendar[index-59:index+1]
    need(type(view) is dict and set(view) == ASOF_VIEW_FIELDS, 'exact as-of view fields required')
    need(view['signal_date'] == day and view['decision_cutoff'] == day + 'T' + real_program.POLICY['decision_clock'] and
         view['window_sessions'] == window and view['first_previous_session'] == calendar[index-60] and
         view['required_return_sessions'] == 60 and view['available_return_sessions'] == 60 and
         view['status'] == 'complete_window',
         'as-of view must preserve the original anchor and sixty-return window')
    need(view['source_authenticated'] is False and view['formal_target_success'] is False,
         'generated as-of view cannot certify a real source')
    rows = view['rows']
    expected = [(session, calendar[calendar.index(session)-1], code)
                for session in window for code in [*codes, '000905.SH']]
    need(type(rows) is list and len(rows) == len(expected), 'complete original as-of view grid required')
    cutoff = _time(view['decision_cutoff'])
    for row, coordinate in zip(rows, expected):
        need(type(row) is dict and set(row) == ASOF_ROW_FIELDS, 'exact as-of return row fields required')
        need((row['session'], row['previous_session'], row['symbol']) == coordinate,
             'as-of return view cannot reorder or drop original coordinates')
        need(row['qualified_return'] is None, 'generated view cannot contain a real qualified return')
        value = row['generated_qualified_return']
        need(value is None or type(value) is str, 'nullable decimal generated return required')
        available = _time(row['maximum_available_at'])
        if value is not None:
            need(available is not None and available <= cutoff and row['blockers'] == [],
                 'generated return must have known usable dependencies at its anchor')
            try:
                numeric = float(value)
            except (ValueError, OverflowError):
                need(False, 'finite generated as-of decimal required')
            _value(numeric)
    need(view['view_sha256'] == digest({k: v for k, v in view.items() if k != 'view_sha256'}),
         'as-of view hash changed')


def _asof_anchors(days, codes, calendar, market, stocks, eligibility, sources):
    # This opt-in stays generated-only. Neither a caller declaration nor a
    # diagnostic return can supply a risk input when the core withholds it.
    from . import asof_return_views
    result = asof_return_views.build(calendar=calendar, symbols=[*codes, '000905.SH'],
        signal_dates=days[::5], evidence_class=CLASS, **sources)
    views = result['views']
    need(type(views) is list and len(views) == len(days[::5]), 'complete original as-of anchors required')
    receipts = []
    for day, view in zip(days[::5], views):
        _validate_asof_view(view, day, codes, calendar)
        view_market, view_stocks = {}, {}
        for source in view['rows']:
            is_market = source['symbol'] == '000905.SH'
            coordinate = source['session'] if is_market else (source['session'], source['symbol'])
            row = deepcopy(market[coordinate] if is_market else stocks[coordinate])
            value = source['generated_qualified_return']
            row.update(value=None if value is None else float(value),
                effective_at=source['maximum_available_at'], available_at=source['maximum_available_at'],
                source_status='unknown' if value is None else 'generated_known')
            if not is_market:
                row.update(return_basis='unknown' if value is None else 'generated_causal_total_economic_return',
                    action_coverage_status='unknown' if value is None else 'generated_complete')
            row['source_evidence_id'] = generated_row_id(row)
            (view_market if is_market else view_stocks)[coordinate] = row
        receipt = _anchor(day, codes, calendar, view_market, view_stocks, eligibility)
        receipt.update(asof_view=deepcopy(view), asof_view_sha256=view['view_sha256'])
        receipts.append(receipt)
    return receipts


def build(case, bundle):
    """Return the three case fields; does not mutate case/bundle or read files.

    Market rows: MARKET_FIELDS; stock rows: STOCK_FIELDS, nested eligibility:
    ELIGIBILITY_FIELDS. Every row's ID is generated_row_id(row), with nested
    eligibility hashed first. Unknown values/times are explicit nulls.
    """
    need(KEY not in case and target_schedule.KEY not in case, 'do not replace an existing derivation or schedule')
    codes, days, calendar, market, stocks, eligibility = _inputs(case, bundle)
    original = case['decision_fixture']
    need(FIELD not in {f['name'] for f in original['fields']} and len(original['fields']) < 8,
         'derived field must be new and fit original bounded public field contract')
    receipts = (_asof_anchors(days, codes, calendar, market, stocks, eligibility, bundle['asof_sources'])
                if bundle['version'] == ASOF_VERSION else
                [_anchor(day, codes, calendar, market, stocks, eligibility) for day in days[::5]])
    by_day = {r['signal_date']: r for r in receipts}
    bundle_hash = digest(bundle)
    table = deepcopy(original)
    table['fields'].append({'name': FIELD, 'unit': 'fraction_of_full_initial_capital_generated_only'})
    latest_receipt = None
    for day in days:
        receipt = by_day.get(day)
        if receipt:
            latest_receipt = receipt
            row_id = digest(receipt)
        elif bundle['version'] == ASOF_VERSION:
            row_id = digest({'version': ASOF_VERSION, 'previous_anchor_receipt_sha256': digest(latest_receipt),
                'signal_date': day, 'action': 'off_anchor_hold_actual_shares'})
        else:
            row_id = digest({'version': VERSION, 'bundle_sha256': bundle_hash,
                'signal_date': day, 'action': 'off_anchor_hold_actual_shares'})
        weights = receipt['measurement']['target_weights'] if receipt else {}
        for code in codes:
            table['field_rows'].append({'session': day, 'symbol': code, 'field': FIELD,
                'value': float(weights.get(code, '0')), 'effective_at': day + 'T' + real_program.POLICY['decision_clock'],
                'available_at': day + 'T' + real_program.POLICY['decision_clock'], 'source_evidence_id': row_id})
    schedule = {'version': target_schedule.VERSION, 'interval_sessions': 5, 'anchor_signal_dates': days[::5],
        'anchor_inputs': [{'signal_date': r['signal_date'], 'status': 'ready' if r['measurement']['status'] == 'ready' else 'unavailable',
            'reason': None if r['measurement']['status'] == 'ready' else r['measurement']['reason'],
            'input_receipt_sha256': digest(r)} for r in receipts],
        'off_anchor': 'hold_actual_shares', 'unavailable_action': 'zero_targets_at_original_anchor',
        'terminal': 'penultimate_signal_zero_next_session'}
    record = {'version': bundle['version'], 'evidence_class': CLASS, 'target_field': FIELD, 'bundle': deepcopy(bundle),
        'bundle_sha256': bundle_hash, 'base_fixture_sha256': digest(original), 'output_fixture_sha256': digest(table),
        'schedule_policy_sha256': digest(schedule), 'numeric_policy_sha256': digest(receipts[0]['measurement']['policy']),
        'anchor_receipts': receipts, 'anchor_receipts_sha256': digest(receipts),
        'source_authenticated': False, 'formal_target_success': False}
    target_schedule.validate_case_policy({**case, 'decision_fixture': table, target_schedule.KEY: schedule})
    real_program.matrices(table)
    return {'decision_fixture': table, target_schedule.KEY: schedule, KEY: record}


def verify_case(case):
    """Recompute fixed generated outputs and custody; not real admission."""
    if not _enabled(case):
        return None
    validate_case_metadata(case)
    record = case[KEY]
    table = deepcopy(case['decision_fixture'])
    need(sum(f['name'] == FIELD for f in table['fields']) == 1, 'exactly one derived target field required')
    table['fields'] = [f for f in table['fields'] if f['name'] != FIELD]
    table['field_rows'] = [r for r in table['field_rows'] if r['field'] != FIELD]
    need(digest(table) == record['base_fixture_sha256'], 'original decision input changed')
    base_case = {k: v for k, v in case.items() if k not in (KEY, target_schedule.KEY)}
    base_case['decision_fixture'] = table
    rebuilt = build(base_case, record['bundle'])
    need(all(case.get(key) == value for key, value in rebuilt.items()), 'derived field, schedule or numerical receipt drift')
    receipts = record['anchor_receipts']
    units = {key: sum(r['measurement']['work_units'][key] for r in receipts)
             for key in receipts[0]['measurement']['work_units']}
    report = {'version': record['version'], 'evidence_class': CLASS, 'binding_verified': True,
        'numerical_recomputation_verified': True, 'anchor_evaluations': len(receipts), 'work_units': units,
        'source_authenticated': False, 'source_arrival_verified': False, 'action_coverage_verified': False,
        'real_research': False, 'formal_target_success': False}
    if record['version'] == ASOF_VERSION:
        sources = record['bundle']['asof_sources']
        report['version_view_work_units'] = {key: len(sources[key]) for key in ASOF_SOURCE_FIELDS}
        report['version_view_work_units'].update(signal_views=len(receipts),
            output_rows=sum(len(receipt['asof_view']['rows']) for receipt in receipts))
        report['version_view_resource_scope'] = 'Generated deterministic inventory/view counts only; not an admitted fair-study resource protocol.'
    return report


def validate_case_metadata(case):
    """Bounded inline structure/hash checks only; no numerical recomputation."""
    if not _enabled(case):
        return None
    record = case[KEY]
    need(type(record) is dict and set(record) == RECORD_FIELDS, 'exact conditional derivation record required')
    _header(case, record['bundle'])
    need(record['version'] == record['bundle']['version'] and record['evidence_class'] == CLASS and record['target_field'] == FIELD,
         'unsupported conditional derivation identity')
    need(record['source_authenticated'] is False and record['formal_target_success'] is False,
         'generated derivation cannot certify real evidence or financial success')
    # Reject giant/deep metadata before JSON serialization or hash traversal.
    stack, nodes = [(record, 0)], 0
    while stack:
        value, depth = stack.pop(); nodes += 1
        need(nodes <= 1000000 and depth <= 16, 'bounded derivation metadata required')
        if type(value) is dict:
            need(len(value) <= 256 and all(type(k) is str and len(k) <= 100 for k in value), 'bounded metadata mapping required')
            stack.extend((v, depth + 1) for v in value.values())
        elif type(value) is list:
            need(len(value) <= 20000, 'bounded metadata list required')
            stack.extend((v, depth + 1) for v in value)
        elif type(value) is str:
            need(len(value) <= 1000, 'bounded metadata text required')
        else:
            need(value is None or type(value) is bool or _finite_number(value),
                 'strict finite JSON metadata required')
    codes, days, _, _, _, _ = _inputs(case, record['bundle'])
    receipts = record['anchor_receipts']
    need(type(receipts) is list and len(receipts) == len(days[::5]), 'complete bounded original anchor receipts required')
    receipt_fields = {'signal_date', 'window_sessions', 'first_previous_session', 'decision_cutoff', 'input_rows_sha256',
        'source_state', 'source_failures', 'measurement', 'measurement_sha256', 'source_authenticated', 'formal_target_success'}
    if record['version'] == ASOF_VERSION:
        receipt_fields |= {'asof_view', 'asof_view_sha256'}
    for day, receipt in zip(days[::5], receipts):
        need(type(receipt) is dict and set(receipt) == receipt_fields and receipt['signal_date'] == day,
             'exact original anchor receipt identity required')
        need(receipt['source_authenticated'] is False and receipt['formal_target_success'] is False,
             'generated anchor cannot claim real authentication')
        need(receipt['measurement_sha256'] == digest(receipt['measurement']), 'saved numerical measurement hash changed')
        if record['version'] == ASOF_VERSION:
            _validate_asof_view(receipt['asof_view'], day, codes, record['bundle']['original_calendar'])
            need(receipt['asof_view_sha256'] == receipt['asof_view']['view_sha256'], 'anchor as-of view binding changed')
    need(len(serial(record).encode('utf-8')) <= 32 * 1024**2, 'generated derivation record exceeds thirty-two MiB')
    need(record['bundle_sha256'] == digest(record['bundle']) and record['anchor_receipts_sha256'] == digest(receipts),
         'conditional input or receipt hash changed')
    need(record['output_fixture_sha256'] == digest(case['decision_fixture']) and
         record['schedule_policy_sha256'] == digest(case.get(target_schedule.KEY)), 'derived field or schedule hash changed')
    return {'version': record['version'], 'evidence_class': CLASS, 'metadata_binding_verified': True,
        'metadata_nodes_checked': nodes, 'numerical_recomputation_verified': False,
        'source_authenticated': False, 'formal_target_success': False}


def public_contract(case):
    if not _enabled(case):
        return None
    report = verify_case(case)
    return {**report, 'target_field': FIELD,
        'scope': 'GENERATED ENGINEERING ONLY. Fixed controller selector; not an autonomous researcher selector or an admitted sixth real task.',
        'source_identity': 'Inline caller statuses, timestamps and row hashes prove only internal consistency. No real availability, adjustment or action coverage is authenticated.',
        'unknown': 'Unknown or late common input requests cash at its original anchor; actual holdings remain subject to execution.',
        'code_binding': 'Runtime frozen source pins; no source files are opened by this derivation.'}
