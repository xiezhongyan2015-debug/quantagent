"""Independent bounded audit of the 36 pinned NEW incomplete research journals.

Implementation V2: exact frozen model versions and retained-book failure dates.
Default is a pure printed contract. Explicit audit execution never imports a
provider, engine, model, old inspector or data runtime. It checks saved state;
it does not replay orders, infer missing prices or certify tax/broker evidence.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import re

AUDIT_IMPLEMENTATION_VERSION = 2
NAMESPACE = 'TARGET_SOURCE_BOUND_DECIMAL_RESEARCH_V1'
FREEZE = 'd142cc97070bc81034a6'
MODELS = ('fixed_label_1', 'fixed_label_2', 'fixed_label_3', 'fixed_label_4',
          'blo_selected', 'selected_full', 'pv', 'pv_factors', 'pv_fund')
SCENARIOS = ('primary', 'stress')
BASES = ('pretax', 'tax_upper_reserve')
RESULTS = 'gru_research/artifacts/target_decimal_research_replay_v1/attempt1'
AUDIT_ROOT = 'gru_research/artifacts/target_decimal_partial_ledger_audit_v1'
REVIEW = 'gru_research/artifacts/target_decimal_result_review_v1/attempt1/actual_result_review_receipt_v1.json'
REVIEW_SHA = 'c6bc1dd74007ae7f6ea7b8cc9b3c0101bcd798674391e386be0c275ef1d1e6a4'
WRAPPER = 'gru_research/artifacts/target_decimal_quote_research_preparation_v2_20261002/root_actual_replay_capture_attempt1/root_actual_replay_execution_receipt_v1.json'
WRAPPER_SHA = '07dac8a27446854fdd554f4dcdd86dd0a21aeaeb13f1bca8eb48d6ab08d80d2f'
CALENDAR = 'gru_research/artifacts/minute_calendar.json'
TAIL = 'gru_research/artifacts/minute_tail_calendar_evidence.json'
SEAL = 'gru_research/artifacts/trading_comparison_source_seal_v4.json'
SEAL_SHA = '34c39255a5cd055cf233d594d03935841d6b99ae1f943dbb2e2251eab41fa20c'
SMALL_CAP = 2 * 1024**2
JOURNAL_CAP = 128 * 1024**2
TOTAL_CAP = 2 * 1024**3
SOURCE_ROSTER = ('research_v3/src/causal_cash_book.py',
 'research_v3/src/pretax_replay_account.py', 'research_v3/src/next_session_execution.py',
 'research_v3/src/dev_rule_scenarios.py', 'gru_research/extended_costs.py',
 'gru_research/research_rule_scenarios.py', 'gru_research/research_market_provider.py',
 'gru_research/trading_replay.py', 'gru_research/tax_reserve_account.py',
 'gru_research/tax_reserve_replay.py', 'gru_research/trading_comparison_v4.py',
 'gru_research/target_decimal_result_review_v1.py', 'gru_research/v4_actual_evidence.py')
KINDS = frozenset(('INITIALIZE', 'SESSION_OPEN', 'SESSION_CLOSE', 'VERSION_CHANGED',
 'PRETAX_ACTION_SCENARIO_ANNOUNCED', 'ENTITLEMENT_CAPTURED',
 'PRETAX_EX_ENTITLEMENT_SCENARIO', 'NEW_SHARES_LISTED_SCENARIO',
 'GROSS_DIVIDEND_PAID_SCENARIO', 'FILL', 'RESEARCH_RAW_TWAP_FILL_WINDOW',
 'RESEARCH_CASH_DIVIDEND_TAX_UPPER_LIABILITY_RESERVED'))
EXECUTION_SCOPE = 'ASSUMED_FIVE_MINUTE_RAW_OPEN_TWAP_WITH_PRIOR_VOLUME_CAP_NOT_OBSERVED_EXECUTABLE_DEPTH'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def reject_constant(value):
    raise ValueError('nonfinite JSON constant: ' + value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def decode(body):
    return json.loads(body, parse_constant=reject_constant, object_pairs_hook=unique_object)


def money(value):
    require(type(value) in (str, int), 'money must retain original decimal string or integer; bool/float rejected')
    number = Decimal(value)
    require(number.is_finite(), 'nonfinite decimal')
    return number


def day(value):
    require(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'plain ISO day required')
    require(date.fromisoformat(value).isoformat() == value, 'noncanonical day')
    return value


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, 'strict nonnegative integer required')
    return value


def pure_contract():
    return dict(schema_version=1, audit_implementation_version=AUDIT_IMPLEMENTATION_VERSION,
        status='PREPARED_ONLY_DEFAULT_NO_FILE_ACCESS',
        namespace=NAMESPACE, expected_journals=36, models=list(MODELS),
        scenarios=list(SCENARIOS), account_bases=list(BASES),
        inspector_receipt_sha256=REVIEW_SHA, finished_wrapper_receipt_sha256=WRAPPER_SHA,
        per_journal_byte_cap=JOURNAL_CAP, total_read_byte_cap=TOTAL_CAP,
        one_journal_at_a_time=True, execute_required=True,
        economic_target_accepted=False, full_PLAN_complete=False,
        all_PLAN_acceptance_complete=False, point_net_return=None,
        source_prices_and_memberships_independently_reconstructed=False,
        broker_tax_FIFO_fraction_and_delisting_qualification='UNKNOWN')


class Reader:
    def __init__(self):
        self.total = 0
        self.bindings = []
        self.observed_stats = {}

    def read(self, path, expected_sha, expected_size=None, cap=SMALL_CAP):
        p = Path(path)
        require(p.is_absolute() and str(p.resolve()) == str(p) and p.is_file() and not p.is_symlink(),
                'exact absolute regular source path required')
        before = p.stat()
        require(before.st_size <= cap and self.total + before.st_size <= TOTAL_CAP, 'read budget exceeded before opening')
        if expected_size is not None:
            require(type(expected_size) is int and before.st_size == expected_size, 'source byte size changed')
        with p.open('rb') as stream:
            body = stream.read(cap + 1)
        self.total += len(body)
        after = p.stat()
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'source changed during bounded read')
        require(len(body) == before.st_size and len(body) <= cap and digest(body) == expected_sha,
                'source bytes/hash changed')
        identity = dict(path=str(p), bytes=len(body), sha256=expected_sha)
        self.bindings.append(identity)
        self.observed_stats[str(p)] = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        return body

    def json(self, path, expected_sha, expected_size=None, cap=SMALL_CAP):
        return decode(self.read(path, expected_sha, expected_size, cap))


def chain(events, declared_tail):
    require(type(events) is list and events, 'actual complete saved event ledger required')
    previous = 'GENESIS'
    for index, event in enumerate(events):
        require(type(event) is dict and set(event) == {'kind', 'day', 'version', 'payload', 'previous_hash', 'hash'},
                'unknown hash-chain event shape at ' + str(index))
        body = {key: value for key, value in event.items() if key != 'hash'}
        # Producer record() uses these DEFAULT JSON separators/ensure_ascii.
        actual = digest(json.dumps(body, sort_keys=True, default=str).encode())
        require(event['previous_hash'] == previous and actual == event['hash'], 'full saved ledger hash chain mismatch at ' + str(index))
        previous = event['hash']
    require(previous == declared_tail, 'saved terminal account hash differs')
    return previous


def quantities(lots):
    return {symbol: sum(lot['quantity'] for lot in rows) for symbol, rows in lots.items()
            if sum(lot['quantity'] for lot in rows)}


def buy_lattice(symbol):
    require(type(symbol) is str and re.fullmatch(r'(sh|sz)\d{6}', symbol), 'unknown security ID')
    if symbol.startswith('sh688'):
        return 200, 1, 100000
    if symbol.startswith(('sh600', 'sh601', 'sh603', 'sh605', 'sz000', 'sz001', 'sz002', 'sz003')):
        return 100, 100, 1000000
    if symbol.startswith(('sz300', 'sz301')):
        return 100, 100, 300000
    raise ValueError('UNKNOWN buy grid for security outside bound original board prefixes: ' + symbol)


def legal_buy(quantity, symbol):
    minimum, step, maximum = buy_lattice(symbol)
    require(quantity >= minimum and (quantity - minimum) % step == 0 and quantity <= maximum,
            'BUY breaches source-bound research share lattice')


def audit_account(journal, basis, sessions, official_days, prior_row):
    events = journal['account_event_ledger']
    chain_tail = chain(events, journal['account_event_ledger_hash'])
    require(journal['status'] == 'INCOMPLETE' and journal['incomplete_prefix_is_not_a_full_window'] is True,
            'fixed task only accepts original incomplete journals')
    for key in ('full_PLAN_complete', 'all_PLAN_acceptance_complete', 'economic_target_accepted', 'net_NAV_contract_compatible'):
        require(journal[key] is False, 'diagnostic acceptance flag promoted: ' + key)
    require(journal['declared_valuation_days'] == official_days, 'declared valuation calendar differs from bound source dates')
    require(journal['distribution_tax_liability_included'] is (basis == 'tax_upper_reserve'), 'basis differs')
    require(journal['completed_marks'] and len(journal['completed_marks']) == prior_row['completed_mark_count'], 'closed mark count differs from pinned inspector')
    return_field = 'full_period_pretax_return' if basis == 'pretax' else 'full_period_tax_reserve_scenario_return'
    require(journal[return_field] is None and journal['failure'] == prior_row['failure'], 'incomplete failure/return changed')
    require(money(journal['initial_scenario_capital']) == Decimal(1000000), 'capital changed')
    current = None
    closed = True
    version = 'INITIAL'
    cash = Decimal(1000000)
    reserve = Decimal(0)
    fee_total = Decimal(0)
    distribution = Decimal(0)
    lots, actions, rights, entitlements, fills, fill_windows = {}, {}, {}, {}, {}, {}
    ex_done, paid, listings, reserve_days = set(), set(), set(), set()
    opens, closes, snapshots, violations = [], [], {}, []
    session_pos = {d: index for index, d in enumerate(sessions)}

    def capture(d):
        snapshots[d] = dict(cash=cash, reserve=reserve, receivable=sum(rights.values(), Decimal(0)),
                            fees=fee_total, distribution=distribution, quantity=quantities(lots))

    for index, event in enumerate(events):
        kind, payload, d = event['kind'], event['payload'], event['day']
        require(kind in KINDS and type(payload) is dict, 'UNKNOWN unsupported actual ledger event: ' + str(kind))
        if kind == 'INITIALIZE':
            require(index == 0 and d is None and event['version'] == version and money(payload['capital']) == cash,
                    'account initialization/reset differs')
            require(payload['calendar_sha256'] == digest(json.dumps(sessions).encode()), 'original account full calendar SHA differs')
            continue
        require(index > 0 and d in session_pos, 'financial event not on bound calendar')
        pre_open = closed and len(opens) < len(official_days) and d == official_days[len(opens)] and kind in {
            'PRETAX_EX_ENTITLEMENT_SCENARIO', 'NEW_SHARES_LISTED_SCENARIO'}
        if kind == 'SESSION_OPEN':
            require(closed and len(opens) < len(official_days) and d == official_days[len(opens)], 'noncontinuous/double open')
            current, closed = d, False
            opens.append(d)
        else:
            require(d == current or pre_open, 'event outside active session or source-bound pre-open action')
        if kind == 'VERSION_CHANGED':
            require(payload['capital_reset'] is False and payload['from'] == version and payload['to'] == event['version'], 'version reset or identity mismatch')
            version = payload['to']
        require(event['version'] == version, 'event version mismatch')
        if kind == 'PRETAX_ACTION_SCENARIO_ANNOUNCED':
            ident = payload['action_id']
            require(not closed and ident not in actions and payload['announced_on'] <= d <= payload['record_on'] < payload['ex_on'],
                    'duplicate/late action could invent record holdings')
            require(payload['entitlement_basis'] == 'LISTED_ONLY' and
                    payload['timing_assumption'] == 'PAY_AT_CLOSE_LIST_AT_OPEN_SELL_AT_EXPLICIT_DATE_RESEARCH_SCENARIO', 'unknown rights timing/basis')
            require(money(payload['cash_gross_per_share']) >= 0 and money(payload['new_share_ratio']) >= 0, 'negative declared entitlement')
            for field in ('record_on', 'ex_on'):
                require(payload[field] in session_pos, 'rights date outside official source calendar')
            if money(payload['cash_gross_per_share']):
                require(payload['pay_on'] in session_pos and payload['pay_on'] >= payload['ex_on'], 'unknown cash payment date')
            if money(payload['new_share_ratio']):
                require(payload['shares_list_on'] in session_pos and payload['shares_sellable_on'] in session_pos and
                        payload['ex_on'] <= payload['shares_list_on'] <= payload['shares_sellable_on'], 'unknown share listing/sellability dates')
            actions[ident] = payload
        elif kind == 'ENTITLEMENT_CAPTURED':
            ident = payload['action']; action = actions[ident]
            q = sum(lot['quantity'] for lot in lots.get(action['symbol'], []) if lot['listed_on'] <= d)
            require(not closed and d == action['record_on'] and ident not in entitlements and payload['basis'] == 'LISTED_ONLY' and
                    integer(payload['quantity']) == q and payload['source'] == action['entitlement_source'], 'record-date quantity/basis differs')
            entitlements[ident] = q
        elif kind == 'PRETAX_EX_ENTITLEMENT_SCENARIO':
            ident = payload['action']; action = actions[ident]
            require(pre_open and ident in entitlements and ident not in ex_done and d == action['ex_on'] and
                    integer(payload['record_quantity']) == entitlements[ident], 'ex entitlement not backed by original record')
            exact_shares = Decimal(entitlements[ident]) * money(action['new_share_ratio'])
            require(exact_shares == exact_shares.to_integral_value() and integer(payload['new_shares']) == exact_shares,
                    'fractional entitlement cannot be rounded/substituted')
            right = (Decimal(entitlements[ident]) * money(action['cash_gross_per_share'])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            require(money(payload['gross_cash_receivable']) == right and payload['distribution_tax_deducted'] is False,
                    'research cent cash right does not reconcile')
            if right:
                rights[ident] = right
                distribution += right
            if exact_shares:
                lots.setdefault(action['symbol'], []).append(dict(quantity=int(exact_shares),
                    sellable_on=action['shares_sellable_on'], listed_on=action['shares_list_on'], acquired_on=d))
            ex_done.add(ident)
        elif kind == 'NEW_SHARES_LISTED_SCENARIO':
            ident = payload['action']
            require(pre_open and ident in ex_done and ident not in listings and d == actions[ident]['shares_list_on'], 'listing event differs')
            listings.add(ident)
        elif kind == 'GROSS_DIVIDEND_PAID_SCENARIO':
            ident = payload['action']
            require(not closed and ident in ex_done and ident not in paid and d == actions[ident]['pay_on'] and
                    payload['timing'] == 'CLOSE_ASSUMPTION' and payload['distribution_tax_deducted'] is False,
                    'payment timing/rights mismatch')
            expected = rights.pop(ident, Decimal(0))
            require(money(payload['amount']) == expected, 'paid cash differs from recorded right')
            cash += expected; paid.add(ident)
        elif kind == 'FILL':
            fid, symbol, side = payload['id'], payload['symbol'], payload['side']
            q = integer(payload['quantity'], 1)
            px, fee = money(payload['price']), money(payload['fee'])
            require(not closed and fid not in fills and payload['day'] == d and side in ('BUY', 'SELL') and px > 0 and fee >= 0,
                    'duplicate/outside-open/invalid fill')
            i = session_pos[d]
            require(i + 1 < len(sessions) and payload['next_session'] == sessions[i+1], 'supplied T+1 differs from real calendar')
            if side == 'BUY':
                legal_buy(q, symbol)
                cash -= px*q + fee
                lots.setdefault(symbol, []).append(dict(quantity=q, sellable_on=payload['next_session'], listed_on=d, acquired_on=d))
            else:
                available = sum(lot['quantity'] for lot in lots.get(symbol, []) if lot['listed_on'] <= d and lot['sellable_on'] <= d)
                require(q <= available, 'SELL uses T+1 locked or unlisted inventory')
                minimum, step, _ = buy_lattice(symbol)
                require(q == available or q >= minimum and (q-minimum) % step == 0,
                        'SELL violates source-bound lattice without final odd-lot liquidation')
                left = q
                for lot in lots.get(symbol, []):
                    if lot['listed_on'] <= d and lot['sellable_on'] <= d:
                        take = min(left, lot['quantity']); lot['quantity'] -= take; left -= take
                        if not left: break
                require(left == 0, 'sale lot allocation mismatch')
                cash += px*q - fee
            fee_total += fee; fills[fid] = payload
        elif kind == 'RESEARCH_RAW_TWAP_FILL_WINDOW':
            fid = payload['fill_id']
            require(not closed and fid in fills and fid not in fill_windows and payload['quantity'] == fills[fid]['quantity'], 'fill window has no matching cash fill')
            require(payload['window_start'] == d+' 09:31:00+08:00' and
                    all(payload[key] == d+' 09:36:00+08:00' for key in ('window_end', 'recorded_fill_time', 'price_available_at')) and
                    payload['execution_evidence'] == EXECUTION_SCOPE and payload['raw_reference_kind'] == 'MEAN_FIVE_COMPLETE_RAW_MINUTE_OPENS', 'fill time/source evidence mismatch')
            fill_windows[fid] = payload
        elif kind == 'SESSION_CLOSE':
            require(not closed and d == current and all(aid in entitlements for aid, action in actions.items() if action['record_on'] == d),
                    'double close/missing record entitlement')
            require(all(aid in paid for aid, action in actions.items() if action['pay_on'] == d and money(action['cash_gross_per_share'])), 'closed payment missing')
            closed = True; closes.append(d); capture(d)
        elif kind == 'RESEARCH_CASH_DIVIDEND_TAX_UPPER_LIABILITY_RESERVED':
            require(basis == 'tax_upper_reserve' and closed and d == current and d not in reserve_days and
                    payload['reserve_is_simulated_liability_not_actual_broker_debit'] is True and payload['day'] == d,
                    'reserve not source-bound research post-close liability')
            target, delta = money(payload['tax_liability_upper_reserve']), money(payload['reserve_delta'])
            require(target >= 0 and money(payload['previous_reserve']) == reserve and delta == target-reserve, 'reserve change arithmetic mismatch')
            cash -= delta; reserve = target
            require(money(payload['spendable_cash']) == cash and money(payload['gross_settled_cash']) == cash+reserve and
                    money(payload['cash_deficit']) == max(Decimal(0), -cash), 'reserve spendable/gross/deficit arithmetic mismatch')
            reserve_days.add(d); capture(d)
        if cash < 0:
            violations.append(dict(kind='NEGATIVE_SPENDABLE_CASH', event_index=index, day=d, amount=str(cash), trigger=kind))
        require(all(lot['quantity'] >= 0 for rows in lots.values() for lot in rows), 'negative holdings')
    require(set(fill_windows) == set(fills), 'fill window trace not one-to-one')
    if basis == 'tax_upper_reserve':
        require(reserve_days == set(closes) and
                [event['payload'] for event in events if event['kind'] == 'RESEARCH_CASH_DIVIDEND_TAX_UPPER_LIABILITY_RESERVED'] == journal['tax_reserve_events'],
                'post-close reserve trace differs from saved account history')
    require(closes == opens[:len(closes)], 'closed days differ from open prefix')
    require(quantities(lots) == journal['closing_quantity'], 'saved terminal quantities differ from independent event arithmetic')
    marks = journal['completed_marks']; mark_days = [day(mark['date']) for mark in marks]
    require(mark_days == official_days[:len(marks)] and closes[:len(marks)] == mark_days, 'marked closed prefix skips/duplicates official days')
    for mark in marks:
        state = snapshots[mark['date']]
        require(money(mark['cash']) == state['cash'] and money(mark['cash_receivable']) == state['receivable'] and
                money(mark['fees']) == state['fees'] and money(mark['gross_distribution_income']) == state['distribution'], 'closed mark cash/rights/fees mismatch')
        components = [money(mark[key]) for key in ('cash', 'listed_stock_value', 'unlisted_share_claim', 'cash_receivable')]
        require(money(mark['nav']) == sum(components, Decimal(0)), 'within-journal NAV component sum mismatch')
        require(money(mark['accounting_residual']) == 0, 'producer recorded nonzero residual')
        require(mark['net_NAV_contract_compatible'] is False and mark['economic_target_accepted'] is False, 'mark claims final net')
        if basis == 'tax_upper_reserve':
            require(money(mark['spendable_cash']) == state['cash'] and money(mark['tax_liability_upper_reserve']) == state['reserve'] and
                    money(mark['gross_settled_cash']) == state['cash']+state['reserve'], 'reserve mark differs')
        require(all(component >= 0 for component in components[1:]), 'negative recorded stock/claim valuation')
    failure = journal['failure']
    require(failure['last_completed_valuation'] == mark_days[-1] and failure['book_day'] == current and
            failure['book_closed'] is closed and len(marks) < len(official_days), 'failure state differs from actual retained book')
    require(len(opens) in (len(closes), len(closes)+1), 'more than one unclosed session')
    incomplete_close_days = closes[len(marks):]
    outcome_result = audit_outcomes(journal, fills, opens, sessions)
    inferred_fraction = []
    if closed and current in session_pos and session_pos[current]+1 < len(sessions):
        next_day = sessions[session_pos[current]+1]
        for ident,action in actions.items():
            if action['ex_on'] == next_day and ident in entitlements and ident not in ex_done:
                value = Decimal(entitlements[ident])*money(action['new_share_ratio'])
                if value != value.to_integral_value():
                    inferred_fraction.append(dict(action_id=ident,symbol=action['symbol'],
                        captured_record_quantity=entitlements[ident],declared_new_share_ratio=action['new_share_ratio'],
                        noninteger_new_share_entitlement=str(value),inferred_due_next_session=next_day,
                        evidence_level='CANDIDATE_INFERRED_FROM_RECONSTRUCTED_ACTION_STATE_NOT_ORIGINAL_FAILURE_SYMBOL_OR_TIMESTAMP'))
    first_violation = violations[0] if violations else None
    return dict(full_saved_ledger_hash_chain_recomputed=True, account_journal_tail_hash=chain_tail,
        actual_events=len(events), event_kind_counts=dict(Counter(e['kind'] for e in events)),
        independently_reconciled_marked_closed_prefix_sessions=len(marks), last_completed_mark=mark_days[-1],
        retained_open_unclosed_day=None if closed else current,
        closed_but_unmarked_days=incomplete_close_days,
        failure_did_not_add_completed_mark=True,
        retained_book_day_closed_and_marked=closed and current in mark_days,
        retained_book_day=current, retained_book_closed=closed,
        failed_attempted_day='UNKNOWN_NOT_RECORDED_IN_ORIGINAL_FAILURE',
        first_uncompleted_declared_session=official_days[len(marks)],
        first_uncompleted_session_is_not_direct_observation_of_failed_attempt_time=True,
        hash_chain_covers_unclosed_tail=True, cash_lots_T1_rights_and_fee_arithmetic_reconciled=True,
        source_bound_research_buy_lattice_checked=True, statutory_or_broker_eligibility_certified=False,
        nonnegative_cash_quantity_constraint_pass=not violations, cash_violations=violations,
        first_cash_violation=first_violation,
        marked_prefix_sessions_before_first_cash_violation=(sum(d < first_violation['day'] for d in mark_days) if first_violation else len(marks)),
        next_session_fraction_candidate_inferences=inferred_fraction,
        inferred_fraction_candidates_do_not_prove_broker_allocation_or_actual_attempt_timestamp=True,
        within_journal_NAV_four_component_addition_checked=True,
        recorded_zero_residual_observed_not_external_valuation_proof=True,
        each_symbol_close_price_and_membership_external_reconstruction='UNKNOWN_NOT_LOGGED_IN_MARKS',
        true_TWAP_depth_statutory_tax_FIFO_rounding_fraction_and_delisting_qualification='UNKNOWN',
        full_account_complete=False, economic_target_accepted=False, full_PLAN_complete=False,
        all_PLAN_acceptance_complete=False, point_net_return=None, **outcome_result)


def audit_outcomes(journal, fills, opens, sessions):
    planned = {}
    for plan in journal['plans']:
        for order in plan['orders']:
            require(order['order_id'] not in planned, 'duplicate planned order ID')
            planned[order['order_id']] = order
    matched, seen, queue = set(), set(), []
    dates = []; used_capacity = {}; capacity_terms = {}
    for outcome in journal['outcomes']:
        oid, d = outcome['order_id'], day(outcome['date'])
        require(oid in planned and oid not in seen and d in opens, 'outcome has no unique real planned/open order')
        seen.add(oid); dates.append(d); order = planned[oid]
        q = integer(outcome['filled']); requested = integer(outcome['requested'], 1)
        require(q <= requested and integer(outcome['expired']) == requested-q, 'filled/remainder arithmetic differs')
        require((outcome['symbol'], outcome['side'], requested, d) ==
                (order['symbol'], order['side'], order['quantity'], order['execute_on']), 'order/outcome identity differs')
        i = sessions.index(d); previous = sessions[i-1]
        require(previous+' 15:00:00+08:00' <= order['decision_at'] < d+' 09:30:00+08:00' and
                order['capacity_asof'] <= order['decision_at'] and outcome['decision_at'] == order['decision_at'] and
                outcome['capacity_asof'] == order['capacity_asof'], 'future order/capacity timestamp')
        executor_keys = ('order_id','symbol','side','quantity','limit_price','decision_at','execute_on','capacity_shares','capacity_asof','evidence')
        order_body = {key:order[key] for key in executor_keys}
        integer(order['quantity'], 1); integer(order['capacity_shares'])
        if order['side'] == 'BUY':
            legal_buy(requested, order['symbol'])
        require(digest(json.dumps(order_body, sort_keys=True, default=str).encode()) == outcome['order_hash'], 'frozen order body hash differs')
        fid = 'OPEN:'+oid
        require(outcome['execution_evidence'] == EXECUTION_SCOPE and
                outcome['execution_window_start'] == d+' 09:31:00+08:00' and
                outcome['execution_window_end'] == d+' 09:36:00+08:00' and
                outcome['five_minute_price_available_at'] == d+' 09:36:00+08:00', 'outcome raw window provenance differs')
        capkey = (d,outcome['symbol'])
        terms = (order['capacity_shares'],order['capacity_asof'],order['evidence'])
        require(capkey not in capacity_terms or capacity_terms[capkey] == terms, 'participation terms changed within same stock/session')
        capacity_terms[capkey] = terms
        if q:
            require(fid in fills and outcome['state'] == ('FILLED' if q == requested else 'PARTIAL_REMAINDER_EXPIRED'), 'filled outcome missing journal or state')
            f = fills[fid]; fees = outcome['fees']; px = money(outcome['price'])
            require((f['symbol'],f['side'],f['quantity'],f['day']) == (outcome['symbol'],outcome['side'],q,d) and
                    money(f['price']) == px and money(f['fee']) == money(fees['total']), 'cash fill/outcome mismatch')
            value = px*q
            fee_evidence = outcome['fee_evidence']
            require(fee_evidence['broker_commission_and_slippage'] == 'ORIGINAL_FROZEN_SCENARIO_ASSUMPTIONS' and
                    money(fee_evidence['minimum_commission']) == Decimal(5) and
                    money(fee_evidence['stamp_sell_only']) == Decimal('.0005') and
                    money(fee_evidence['transfer_bilateral']) == Decimal('.00001') and
                    fee_evidence['net_dividend_tax_claim'] is False, 'research fee evidence contract differs')
            rate = Decimal('.0003') if journal['cost_scenario'] == 'PRIMARY' else Decimal('.0005')
            require(money(fee_evidence['commission_rate']) == rate and
                    money(fee_evidence['slippage_bps']) == (Decimal(10) if journal['cost_scenario'] == 'PRIMARY' else Decimal(20)),
                    'scenario fee/slippage provenance differs')
            cent = lambda number:number.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            expected = dict(commission=cent(max(Decimal(5), value*rate)), transfer=cent(value*Decimal('.00001')),
                            stamp=cent(value*Decimal('.0005')) if outcome['side'] == 'SELL' else Decimal(0))
            require(all(money(fees[key]) == amount for key,amount in expected.items()) and
                    money(fees['total']) == sum(expected.values(),Decimal(0)), 'source-bound research fee calculation differs')
            require(outcome['recorded_fill_time'] == d+' 09:36:00+08:00', 'fill recorded before window completes')
            require(q <= order['capacity_shares'] and
                    (px <= money(order['limit_price']) if outcome['side'] == 'BUY' else px >= money(order['limit_price'])), 'fill exceeds declared capacity/order price')
            used_capacity[capkey] = used_capacity.get(capkey,0)+q
            require(used_capacity[capkey] <= order['capacity_shares'], 'cumulative actual fills exceed dated capacity')
            matched.add(fid)
        else:
            require(fid not in fills and outcome['price'] is None and outcome['fees'] is None and outcome['recorded_fill_time'] is None,
                    'zero fill changed cash/inventory or fabricated timestamp')
        if outcome['symbol'] == 'sh600126' and d == '2025-02-10' and outcome['side'] == 'BUY':
            require(q == 0, 'source-bound ceiling target BUY fabricated a fill')
            if outcome['state'] == 'AT_LIMIT_QUEUE_NOT_ASSUMED':
                queue.append(dict(order_id=oid, state=outcome['state'], filled=q,
                    declared_order_limit_price=order['limit_price'], source_bound_raw_reference_and_ceiling='7.61',
                    order_limit_not_assumed_equal_exchange_ceiling=True))
    require(dates == sorted(dates) and matched == set(fills), 'outcome chronology or unpaired account fill')
    return dict(outcomes_checked=len(seen), fills_checked=len(fills), unattempted_planned_orders=len(set(planned)-seen),
        source_bound_research_fee_schedule_recomputed=True, broker_fee_certified=False,
        target_limit_BUY_zero_fill_events=queue, target_limit_BUY_zero_fill_count=len(queue))


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def execute(contract_path, contract_sha, output):
    root = Path(__file__).resolve().parents[1]
    reader = Reader()
    contract = reader.json(Path(contract_path).resolve(), contract_sha)
    require(contract['schema_version'] == 1 and contract['audit_implementation_version'] == AUDIT_IMPLEMENTATION_VERSION and
            contract['inspector_receipt_sha256'] == REVIEW_SHA and
            contract['finished_wrapper_receipt_sha256'] == WRAPPER_SHA and
            contract['per_journal_byte_cap'] == JOURNAL_CAP and contract['total_read_byte_cap'] == TOTAL_CAP,
            'externally pinned preparation contract differs')
    require(contract['source_roster'] == list(SOURCE_ROSTER), 'source roster differs')
    require(contract['audit_code']['path'] == str(Path(__file__).resolve()), 'audit source path differs')
    reader.read(Path(__file__).resolve(), contract['audit_code']['sha256'], contract['audit_code']['bytes'])
    review = reader.json(root/REVIEW, REVIEW_SHA, 216396)
    require(review['status'] == 'PASS_READONLY_ACTUAL_36_ATTEMPTS_RECORDED_NOT_FINANCIAL_ACCEPTANCE' and
            review['actual_all36_recorded'] is True and review['incomplete_journal_count'] == 36 and
            review['complete_diagnostic_journal_count'] == 0 and review['missing_journal_count'] == 0 and
            review['actual_target_queue_zero_fill_event_count'] == 19, 'fixed inspector evidence differs')
    for key in ('economic_target_accepted','full_PLAN_complete','all_PLAN_acceptance_complete'):
        require(review[key] is False, 'inspector promoted acceptance')
    finished = reader.json(root/WRAPPER, WRAPPER_SHA, 2786)
    require(finished['exit_code'] == 0 and finished['new_namespace_driver_executed_once'] is True and
            finished['actual_small_SHA_before_after_identical'] is True and finished['original_stat_before_after_identical'] is True and
            finished['new_output'] == str(root/RESULTS), 'actual once-finished wrapper differs')
    require(review['actual_file_identities'][str(root/WRAPPER)]['sha256'] == WRAPPER_SHA, 'inspector/wrapper chain differs')
    for relative in SOURCE_ROSTER:
        identity = contract['source_identities'][relative]
        require(identity['path'] == str(root/relative), 'source identity path differs')
        reader.read(root/relative, identity['sha256'], identity['bytes'])
    seal = reader.json(root/SEAL, SEAL_SHA, 933341)
    for relative in SOURCE_ROSTER:
        original_pin = seal['immutable_files'].get(str(root/relative))
        if original_pin is not None:
            require(contract['source_identities'][relative]['sha256'] == original_pin, 'original sealed source identity differs')
    calendar_identity = contract['calendar_identity']; tail_identity = contract['tail_calendar_identity']
    require(calendar_identity['path'] == str(root/CALENDAR) and tail_identity['path'] == str(root/TAIL) and
            seal['immutable_files'][str(root/CALENDAR)] == calendar_identity['sha256'] and
            seal['immutable_files'][str(root/TAIL)] == tail_identity['sha256'], 'original calendar seal differs')
    cal = reader.json(root/CALENDAR, calendar_identity['sha256'], calendar_identity['bytes'])
    tail = reader.json(root/TAIL, tail_identity['sha256'], tail_identity['bytes'])
    sessions = [day(d) for d in cal['dates']]
    require(sessions == sorted(set(sessions)) and sessions[-1] == tail['last_signal_date'], 'calendar order/tail differs')
    tail_day = datetime.fromtimestamp(tail['next_known_execution_time']/1000000000, timezone.utc).astimezone(
        ZoneInfo('Asia/Shanghai')).date().isoformat()
    require(tail_day == '2026-09-24' and tail_day > sessions[-1], 'verified tail day differs')
    sessions.append(tail_day)
    sessions = [d for d in sessions if d >= '2024-12-01']
    official_days = [d for d in sessions if '2025-01-02' <= d <= '2026-09-23']
    require(len(official_days) == 420 and digest(json.dumps(sessions).encode()) == '74c06206201019ac9c99f6d098c538d8095d343428e14e187fd65bc600957d48', 'account calendar differs')
    roster = [(model,scenario,basis) for model in MODELS for scenario in SCENARIOS for basis in BASES]
    rows = review['actual_rows']
    require(len(rows) == 36 and [(r['model'],r['scenario'],r['account_basis']) for r in rows] == roster, '36 fixed inspector row order differs')
    require(contract['journal_roster'] == [row['replay_identity'] for row in rows],
            'advertised contract36 exact path/bytes/SHA roster differs from pinned actual inspector')
    declared_semantics = dict(namespace=NAMESPACE, freeze_id=FREEZE,
        model_versions={model:FREEZE+':'+model for model in MODELS},
        cost_scenario_names={scenario:scenario.upper() for scenario in SCENARIOS},
        account_bases=list(BASES), corporate_action_scope='RECORDED_LISTED_RIGHTS_RESEARCH_SCENARIO_ONLY_NOT_INDEPENDENT_PRIMARY_OR_BROKER_CERTIFICATION',
        corporate_action_rule_source_paths=['research_v3/src/causal_cash_book.py','research_v3/src/pretax_replay_account.py','gru_research/tax_reserve_account.py'])
    require(contract['journal_semantics'] == declared_semantics, 'model/cost/basis/action scope contract differs')
    total_journals = 0
    for row,(model,scenario,basis) in zip(rows,roster):
        expected_path = root/RESULTS/model/scenario/basis/'replay.json'
        ident = row['replay_identity']
        require(ident['path'] == str(expected_path) and 0 < ident['bytes'] <= JOURNAL_CAP and
                row['status'] == 'INCOMPLETE' and row['expected_run_id'] == NAMESPACE+':'+FREEZE+':'+model+':'+scenario+':'+basis,
                'unapproved journal/identity/status')
        require(review['actual_file_identities'][str(expected_path)] == ident, 'journal binding differs within stable inspector')
        total_journals += ident['bytes']
    require(type(contract['expected_total_journal_bytes']) is int and
            contract['expected_total_journal_bytes'] == total_journals, 'advertised exact36 journal byte sum differs')
    require(reader.total+total_journals <= TOTAL_CAP, 'all36 total budget unavailable')
    target = root/AUDIT_ROOT/'attempt1'
    require(Path(output).resolve() == target and not target.exists(), 'only exclusive fixed new audit output allowed')
    target.parent.mkdir(parents=True, exist_ok=True)
    marker = target.parent/'READONLY_AUDIT_STARTED_v1.json'
    write_new(marker, dict(created_utc=datetime.now(timezone.utc).isoformat(), status='ONCE_READONLY_AUDIT_DISPATCHED',
        inspector_receipt_sha256=REVIEW_SHA, finished_wrapper_receipt_sha256=WRAPPER_SHA,
        audit_code_sha256=contract['audit_code']['sha256'], audit_implementation_version=AUDIT_IMPLEMENTATION_VERSION, output=str(target), economic_target_accepted=False,
        full_PLAN_complete=False, all_PLAN_acceptance_complete=False))
    target.mkdir()
    results=[]
    try:
        for row,(model,scenario,basis) in zip(rows,roster):
            ident = row['replay_identity']
            journal = reader.json(Path(ident['path']), ident['sha256'], ident['bytes'], JOURNAL_CAP)
            require(journal['run_id'] == row['expected_run_id'] and journal['model'] == FREEZE+':'+model and
                    journal['cost_scenario'] == scenario.upper(), 'actual journal run identity differs')
            with localcontext() as ctx:
                ctx.prec=28  # Bound producer Decimal context; this is not a new rounding policy.
                result = audit_account(journal,basis,sessions,official_days,row)
            result.update(model=model,model_version=journal['model'],scenario=scenario,account_basis=basis,run_id=journal['run_id'],
                audit_implementation_version=AUDIT_IMPLEMENTATION_VERSION,replay_identity=ident)
            result_path = target/(model+'__'+scenario+'__'+basis+'.json')
            write_new(result_path,result)
            results.append(result)
            del journal  # Never keep another actual journal alive during the next bounded read.
        queue_count=sum(r['target_limit_BUY_zero_fill_count'] for r in results)
        require(queue_count == 19, 'independent actual target zero-fill aggregate differs')
        journal_stats_after = {}
        for row in rows:
            ident = row['replay_identity']; p = Path(ident['path']); st = p.stat()
            observed = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            require(p.is_file() and not p.is_symlink() and observed == reader.observed_stats[str(p)], 'journal changed after its audit')
            journal_stats_after[str(p)] = dict(device=st.st_dev,inode=st.st_ino,bytes=st.st_size,mtime_ns=st.st_mtime_ns)
        guard_bindings = list(reader.bindings[:len(reader.bindings)-36])
        for ident in guard_bindings:
            reader.read(Path(ident['path']),ident['sha256'],ident['bytes'])
        output_receipt=dict(schema_version=1,audit_implementation_version=AUDIT_IMPLEMENTATION_VERSION,
            status='COMPLETED_READONLY_PARTIAL_LEDGER_AUDIT_NOT_ACCOUNT_COMPLETION',
            created_utc=datetime.now(timezone.utc).isoformat(), research_evidence=True, actual_journals_audited=36,
            actual_ledger_hash_chains_recomputed=36, full_continuous_accounts=0, target_limit_BUY_zero_fill_count=queue_count,
            nonnegative_constraint_violating_runs=sum(not r['nonnegative_cash_quantity_constraint_pass'] for r in results),
            source_bindings=reader.bindings, actual_bytes_read=reader.total, results=results,
            metadata_and_code_actual_SHA_rechecked_before_publication=True,
            journal_fresh_STAT_after_audit_not_a_second_byte_hash=journal_stats_after,
            inspector_receipt_sha256=REVIEW_SHA, actual_finished_wrapper_receipt_sha256=WRAPPER_SHA,
            no_engine_replay_model_forward_score_CSV_NPY_checkpoint_network_or_original_journal_read=True,
            external_symbol_close_valuation='UNKNOWN', broker_tax_FIFO_fraction_and_delisting_qualification='UNKNOWN',
            economic_target_accepted=False, full_PLAN_complete=False, all_PLAN_acceptance_complete=False, point_net_return=None)
        write_new(target/'actual_partial_ledger_audit_receipt_v1.json',output_receipt)
        return output_receipt
    except BaseException as error:
        write_new(target/'failure_receipt_v1.json',dict(status='FAIL_CLOSED_READONLY_AUDIT_NOT_COMPLETED',
            created_utc=datetime.now(timezone.utc).isoformat(), audit_implementation_version=AUDIT_IMPLEMENTATION_VERSION,
            error_type=type(error).__name__, reason=str(error),
            successfully_audited_journal_count=len(results), actual_bytes_read=reader.total, source_bindings=reader.bindings,
            economic_target_accepted=False, full_PLAN_complete=False, all_PLAN_acceptance_complete=False))
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('contract','audit'), nargs='?', default='contract')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--contract-file'); parser.add_argument('--contract-sha'); parser.add_argument('--output')
    args=parser.parse_args()
    if args.command == 'contract':
        require(not args.execute, 'contract never executes')
        print(json.dumps(pure_contract(),ensure_ascii=False,indent=2)); return
    require(args.execute and args.contract_file and args.contract_sha and args.output,
            'explicit execute and externally pinned contract/output required')
    result=execute(args.contract_file,args.contract_sha,args.output)
    print(json.dumps({key:result[key] for key in ('status','actual_journals_audited','full_continuous_accounts',
        'nonnegative_constraint_violating_runs','economic_target_accepted','full_PLAN_complete')},indent=2))


if __name__ == '__main__':
    main()
