"""Meaningful cash/T+1/date isolation checks, synthetic interface fixtures only."""
import unittest
import sys
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal as D
from dataclasses import replace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extended_costs import research_costs, ResearchTWAPExecutor, EXECUTION_EVIDENCE
from causal_cash_book import ResearchAccount
from next_session_execution import CostScenario, DatedRule, FixedOrder, CN


class ExtendedCostsTests(unittest.TestCase):
    def setUp(self):
        self.days = [date(2025, 1, d) for d in (2, 3, 6, 7, 8, 9)]
        self.costs = research_costs()
        self.book = ResearchAccount(1000000, 'EXPLICIT_SYNTHETIC_UNIT_CAPITAL', sessions=self.days,
            calendar_source='EXPLICIT_SYNTHETIC_UNIT_CALENDAR', fee_source='SYNTHETIC_RESEARCH_FEES')
        self.engine = ResearchTWAPExecutor(self.book, self.days, self.days[1], self.costs, 'EXPLICIT_SYNTHETIC_UNIT_CALENDAR')
        self.book.begin(self.days[1])
        self.rule = DatedRule(self.days[1], 'SYNTHETIC_DATED_RULE', datetime(2025,1,3,9,15,tzinfo=CN),
            D('.01'), D(9), D(11), 100, 100, 100, 100, 1000000, True, True, True, True)
        self.order = FixedOrder('buy', 'A', 'BUY', 3000, D('10.2'), datetime(2025,1,2,15,1,tzinfo=CN),
            self.days[1], 10000, datetime(2025,1,2,15,tzinfo=CN), 'SYNTHETIC_PRIOR_CAP_RAW_TWAP')

    def execute(self, order=None, rule=None):
        return self.engine.execute(order or self.order, rule or self.rule, D(10), {'A': D(10)}, {'A'},
            valuation_source='0931_0936_RAW_OPEN_TWAP=SYNTHETIC_RAW_TWAP_MARKS', membership_source='SYNTHETIC_DATED_MEMBERS', eligible_symbols={'A'})

    def test_exact_fee_values_scope_and_bounded_dates(self):
        self.assertEqual(self.costs.fees(date(2025,1,3), 'BUY', 100000)['total'], D(31))
        self.assertEqual(self.costs.fees(date(2026,9,23), 'SELL', 100000)['total'], D(81))
        self.assertEqual(self.costs.fees(date(2026,9,23), 'BUY', 1000)['total'], D('5.01'))
        self.assertEqual(research_costs(True).fees(date(2026,9,23), 'SELL', 100000)['total'], D(101))
        for day in [date(2024,1,2), date(2027,1,4)]:
            with self.assertRaises(ValueError): self.costs.fees(day, 'BUY', 100000)
        original = CostScenario('old', D('.0003'), D(5), D(10), self.costs.commission_scope, 'SYNTHETIC_LEGACY')
        self.assertEqual(original.fees(date(2023,8,25), 'SELL', 100000), self.costs.fees(date(2023,8,25), 'SELL', 100000))
        with self.assertRaises(ValueError): original.fees(date(2025,1,3), 'BUY', 100000)
        self.assertTrue(self.costs.evidence()['handling_regulatory_included_in_commission'])

    def test_cash_slippage_once_and_tplusone_sale(self):
        out = self.execute()
        self.assertEqual(out['price'], D('10.01'))
        self.assertEqual(out['fees']['total'], D('9.31'))
        self.assertEqual(self.book.cash, D('969960.69'))
        self.assertEqual(self.book.sellable('A'), 0)
        self.assertEqual(out['execution_evidence'], EXECUTION_EVIDENCE)
        self.assertEqual(out['recorded_fill_time'], datetime(2025,1,3,9,36,tzinfo=CN))
        self.assertEqual(out['recorded_fill_time'], out['execution_window_end'])
        self.assertEqual(self.execute(), out)  # No duplicate cash debit.
        with self.assertRaisesRegex(ValueError, 'unavailable sale'):
            self.execute(replace(self.order, order_id='same_day_sell', side='SELL', quantity=100, limit_price=D('9.8')))
        self.assertEqual(self.book.cash, D('969960.69'))
        self.book.close(); self.book.begin(self.days[2])
        rule = replace(self.rule, day=self.days[2], available_at=datetime(2025,1,6,9,15,tzinfo=CN))
        sell = replace(self.order, order_id='next_day_sell', side='SELL', quantity=100, limit_price=D('9.8'),
            execute_on=self.days[2], decision_at=datetime(2025,1,3,15,1,tzinfo=CN), capacity_asof=datetime(2025,1,3,15,tzinfo=CN))
        result = self.execute(sell, rule)
        self.assertEqual(result['filled'], 100)
        self.assertEqual(result['price'], D('9.99'))
        self.assertEqual(result['fees']['total'], D('5.51'))
        self.assertEqual(self.book.cash, D('970954.18'))
        self.assertEqual(self.book.quantity('A'), 2900)

    def test_capacity_status_limit_and_prior_date_guards_stay_frozen(self):
        limited = replace(self.order, capacity_shares=1500)
        self.assertEqual(self.execute(limited)['filled'], 1500)
        self.assertEqual(self.execute(replace(limited, order_id='capacity_second'))['filled'], 0)
        with self.assertRaises(ValueError):
            self.execute(replace(limited, order_id='future_cap', capacity_asof=datetime(2025,1,3,9,tzinfo=CN)))
        with self.assertRaises(ValueError):
            self.execute(replace(limited, order_id='late', decision_at=datetime(2025,1,3,9,30,tzinfo=CN)))
        before = self.book.cash
        self.assertEqual(self.execute(replace(limited, order_id='blocked'), replace(self.rule, tradable_at_open=False))['filled'], 0)
        self.assertEqual(self.book.cash, before)

    def test_cross_object_failure_restores_real_cash_lots_and_retry(self):
        original = self.engine._outcome
        def fault(*args, **kwargs):
            raise RuntimeError('SYNTHETIC_AFTER_FILL_FAILURE')
        self.engine._outcome = fault
        with self.assertRaises(RuntimeError): self.execute()
        self.assertEqual(self.book.cash, D(1000000)); self.assertEqual(self.book.quantity('A'), 0)
        self.assertEqual(self.engine.daily_used_capacity, {})
        self.engine._outcome = original
        self.assertEqual(self.execute()['filled'], 3000)


if __name__ == '__main__':
    unittest.main()
