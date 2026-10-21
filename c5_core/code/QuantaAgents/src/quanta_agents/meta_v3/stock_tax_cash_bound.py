"""Cash protection for unresolved stock-dividend taxes; never a tax assessment.

The bound allows arbitrary partitions of INTEGER original shares and cent
rounding of each nonnegative cash/bonus component no higher than its ceiling.
It reserves at most20percent before rounding. This explicit mathematical
envelope neither proves historical tax-lot rules nor produces net performance.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, localcontext

from .stock_distribution import (
    AdapterError, RawShareLedger, StockDistributionRightsAdapter,
)
from .kernel import module

_CA = module("corporate_action_adapter")
VERSION = "unresolved-stock-tax-integer-partition-cash-bound-v2"
CENT = Decimal("0.01")
RATE = Decimal("0.20")


def stock_tax_cash_bound(ledger, adapters, *, as_of):
    """Retain the full registered liability envelope even after selling shares.

Explicit paid taxes reduce the remaining cash reserve by the same cash outflow.
An unverified tax_final/zero declaration never releases it. Active cash-only
actions are listed as uncovered and suppress the all-tax spendable-cash result.
A recorded assessment above the envelope contradicts its protection and is
rejected even before payment, regardless of assessment finality.
"""
    if not isinstance(ledger, RawShareLedger) or not ledger.verify_state_commitment():
        raise AdapterError("valid committed raw ledger required")
    ledger._read_time(as_of)
    if type(adapters) is not list or len(adapters) > 64:
        raise AdapterError("bounded explicit stock-rights adapter list required")
    by_id = {}
    for adapter in adapters:
        if type(adapter) is not StockDistributionRightsAdapter:
            raise AdapterError("only pinned stock-rights adapters accepted")
        adapter._ledger(ledger, as_of)
        key = adapter.spec.action_id
        if key in by_id:
            raise AdapterError("duplicate stock action adapter")
        by_id[key] = adapter
    state = ledger.snapshot()
    stock_actions = {key: value for key, value in state['actions'].items()
                     if value['active'] and value['bonus_entitled'] > 0}
    missing = sorted(set(stock_actions) - set(by_id))
    if missing:
        raise AdapterError("uncovered active stock-distribution tax obligations: " + ','.join(missing))
    uncovered = sorted(key for key, action in state['actions'].items()
                       if action['active'] and key not in stock_actions)
    journal = ledger.journal
    rows = []
    with localcontext() as context:
        context.prec = 60
        reserve = Decimal('0')
        for key, action in sorted(stock_actions.items()):
            adapter = by_id[key]
            expected = adapter._expected_events(action['entitled_shares'])
            seen = set()
            for entry in journal:
                event = entry['event']
                if event['data'].get('action_id') != key:
                    continue
                if event['kind'] in {'tax_assessed', 'tax_paid'}:
                    # These are recorded events, not assertions that this module
                    # has authenticated their tax calculation or holding period.
                    continue
                stage = next((name for name, value in expected.items() if event == value), None)
                if stage is None:
                    raise AdapterError("physical event disagrees with pinned stock-rights policy")
                seen.add(stage)
            if not {'record', 'activate'} <= seen:
                raise AdapterError("pinned registration and activation events required")
            registered = action['entitled_shares']
            cash_unit = _CA._decimal(adapter.spec.gross_cash_per_share) * RATE
            bonus_unit = (_CA._decimal(adapter.spec.bonus_per_share)
                          * _CA._decimal(adapter.spec.bonus_par_value_cny) * RATE)
            # For any partition q=sum(q_i), round(q_i*x)<=q_i*ceil(x).
            # Apply this separately to cash and bonus; their fractional cents
            # must not cancel. Verified share-premium capitalization is excluded.
            cash_unit_bound = cash_unit.quantize(CENT, rounding=ROUND_CEILING)
            bonus_unit_bound = bonus_unit.quantize(CENT, rounding=ROUND_CEILING)
            ceiling = registered * (cash_unit_bound + bonus_unit_bound)
            paid = Decimal(action['tax_paid'])
            if not Decimal('0') <= paid <= ceiling:
                raise AdapterError("recorded tax payment contradicts the declared liability envelope")
            assessed = None if action['tax_due'] is None else Decimal(action['tax_due'])
            # Recording an assessment does not prove its tax algorithm, but a
            # larger known liability invalidates this smaller cash envelope.
            # Compare TOTAL due with the original ceiling, not with the
            # remaining reserve: prior payments reduce both unpaid and reserve.
            if assessed is not None and not paid <= assessed <= ceiling:
                raise AdapterError("recorded tax assessment contradicts the declared liability envelope")
            remaining = ceiling - paid
            reserve += remaining
            rows.append({
                'action_id': key, 'symbol': adapter.spec.symbol,
                'registered_original_shares': registered,
                'current_physical_shares_do_not_reduce_registered_tax_envelope': True,
                'source_sha256': adapter.spec.source_sha256,
                'rights_policy_sha256': adapter.manifest()['assumption_sha256'],
                'cash_tax_per_original_share_unrounded': str(cash_unit),
                'bonus_tax_per_original_share_unrounded': str(bonus_unit),
                'cash_tax_per_original_share_ceiling': format(cash_unit_bound, '.2f'),
                'bonus_tax_per_original_share_ceiling': format(bonus_unit_bound, '.2f'),
                'tax_cash_ceiling_before_paid': format(ceiling, '.2f'),
                'recorded_tax_paid': format(paid, '.2f'),
                'recorded_total_tax_assessed': None if assessed is None else format(assessed, '.2f'),
                'recorded_tax_unpaid': None if assessed is None else format(assessed - paid, '.2f'),
                'remaining_cash_reserve': format(remaining, '.2f'),
                'unverified_tax_final_does_not_release_reserve': True,
                'recorded_payment_recognized_as_outflow_not_tax_rule_adjudication': True,
            })
        cash = Decimal(state['cash'])
        residual = cash - reserve
        return {
            'version': VERSION, 'as_of': as_of, 'journal_head': state['journal_head'],
            'cash_available': format(cash, '.2f'),
            'total_stock_tax_cash_reserve': format(reserve, '.2f'),
            'cash_after_stock_tax_reserve_only': format(residual, '.2f'),
            'cash_shortfall_to_stock_tax_bound': format(max(-residual, Decimal('0')), '.2f'),
            'uncovered_other_active_tax_actions': uncovered,
            'spendable_cash_after_all_tax_reserves': None if uncovered else format(max(residual, Decimal('0')), '.2f'),
            'cash_receivables_and_stock_values_not_spendable_cash': True,
            'partition_envelope': 'integer original-share partitions; cash and bonus components each rounded no higher than ceiling cent',
            'maximum_unrounded_rate': '0.20', 'actions': rows,
            'tax_lineage_implemented': False, 'actual_tax_assessed_by_this_function': False,
            'historical_tax_rule_applicability_verified_by_this_function': False,
            'net_asset_value': None, 'research_execution_ready': False,
        }
