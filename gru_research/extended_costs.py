"""Research-only 2025--2026 fee/date extension of the frozen cash executor.

No production date gate, share lattice, capacity, cash, lot, risk or T+1 rule is
altered. This remains an assumed raw-TWAP replay, not executable depth evidence.
Dividend tax is deliberately outside trading charges and requires its ledger.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'research_v3/src'))
from causal_cash_book import D
from next_session_execution import CostScenario, NextSessionExecutor, CN

COMMISSION_SCOPE = 'includes_exchange_and_regulatory_excludes_transfer_stamp_dividend_tax'
OFFICIAL_EVIDENCE = {
    'investor_fee_summary': 'https://one.sse.com.cn/onething/gptz/',
    'stamp_reduction_2023_39': 'https://shanghai.chinatax.gov.cn/tax/zcfw/zcfgk/yhs/202308/t468451.html',
    'sse_charge_2025': 'https://www.sse.com.cn/services/tradingservice/charge/ssecharge/c/c_20250102_10767593.shtml',
    'sse_charge_2026': 'https://www.sse.com.cn/services/tradingservice/charge/ssecharge/c/c_20251231_10803655.shtml',
}
RESEARCH_BEGIN = date(2025, 1, 1)
RESEARCH_END = date(2026, 12, 31)
EXECUTION_EVIDENCE = 'ASSUMED_FIVE_MINUTE_RAW_OPEN_TWAP_WITH_PRIOR_VOLUME_CAP_NOT_OBSERVED_EXECUTABLE_DEPTH'


class ResearchCostScenario(CostScenario):
    """Keeps original commission/minimum/slippage assumptions and cent rounding."""
    def fees(self, day, side, value):
        if type(day) is not date:
            raise ValueError('plain dated trading fee required')
        if date(2017, 1, 1) <= day <= date(2023, 12, 31):
            return super().fees(day, side, value)
        if not RESEARCH_BEGIN <= day <= RESEARCH_END or side not in ('BUY', 'SELL') or D(value) <= 0:
            raise ValueError('research fee evidence bounded to 2025--2026; legacy 2017--2023 retained')
        money = lambda x: D(x).quantize(D('.01'), rounding=ROUND_HALF_UP)
        commission = money(max(D(self.minimum_commission), D(value) * D(self.commission_rate)))
        # 0.01 per mille transfer, bilateral. The exchange/regulatory component
        # (0.0541 per mille) is already included in commission: never add twice.
        transfer = money(D(value) * D('.00001'))
        stamp = money(D(value) * D('.0005')) if side == 'SELL' else D(0)
        return {'commission': commission, 'transfer': transfer, 'stamp': stamp,
            'total': commission + transfer + stamp,
            'dividend_tax': 'must_be_reconciled_in_action_ledger'}

    def evidence(self):
        return {'research_begin': str(RESEARCH_BEGIN), 'research_end': str(RESEARCH_END),
            'official_urls': OFFICIAL_EVIDENCE, 'stamp_sell_only': '.0005', 'transfer_bilateral': '.00001',
            'handling_regulatory_included_in_commission': True,
            'broker_commission_and_slippage': 'ORIGINAL_FROZEN_SCENARIO_ASSUMPTIONS',
            'commission_rate': str(self.commission_rate), 'minimum_commission': str(self.minimum_commission),
            'slippage_bps': str(self.slippage_bps), 'net_dividend_tax_claim': False}


def research_costs(stress=False):
    return ResearchCostScenario('STRESS' if stress else 'PRIMARY',
        D('.0005') if stress else D('.0003'), D(5), D(20) if stress else D(10),
        COMMISSION_SCOPE, 'FROZEN_PROTOCOL_V3_COMMISSION_MIN5_SLIPPAGE_WITH_OFFICIAL_2025_2026_STAMP_TRANSFER:'
        + '|'.join(OFFICIAL_EVIDENCE.values()))


class ResearchTWAPExecutor(NextSessionExecutor):
    """Inherited atomic execution/accounting with an explicit research date gate.

The inherited method uses its historical argument name ``raw_open``. The
research provider must supply the mean of five genuine unadjusted minute opens
for 09:31--09:35, and use that same price as its execution-window mark.
No daily OPEN proxy or adjusted price is accepted as evidence by this adapter.
The journal keeps the legacy ``OPEN:`` transaction ID namespace for exact
account compatibility; outcome metadata states the actual TWAP scenario.
"""
    def __init__(self, account, calendar, first_day, costs, calendar_source):
        if not isinstance(costs, ResearchCostScenario):
            raise ValueError('research fee/date schedule must be bound explicitly')
        super().__init__(account, calendar, first_day, costs, calendar_source)

    def _validate_order(self, o):
        if date(2017, 1, 1) <= o.execute_on <= date(2023, 12, 31):
            return super()._validate_order(o)
        # Exact frozen validation sequence, with only the final dated guard
        # extended to the two officially evidenced research years.
        if not o.order_id or not o.symbol or not o.evidence or o.side not in ('BUY', 'SELL'):
            raise ValueError('invalid order identity/evidence')
        if type(o.quantity) is not int or o.quantity <= 0 or type(o.capacity_shares) is not int or o.capacity_shares < 0 or D(o.limit_price) <= 0:
            raise ValueError('invalid fixed quantity/limit/capacity')
        if o.execute_on not in self.calendar or o.execute_on < self.first:
            raise ValueError('off-calendar order')
        i = self.calendar.index(o.execute_on)
        previous = self.calendar[i - 1]
        earliest = datetime.combine(previous, time(15), CN)
        latest = datetime.combine(o.execute_on, time(9, 30), CN)
        if o.decision_at.tzinfo is None or not earliest <= o.decision_at < latest:
            raise ValueError('order must use previous-session signal before next open')
        if o.capacity_asof.tzinfo is None or o.capacity_asof > o.decision_at or o.capacity_asof.astimezone(CN).date() > previous:
            raise ValueError('participation cap uses future information')
        if self.account.day != o.execute_on or self.account.closed:
            raise ValueError('account session does not match execution')
        if i + 1 >= len(self.calendar):
            raise ValueError('next official session required before submission')
        if not RESEARCH_BEGIN <= o.execute_on <= RESEARCH_END:
            raise ValueError('execution research date evidence bounded to 2025--2026')

    def execute(self, o, rule, raw_open, open_marks, members, **kwargs):
        if '0931_0936_RAW_OPEN_TWAP=' not in kwargs.get('valuation_source',''):
            raise ValueError('explicit genuine raw-minute TWAP provider evidence required; daily OPEN proxy prohibited')
        return super().execute(o, rule, raw_open, open_marks, members, **kwargs)

    def _outcome(self, o, identity, state, q, price, fees, risk_after=None):
        result = super()._outcome(o, identity, state, q, price, fees, risk_after)
        result['execution_evidence'] = EXECUTION_EVIDENCE
        result['fee_evidence'] = self.costs.evidence()
        result['fill_id_namespace'] = 'LEGACY_OPEN_PREFIX_COMPATIBILITY_ONLY'
        result['signal_cutoff'] = datetime.combine(self.calendar[self.calendar.index(o.execute_on)-1], time(15), CN)
        result['execution_window_start'] = datetime.combine(o.execute_on, time(9, 31), CN)
        result['execution_window_end'] = datetime.combine(o.execute_on, time(9, 36), CN)
        result['five_minute_price_available_at'] = result['execution_window_end']
        result['recorded_fill_time'] = result['execution_window_end'] if q else None
        result['account_engine_time_resolution'] = 'DATED_BOOK_WITH_EXPLICIT_0936_TWAP_OUTCOME_TIMESTAMP'
        result['net_return_claim'] = False
        if q:
            self.account.record('RESEARCH_RAW_TWAP_FILL_WINDOW', {
                'fill_id': 'OPEN:' + o.order_id, 'window_start': result['execution_window_start'],
                'window_end': result['execution_window_end'], 'recorded_fill_time': result['recorded_fill_time'],
                'price_available_at': result['five_minute_price_available_at'], 'quantity': q,
                'raw_reference_kind': 'MEAN_FIVE_COMPLETE_RAW_MINUTE_OPENS',
                'execution_evidence': EXECUTION_EVIDENCE})
        self.outcomes[o.order_id] = deepcopy(result)
        self.events[-1] = deepcopy(result)
        return deepcopy(result)
