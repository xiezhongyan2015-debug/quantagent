"""Fixed continuous-account comparison arithmetic; no score selection or fitting.

All returns are interval returns. Tax envelopes describe the same simulated
trading path, not alternative paths under an unknown future tax settlement.
"""
from datetime import date
from decimal import Decimal

D = lambda value: Decimal(str(value))
WINDOWS = {
    'whole_observed': ('2025-01-02', '2026-09-23'),
    'calendar_2025_observed': ('2025-01-02', '2025-12-31'),
    'calendar_2026_through_sep23': ('2025-12-31', '2026-09-23'),
}


def day(value):
    return value if type(value) is date else date.fromisoformat(str(value)[:10])


def growth(start, end, index_start, index_end):
    values = [D(x) for x in (start, end, index_start, index_end)]
    if min(values) <= 0:
        raise ValueError('positive actual NAV/index endpoints required')
    a, b, c, e = values
    portfolio, benchmark = b/a-1, e/c-1
    return dict(account_scenario_return=portfolio, csi500_price_return=benchmark,
        arithmetic_excess_percentage_points=100*(portfolio-benchmark),
        wealth_relative_excess_return=(b/a)/(e/c)-1)


def tax_growth_bounds(first, last, index_start, index_end):
    keys = ('net_nav_lower_bound', 'net_nav_upper_bound')
    if any(m.get(k) is None for m in (first, last) for k in keys):
        return None
    low0, high0 = (D(first[k]) for k in keys)
    low1, high1 = (D(last[k]) for k in keys)
    if not 0 < low0 <= high0 or not 0 < low1 <= high1:
        raise ValueError('invalid positive same-path tax NAV envelope')
    lower, upper = low1/high0-1, high1/low0-1
    benchmark = D(index_end)/D(index_start)-1
    return dict(same_trading_path_net_return_lower=lower,
        same_trading_path_net_return_upper=upper,
        arithmetic_net_excess_percentage_points_lower=100*(lower-benchmark),
        arithmetic_net_excess_percentage_points_upper=100*(upper-benchmark),
        wealth_relative_net_excess_return_lower=(1+lower)/(1+benchmark)-1,
        wealth_relative_net_excess_return_upper=(1+upper)/(1+benchmark)-1,
        scope='SAME_SIMULATED_RESERVE_TRADING_PATH_NOT_ALL_UNKNOWN_TAX_CASH_FEEDBACK_PATHS')


def drawdown(points):
    peak = None
    worst = D(0)
    for value in points:
        value = D(value)
        if value <= 0:
            raise ValueError('nonpositive NAV/index mark')
        peak = value if peak is None else max(peak, value)
        worst = min(worst, value/peak-1)
    return -worst if peak is not None else None


def reference_exposures(result, ex_checks):
    """Keep only actual held/requested names affected by unresolved references."""
    quantities, actions, exposed = {}, {}, set()
    def capture(d):
        exposed.update((day(d), s) for s, q in quantities.items() if q > 0)
    for event in result.get('account_event_ledger', []):
        d = event.get('day')
        if d is None:
            continue
        capture(d)
        payload, kind = event['payload'], event['kind']
        if kind in ('ACTION_ANNOUNCED', 'PRETAX_ACTION_SCENARIO_ANNOUNCED'):
            actions[payload['action_id']] = payload['symbol']
        elif kind == 'FILL':
            s = payload['symbol']
            quantities[s] = quantities.get(s, 0)+(1 if payload['side']=='BUY' else -1)*int(payload['quantity'])
            if quantities[s] < 0:
                raise ValueError('comparison cannot repair negative journal quantity')
        elif kind in ('EX_DISTRIBUTION', 'PRETAX_EX_ENTITLEMENT_SCENARIO'):
            s = actions[payload['action']]
            quantities[s] = quantities.get(s, 0)+int(payload['new_shares'])
        capture(d)
    exposed.update((day(o['date']), o['symbol']) for o in result.get('outcomes', []))
    for event in result.get('provider_execution_events', []):
        if event['kind'] == 'NEXT_SESSION_RAW_TWAP_CONTEXT':
            exposed.update((day(event['day']), b['symbol']) for b in event['no_fill_windows'])
    affected = []
    for row in ex_checks:
        status = row.get('status', row.get('state'))
        ex = row.get('ex_date', row.get('date', row.get('ex_on')))
        if ex is None:
            raise ValueError('unrecognized sealed ex-reference audit schema')
        if status != 'MATCHES_ORDINARY_NO_RIGHTS_FORMULA' and (day(ex), row['symbol']) in exposed:
            affected.append(dict(row))
    return affected


def compare_run(result, benchmark, *, account_basis, special_reference_exposures=(),
                score_coverage_complete=True, slippage_by_order=None):
    """Report full endpoints only when they are proven; retain a marked prefix."""
    marks = result.get('completed_marks', [])
    byday = {day(m['date']): m for m in marks}
    if len(byday) != len(marks) or list(byday) != sorted(byday):
        raise ValueError('unique chronological actual marks required')
    outcomes = result.get('outcomes', [])
    unknown = [(day(e['day']), b) for e in result.get('provider_execution_events', [])
        if e['kind'] == 'NEXT_SESSION_RAW_TWAP_CONTEXT'
        for b in e['no_fill_windows'] if b.get('execution_evidence_missing')]
    refs = list(special_reference_exposures)
    prefix = None
    if marks:
        first, last = marks[0], marks[-1]
        a, b = day(first['date']), day(last['date'])
        if a in benchmark and b in benchmark:
            prefix = dict(start=str(a), end=str(b), completed_marks=len(marks),
                explicitly_diagnostic_prefix=True, **growth(first['nav'], last['nav'], benchmark[a], benchmark[b]))
    segments = {}
    for name, (a, b) in WINDOWS.items():
        a, b = day(a), day(b)
        dates = [d for d in sorted(benchmark) if a <= d <= b]
        required_unknown = [dict(date=str(d), **v) for d, v in unknown if a < d <= b]
        prior_unknown = [dict(date=str(d), **v) for d, v in unknown if d <= a]
        required_refs = [r for r in refs if a < day(r.get('ex_date', r.get('date', r.get('ex_on')))) <= b]
        prior_refs = [r for r in refs if day(r.get('ex_date', r.get('date', r.get('ex_on')))) <= a]
        # Exposures before the anchor can leave tax/corporate lots unresolved;
        # per-mark envelopes reflect that continuing state, never reset it.
        complete_marks = bool(dates) and all(d in byday for d in dates) and a in byday and b in byday
        failure = result.get('failure')
        failure_day = day(failure['book_day']) if failure and failure.get('book_day') else None
        blocked_source = failure_day is not None and failure_day <= b and failure.get('error_type') not in (
            'IncompleteCashTaxEvidence', 'IncompleteRawTWAPEvidence')
        score_complete = score_coverage_complete.get(name,False) if isinstance(score_coverage_complete,dict) else score_coverage_complete
        complete = complete_marks and not required_unknown and not prior_unknown and not blocked_source and score_complete
        sample = [byday[d] for d in dates if d in byday]
        close_risk = [dict(date=str(d), violations=byday[d].get('violations', [])) for d in dates
            if d in byday and byday[d].get('violations')]
        fill_risk = [dict(date=str(day(o['date'])), order_id=o['order_id'],
            violations=o['risk_after']['violations']) for o in outcomes
            if a < day(o['date']) <= b and (o.get('risk_after') or {}).get('violations')]
        fees = {k: D(0) for k in ('commission', 'transfer', 'stamp', 'total')}
        buy, sell, turnover = D(0), D(0), D(0)
        turnover_unknown, slippage_unknown = [], []
        slips = D(0)
        for o in outcomes:
            d = day(o['date'])
            if not a < d <= b or not o['filled']:
                continue
            notional = D(o['price'])*int(o['filled'])
            if o['side'] == 'BUY': buy += notional
            else: sell += notional
            i = sorted(benchmark).index(d)
            previous = sorted(benchmark)[i-1] if i else None
            if previous not in byday:
                turnover_unknown.append(o['order_id'])
            else:
                turnover += notional/D(byday[previous]['nav'])
            for k in fees:
                if o.get('fees', {}).get(k) is None:
                    raise ValueError('filled order lacks actual fee component '+k)
                fees[k] += D(o['fees'][k])
            slip = (slippage_by_order or {}).get(o['order_id'])
            if slip is None:
                slippage_unknown.append(o['order_id'])
            else:
                slips += D(slip)
        values = growth(byday[a]['nav'], byday[b]['nav'], benchmark[a], benchmark[b]) if complete else None
        bounds = tax_growth_bounds(byday[a], byday[b], benchmark[a], benchmark[b]) if complete and account_basis=='tax_upper_reserve' and not required_refs and not prior_refs else None
        segments[name] = dict(anchor=str(a), end=str(b), reset_account=False,
            interval_not_annualized=True, full_mark_and_execution_coverage=complete,
            observed_mark_count=len(sample), expected_mark_count=len(dates),
            scenario_values=values, same_path_net_tax_bounds=bounds,
            point_net_excess_return=None, point_net_target_accepted=False,
            scenario_max_drawdown=drawdown(m['nav'] for m in sample) if complete else None,
            csi500_price_max_drawdown=drawdown(benchmark[d] for d in dates),
            filled_buy_notional_cny=buy, filled_sell_notional_cny=sell,
            cumulative_gross_turnover_previous_close_nav=turnover if not turnover_unknown else None,
            cumulative_one_way_half_gross_turnover=turnover/2 if not turnover_unknown else None,
            turnover_unknown_orders=turnover_unknown, trading_fees_cny=fees,
            slippage_vs_raw_twap_cny=slips if not slippage_unknown else None,
            slippage_unknown_orders=slippage_unknown,
            slippage_already_in_fill_NAV_not_added_again=True,
            cost_figures_cover_completed_actual_orders_only=not complete,
            eod_risk_violations=close_risk, postfill_risk_violations=fill_risk,
            frozen_risk_policy_satisfied=complete and not close_risk and not fill_risk,
            missing_execution_windows=required_unknown,
            prior_execution_evidence_affect_anchor=prior_unknown,
            exposed_unresolved_ex_references=required_refs,
            prior_ex_reference_exposures_affect_anchor=prior_refs,
            economic_comparison_eligible=complete and not close_risk and not fill_risk and not required_refs and not prior_refs and bounds is not None,
            all_PLAN_acceptance_complete=False)
    return dict(account_basis=account_basis, replay_status=result['status'],
        continuous_account=True, prefix_diagnostic=prefix, windows=segments,
        all_PLAN_acceptance_complete=False, point_net_target_accepted=False,
        remaining='Independent prospective60sessions and prediction/statistical acceptance are outside this trading driver')
