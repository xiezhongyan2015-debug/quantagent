"""Provider isolation and genuine raw-TWAP mapping; no market replay/fit."""
import sys
from pathlib import Path
import unittest
from datetime import date, datetime, time
from decimal import Decimal
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import trading_provider as p
from prior_day_order_planner import Snapshot, Security, Holding, FeeReserve
from next_session_execution import DatedRule
from pretax_portfolio_replay import MarketContext


class BaseFixture:
    """Explicitly synthetic interface fixture, never a market training input."""
    calendar = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
    calendar_source = 'SYNTHETIC_UNIT_CALENDAR'
    at = datetime(2025, 1, 2, 15, 1, tzinfo=p.CN)
    fees = FeeReserve(Decimal('.00032'), Decimal(5), Decimal('.00132'), Decimal(5), at, 'SYNTHETIC_FEES')
    holding = Holding('sh600004', 100, 100, 100, Decimal(10), at, 'SYNTHETIC_ACTUAL_LOTS')
    snapshot = Snapshot(at, date(2025, 1, 3), tuple(calendar), calendar_source, date(2025, 1, 3),
        (Security('sh600004', True, Decimal(999), Decimal(10), Decimal(10000), 20, True, None,
                  date(2025, 1, 2), at, 'SYNTHETIC_MARKET'),
         Security('sz000001', True, Decimal(999), Decimal(12), Decimal(10000), 20, True, None,
                  date(2025, 1, 2), at, 'SYNTHETIC_MARKET')),
        (holding,), Decimal(9000), Decimal(10000), Decimal(0), at, 'SYNTHETIC_CLOSED_BOOK', 0, fees)
    rule = DatedRule(date(2025, 1, 3), 'SYNTHETIC_DATED_RULE', datetime(2025, 1, 3, 9, 15, tzinfo=p.CN),
        Decimal('.01'), Decimal(9), Decimal(11), 100, 100, 100, 100, 1000000, True, True, True, True)

    def planning_snapshot(self, previous, day, first, book, model, costs):
        assert model == 'BASE_MODEL'
        return self.snapshot

    def market_context(self, day, symbols, phase):
        return MarketContext(day, {s: Decimal(10) for s in symbols}, frozenset(symbols),
            frozenset(symbols), 'SYNTHETIC_RAW_OPEN_VALUATION', 'SYNTHETIC_MEMBER', {s: self.rule for s in symbols})

    def require_action_coverage(self, symbols, years):
        self.request = (set(symbols), tuple(years))

    def record_actions(self, book, day):
        return ('UNCHANGED_ACTION_CALL', book, day)


class MissingWindowFixture:
    def window(self, signal, symbols):
        ns = p.utc_ns(signal)
        start = p.utc_ns(datetime(2025, 1, 3, 9, 31, tzinfo=p.CN))
        return p.TWAPWindow(ns, start, start + 5 * 60 * 10**9,
            {s: p.TWAPQuote(s, None, False, False, None, 'SYNTHETIC_MISSING_WINDOW') for s in symbols},
            'SYNTHETIC_WINDOW_SOURCE')


def scores(available=None):
    d = {'symbol': ['600004.SH'], 'signal_time': [p.utc_ns(datetime(2025, 1, 2, 15, tzinfo=p.CN))],
         'score': [.5], 'model_version': ['GRU_TEST']}
    if available is not None:
        d['available_at'] = [available]
    return p.pd.DataFrame(d)


def adapter(f=None):
    return p.GRUTradingProviderAdapter(BaseFixture(), scores() if f is None else f,
        model_version='GRU_TEST', planning_model='BASE_MODEL', score_source='SYNTHETIC_HASHED_SCORE',
        arrival_policy=p.ARRIVAL_ASSUMPTION, minute_source=MissingWindowFixture())


class TradingProviderTests(unittest.TestCase):
    def test_only_scores_change_missing_names_cash_and_lots_stay(self):
        a = adapter()
        old = a.base.snapshot
        out = a.planning_snapshot(date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 3),
                                  object(), 'GRU_TEST', object())
        self.assertEqual([s.symbol for s in out.securities], [s.symbol for s in old.securities])
        self.assertEqual(out.securities[0].score, Decimal('.5'))
        self.assertIsNone(out.securities[1].score)
        self.assertEqual(out.holdings, old.holdings)
        self.assertEqual((out.cash, out.nav, out.fees), (old.cash, old.nav, old.fees))
        self.assertEqual(a.base.snapshot.securities[0].score, Decimal(999))

    def test_future_score_arrival_and_label_fields_are_rejected(self):
        a = adapter(scores(p.utc_ns(datetime(2025, 1, 2, 16, tzinfo=p.CN))))
        with self.assertRaises(p.IncompleteTradingEvidence):
            a.planning_snapshot(date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 3),
                                object(), 'GRU_TEST', object())
        contaminated = scores(); contaminated['y4'] = .1
        with self.assertRaisesRegex(ValueError, 'whitelist'):
            adapter(contaminated)

    def test_missing_twap_blocks_fill_keeps_sourced_holding_mark_and_rule_original(self):
        a = adapter()
        c = a.market_context(date(2025, 1, 3), {'sh600004'}, 'OPEN')
        self.assertEqual(c.prices['sh600004'], Decimal(10))
        self.assertFalse(c.rules['sh600004'].tradable_at_open)
        self.assertTrue(a.base.rule.tradable_at_open)
        self.assertIn('VALUATION_ONLY_FILL_PROHIBITED', c.valuation_source)
        self.assertIsNone(a.events[-1]['no_fill_windows'][0]['fill_price'])
        self.assertTrue(a.events[-1]['no_fill_windows'][0]['holding_retained'])
        self.assertFalse(a.execution_coverage_complete)

    def test_all_four_windows_use_genuine_minute_raw_open_twap(self):
        source = p.FrozenMinuteTWAPSource()
        raw = p.pd.read_parquet(source.artifacts / 'minute_hourly_raw' / '2025-01-02.parquet')
        good = raw.groupby('symbol').window_tradable.all()
        symbol = good[good].index[0]
        expected = ['2025-01-02 10:31', '2025-01-02 13:01', '2025-01-02 14:01', '2025-01-03 09:31']
        for clock, start in zip(['10:30', '11:30', '14:00', '15:00'], expected):
            signal = p.pd.Timestamp('2025-01-02 ' + clock, tz=p.CN)
            w = source.window(signal, [symbol])
            first = p.pd.Timestamp(w.execution_start, tz='UTC').tz_convert(p.CN)
            self.assertEqual(first.strftime('%Y-%m-%d %H:%M'), start)
            self.assertEqual(w.execution_end - w.execution_start, 5 * 60 * 10**9)
            day = first.strftime('%Y-%m-%d')
            observed = p.pd.read_parquet(source.artifacts / 'minute_hourly_raw' / f'{day}.parquet')
            row = observed[(observed.symbol == symbol) & (observed.window_start_time == w.execution_start)].iloc[0]
            self.assertEqual(w.quotes[p.cash_symbol(symbol)].raw_price, Decimal(str(row.window_raw_price)))
            self.assertEqual(w.price_kind, 'MEAN_FIVE_COMPLETE_RAW_MINUTE_OPENS_NO_VWAP_NO_ADJUSTED_PRICE')

    def test_legacy_scope_and_action_contract_not_rewritten(self):
        a = adapter()
        report = a.compatibility([2025, 2026])
        self.assertFalse(report['old_executor_fee_date_guard_supports_requested_years'])
        self.assertTrue(report['requires_separately_frozen_verified_extended_executor_for_2025_2026'])
        self.assertFalse(report['net_return_claim'])
        a.require_action_coverage({'sh600004'}, [2025, 2026])
        self.assertEqual(a.base.request, ({'sh600004'}, (2025, 2026)))


if __name__ == '__main__':
    unittest.main()
