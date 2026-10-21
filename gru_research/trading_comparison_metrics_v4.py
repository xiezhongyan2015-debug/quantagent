"""Frozen comparison arithmetic with independent factual/instrument gates."""
from copy import deepcopy
from trading_comparison_metrics import (compare_run as v2_compare_run,
    reference_exposures as v2_reference_exposures, day, WINDOWS)
from primary_reference_inputs_v4 import PrimaryInputCatalogV4


def checked_catalog(catalog):
    if type(catalog) is not PrimaryInputCatalogV4 or not catalog.reference_inputs.verified:
        raise ValueError('V4 gate requires verified independent input catalog')
    return catalog


def reference_resolution_audit(result, checks, *, catalog):
    checked_catalog(catalog)
    resolved, unresolved = [], []
    for row in v2_reference_exposures(result, checks):
        proof = catalog.reference_resolution(row, result)
        (resolved if proof else unresolved).append(proof if proof else deepcopy(row))
    return dict(resolved_by_primary_evidence=resolved, unresolved_actual_exposures=unresolved,
        original_ex_reference_statuses_and_vendor_values_unmodified=True,
        reference_formula_evidence_not_instrument_tax_fee_or_noncash_FIFO_acceptance=True,
        admission_pending_source_orders=catalog.reference_inputs.pending_orders)


def reference_exposures(result, checks, *, catalog):
    return reference_resolution_audit(result, checks, catalog=catalog)['unresolved_actual_exposures']


def instrument_qualification_exposures(result, *, catalog):
    """All dates of actual held/requested/filled scopes, including no ex-date."""
    checked_catalog(catalog)
    unknown = catalog.reference_inputs.instrument_qualifications
    quantities, actions, events = {}, {}, {}
    def capture(d, symbol, reason):
        if symbol not in unknown:
            return
        key = (day(d), symbol)
        events.setdefault(key, set()).add(reason)
    def held(d):
        for symbol, quantity in quantities.items():
            if quantity > 0:
                capture(d, symbol, 'ACTUAL_JOURNAL_HELD')
    for event in result.get('account_event_ledger', []):
        if event.get('day') is None:
            continue
        d, payload, kind = event['day'], event['payload'], event['kind']
        held(d)
        if kind in ('ACTION_ANNOUNCED', 'PRETAX_ACTION_SCENARIO_ANNOUNCED'):
            actions[payload['action_id']] = payload['symbol']
        elif kind == 'FILL':
            symbol = payload['symbol']
            capture(d, symbol, 'ACTUAL_JOURNAL_FILL')
            quantities[symbol] = quantities.get(symbol, 0)+(1 if payload['side']=='BUY' else -1)*int(payload['quantity'])
            if quantities[symbol] < 0:
                raise ValueError('V4 cannot repair negative journal quantity')
        elif kind in ('EX_DISTRIBUTION', 'PRETAX_EX_ENTITLEMENT_SCENARIO'):
            symbol = actions[payload['action']]
            quantities[symbol] = quantities.get(symbol, 0)+int(payload['new_shares'])
        held(d)
    for outcome in result.get('outcomes', []):
        capture(outcome['date'], outcome['symbol'], 'ACTUAL_REQUESTED_OR_FILLED_ORDER')
    for event in result.get('provider_execution_events', []):
        if event['kind'] == 'NEXT_SESSION_RAW_TWAP_CONTEXT':
            for context in event.get('no_fill_windows', []):
                capture(event['day'], context['symbol'], 'ACTUAL_REQUIRED_NO_FILL_WINDOW')
    # Sparse contract fixtures may omit EOD journal events. Actual complete marks
    # after a retained positive lot still inherit the instrument scope explicitly.
    for mark in result.get('completed_marks', []):
        if quantities and all(day(mark['date']) >= d for d, _ in events):
            held(mark['date'])
    return [dict(date=str(d), symbol=symbol, exposure_reasons=sorted(reasons),
                 **deepcopy(unknown[symbol]))
            for (d,symbol),reasons in sorted(events.items())]


def compare_run(result, benchmark, *, catalog, account_basis,
                special_reference_exposures=(), score_coverage_complete=True,
                slippage_by_order=None):
    summary = v2_compare_run(result, benchmark, account_basis=account_basis,
        special_reference_exposures=special_reference_exposures,
        score_coverage_complete=score_coverage_complete, slippage_by_order=slippage_by_order)
    exposures = instrument_qualification_exposures(result, catalog=catalog)
    for name, window in summary['windows'].items():
        anchor, end = day(window['anchor']), day(window['end'])
        required = [e for e in exposures if anchor < day(e['date']) <= end]
        prior = [e for e in exposures if day(e['date']) <= anchor]
        affected = bool(required or prior)
        window.update(instrument_qualification_exposures=required,
            prior_instrument_qualification_exposures_affect_anchor=prior,
            instrument_tax_fee_scope_complete=not affected,
            diagnostic_cost_path='FROZEN_STYLIZED_COST_PATH_INSTRUMENT_APPLICABILITY_UNVERIFIED' if affected else 'UNCHANGED_V3_SCOPE',
            trading_fee_amounts_scope='SIMULATED_FROZEN_COST_AMOUNTS_NOT_VERIFIED_ACTUAL_CDR_FEES' if affected else 'UNCHANGED_V3_SIMULATED_FEE_SCOPE',
            trading_fees_cny_certify_actual_cdr_fee_schedule=False,
            full_mark_execution_and_instrument_scope_complete=window['full_mark_and_execution_coverage'] and not affected)
        if affected:
            window['same_path_net_tax_bounds'] = None
            window['economic_comparison_eligible'] = False
            window['point_net_excess_return'] = None
            window['point_net_target_accepted'] = False
            window['scenario_values_diagnostic_only'] = True
            window['final_net_blocked_by_unknown_instrument_qualification'] = True
    summary['instrument_qualification_exposures'] = exposures
    summary['instrument_gate_is_independent_of_reference_formula_gate'] = True
    return summary
