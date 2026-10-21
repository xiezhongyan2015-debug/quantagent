"""Recompute saved terms under the same frozen simulation rules, never execute.

The caller must bind expected hashes to saved custody. The original rule's
facts_verified/source flags are inputs, not credentials established here.
"""
from collections import defaultdict
from decimal import Decimal
import hashlib
import re

from .kernel import FROZEN, module
from .ledger import digest, need

VERSION = 'frozen_account_terms_v1'
BACKEND = 'meta-v3-saved-execution-stream-v1'
RULE_SOURCES = ('raw_share_ledger.py', 'raw_portfolio_backtest.py', 'corporate_action_adapter.py')
POLICY = {'version': VERSION, 'backend': BACKEND, 'rule_sources': list(RULE_SOURCES),
    'mode': 'same_frozen_simulation_rules', 'fees': 'all frozen fee_breakdown fields, per saved fill',
    'tax': 'CashDividendAdapter per original announcement and full calendar; portfolio_tax_valuation at every saved EOD',
    'marks': 'original saved mark_evidence only; market files never opened',
    'capital': 'one original full initial cash deposit; no resizing or replenishment',
    'calendar': 'exact plan calendar including inactive days; no prefix treated as complete',
    'rounding_policy': 'aggregate_half_up_simulated', 'formal_target_success': False}


def source_pins():
    return {name: hashlib.sha256((FROZEN / name).read_bytes()).hexdigest() for name in RULE_SOURCES}


def _number(value):
    need(type(value) in (str, int, float) and len(str(value)) <= 100, 'bounded numeric amount required')
    value = Decimal(str(value))
    need(value.is_finite() and abs(value.as_tuple().exponent) <= 100, 'nonfinite or oversized amount')
    return value


def _same(actual, expected, reason):
    need(digest(actual) == digest(expected), reason)


def recompute_terms(raw_plan, body, *, expected_plan_sha256, expected_result_sha256, input_kind):
    """Read-only saved-event replay plus frozen fee/tax recomputation.

    Only the streamed backend is supported. Structural and L2 are rejected.
    Expected identities must come from the caller's independently anchored
    custody; supplying matching hashes alone does not establish that custody.
    """
    need(input_kind in ('generated_fixture', 'caller_bound_saved_records'), 'explicit input kind required')
    need(all(type(x) is str and re.fullmatch('[0-9a-f]{64}', x) for x in
             (expected_plan_sha256, expected_result_sha256)), 'explicit expected hashes required')
    need(raw_plan.get('version') == BACKEND and 'archive_storage_policy' not in raw_plan, 'unsupported backend/storage')
    plan = {key: value for key, value in raw_plan.items() if key != 'plan_sha256'}
    need(raw_plan.get('plan_sha256') == digest(plan) == expected_plan_sha256, 'frozen plan hash mismatch')
    need(digest(body) == expected_result_sha256, 'saved result hash mismatch')
    need(raw_plan['policy']['rounding_policy'] == POLICY['rounding_policy'], 'unsupported frozen rounding policy')
    pins = source_pins()
    need(all(raw_plan['engine_sources'].get(name) == value for name, value in pins.items()), 'frozen rule source drift')
    ca, portfolio, ledger_module = (module(name) for name in ('corporate_action_adapter', 'raw_portfolio_backtest', 'raw_share_ledger'))
    need(body['version'] == portfolio.VERSION, 'saved portfolio version differs')
    days = raw_plan['calendar']
    need(type(days) is list and 3 <= len(days) <= 512 and days == sorted(set(days)), 'invalid frozen calendar')
    need(all(type(day) is str and '2017-01-01' <= day <= '2021-12-31' for day in days), 'development calendar only')
    _same(body['calendar'], days, 'body calendar differs')
    _same(body['replay_dates'], days, 'replay calendar differs')
    _same(body['genesis']['trading_days'], days, 'genesis calendar differs')
    _same([row['date'] for row in body['daily']], days, 'omitted/reordered daily valuation')
    initial = _number(raw_plan['initial_cash'])
    need(initial > 0 and _number(body['initial_cash']) == initial, 'original capital differs')
    need(type(body['journal']) is list and len(body['journal']) <= 8192 and
         type(body['trades']) is list and len(body['trades']) <= 8192, 'saved account event bound')
    adapters = [ca.CashDividendAdapter(ca.CashDividendAnnouncement(**notice), days,
        rounding_policy=raw_plan['policy']['rounding_policy']) for notice in raw_plan['corporate_actions']]
    need(len({item.manifest()['announcement']['action_id'] for item in adapters}) == len(adapters), 'duplicate action adapter')
    _same(body['manifest']['corporate_actions'], [item.manifest() for item in adapters], 'frozen action manifest differs')
    recorded_policy = body['manifest']['policy']
    need(digest(recorded_policy) == body['manifest']['policy_sha256'], 'portfolio policy hash mismatch')
    need(recorded_policy['corporate_action_rounding_policy'] == raw_plan['policy']['rounding_policy'] and
         _number(recorded_policy['initial_cash']) == initial, 'portfolio terms differ from plan')
    for key in ('participation_rate', 'slippage_fraction'):
        need(_number(recorded_policy[key]) == _number(raw_plan['policy'][key]), 'saved execution term differs: ' + key)
    ledger = ledger_module.RawShareLedger.replay(body['genesis'], [])
    fees, fill_ids = [], {}
    by_date = defaultdict(lambda: Decimal(0))
    for trade in body['trades']:
        eid = trade['event_id']
        need(type(eid) is str and eid not in fill_ids, 'duplicate trade event')
        need(trade['date'] in days and trade['side'] in ('buy', 'sell') and trade['symbol'] in raw_plan['codes'], 'trade outside original scope')
        need(type(trade['quantity']) is int and trade['quantity'] > 0, 'invalid funded fill quantity')
        expected = portfolio.fee_breakdown(trade['side'], trade['date'], trade['quantity'], trade['raw_price'])
        _same(trade['fees'], expected, 'frozen fee breakdown mismatch: ' + eid)
        by_date[trade['date']] += _number(expected['total'])
        fill_ids[eid] = trade
        fees.append({'event_id': eid, 'date': trade['date'], 'recomputed_fees': expected})
    cursor, deposits, seen = 0, 0, set()
    daily, cumulative = [], Decimal(0)
    for row in body['daily']:
        valuation = row['valuation']
        as_of = row['date'] + 'T15:05:00+08:00'
        need(valuation['as_of'] == as_of, 'valuation outside frozen EOD')
        while ledger.snapshot()['journal_head'] != valuation['ledger_head']:
            need(cursor < len(body['journal']), 'valuation head absent from journal')
            entry = body['journal'][cursor]; event = entry['event']; data = event['data']
            if event['kind'] == 'cash_deposit':
                deposits += 1
                need(deposits == 1 and _number(data['amount']) == initial, 'capital deposit omitted/resized/repeated')
            if event['kind'] in ('buy_fill', 'sell_fill'):
                eid = event['event_id']
                need(eid in fill_ids and eid not in seen, 'journal fill missing/duplicated in trades')
                trade = fill_ids[eid]
                need(event['kind'] == trade['side'] + '_fill' and data['symbol'] == trade['symbol'] and
                     data['quantity'] == trade['quantity'] and _number(data['raw_price']) == _number(trade['raw_price']) and
                     _number(data['fees']) == _number(trade['fees']['total']) and
                     event['effective_at'].startswith(trade['date'] + 'T'), 'trade/journal terms differ')
                seen.add(eid)
            applied = ledger.apply(event, as_of=entry['applied_at'])
            need(applied['status'] == 'applied', 'saved event no longer applies')
            _same(ledger.journal[-1], entry, 'saved journal replay differs')
            cursor += 1
        need(deposits == 1 and _number(ledger.snapshot()['external_cash_flow']) == initial, 'full original capital not preserved')
        recomputed = ca.portfolio_tax_valuation(ledger, adapters, valuation['raw_ledger_valuation']['mark_evidence'], as_of=as_of)
        _same(recomputed, valuation, 'frozen daily tax valuation mismatch: ' + row['date'])
        for key in ('simulated_net_asset_value', 'simulated_net_pnl', 'gross_asset_value', 'cash_available',
                    'realized_tax_unpaid', 'remaining_tax_reserve', 'conservative_cash_after_tax_reserve'):
            need(_number(row[key]) == _number(recomputed[key]), 'daily projection mismatch: ' + key)
        need(_number(row['cash_receivable_gross']) == _number(recomputed['raw_ledger_valuation']['cash_receivable_gross']), 'daily receivable differs')
        cumulative += by_date[row['date']]
        need(cumulative == _number(row['fees_paid_cumulative']) == _number(ledger.snapshot()['fees_paid']), 'cumulative frozen fees differ')
        daily.append({'date': row['date'], 'ledger_head': recomputed['ledger_head'], 'valuation_sha256': digest(recomputed),
            **{key: recomputed[key] for key in ('simulated_net_asset_value', 'realized_tax_unpaid', 'remaining_tax_reserve', 'estimated_total_tax_unpaid')},
            'reserve_reports_sha256': digest(recomputed['reserve_reports'])})
    need(cursor == len(body['journal']) and seen == set(fill_ids), 'omitted terminal journal/fills')
    _same(ledger.snapshot(), body['final_snapshot'], 'saved final snapshot differs')
    need(source_pins() == pins, 'frozen rule sources changed during audit')
    need(digest(body) == expected_result_sha256 and digest(plan) == expected_plan_sha256, 'input changed during audit')
    return {'version': VERSION, 'input_kind': input_kind, 'policy_sha256': digest(POLICY),
        'plan_sha256': expected_plan_sha256, 'result_sha256': expected_result_sha256, 'rule_source_pins': pins,
        'days_checked': len(daily), 'fills_checked': len(fees), 'fees_paid': str(cumulative),
        'fee_rows': fees, 'daily_rows': daily, 'fees_sha256': digest(fees), 'daily_terms_sha256': digest(daily),
        'same_frozen_fee_and_tax_rules_matched': True, 'algorithmically_independent': False,
        'custody_verified_by_this_function': False, 'sources_authenticated': False,
        'historical_arrival_verified': False, 'capacity_verified': False, 'execution_valid': False,
        'formal_target_success': False, 'market_files_opened': False, 'strategy_executed': False,
        'limitations': ['Rule implementation is reused, not an algorithmically independent tax/fee implementation.',
            'Saved marks, issuer facts, fees and availability remain declared inputs; their real-world completeness is not certified.',
            'Hash expectations require caller custody; matching hashes are not signatures or a formal credential.']}
