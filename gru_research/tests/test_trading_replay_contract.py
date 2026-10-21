"""Initial cash-mark integration with genuine dated members and no fake forecast."""
from datetime import date
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'gru_research'))
sys.path.insert(0, str(ROOT / 'research_v3/src'))
from pretax_portfolio_replay import MarketContext
from trading_inputs import historical_members
from extended_costs import research_costs
from trading_replay import replay_research_pretax
from tax_reserve_replay import replay_research_tax_reserve


class NoForecastProvider:
    calendar = [date(2025,1,d) for d in [2,3,6,7,8]]
    calendar_source = 'ACTUAL_IFIND_DATED_CALENDAR_WITH_EXPLICIT_UNIT_TEST_NO_FORECAST'
    def __init__(self):
        actual = historical_members(self.calendar)
        self.members = {day.date():frozenset(group.symbol) for day,group in actual.groupby('date')}
    def market_context(self, day, symbols, phase):
        if symbols:
            raise ValueError('test has no stock price or fabricated order')
        return MarketContext(day, {}, self.members[day], self.members[day],
            'NO_HOLDINGS_NO_PRICE_INPUT', 'ACTUAL_HISTORICAL_MEMBER_KEYS', {})
    def record_actions(self, book, day):
        return []
    def planning_snapshot(self, *args):
        raise ValueError('SOURCE_SCORE_INPUT_NOT_SUPPLIED')


class ReplayContractTests(unittest.TestCase):
    def check_driver(self, driver):
        provider = NoForecastProvider()
        result = driver(provider, model='NO_FORECAST_INTERFACE_TEST',
            valuation_days=provider.calendar[:4], costs=research_costs(), run_id='INITIAL_CASH_CONTEXT_CONTRACT_ONLY')
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(len(result['completed_marks']), 1)
        self.assertEqual(result['completed_marks'][0]['accounting_residual'], 0)
        self.assertEqual(result['completed_marks'][0]['nav'], 1000000)
        self.assertEqual(result['outcomes'], [])
        self.assertIn('SOURCE_SCORE_INPUT_NOT_SUPPLIED', result['failure']['reason'])
    def test_pretax_driver_reuses_original_market_context(self):
        self.check_driver(replay_research_pretax)
    def test_tax_reserve_driver_reuses_original_market_context(self):
        self.check_driver(replay_research_tax_reserve)


if __name__ == '__main__':
    unittest.main()
