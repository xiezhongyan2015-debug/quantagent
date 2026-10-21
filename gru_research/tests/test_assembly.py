import unittest
import numpy as np
import pandas as pd
from gru_research.assemble import normalize_cross_section, validate_slow_dates, verify_labels


class AssemblyTests(unittest.TestCase):
    def test_reference_is_historical_membership_and_nan_preserved(self):
        raw = np.array([[1, np.nan, 3], [2, np.nan, 3], [3, np.nan, 3], [10000, 5, 8]])
        member = np.array([1, 1, 1, 0], bool)
        normalized, stats = normalize_cross_section(raw, member)
        self.assertTrue(np.isnan(normalized[:3, 1]).all())
        np.testing.assert_array_equal(normalized[:3, 2], 0)
        raw[-1, 0] = -1e8
        again, _ = normalize_cross_section(raw, member)
        np.testing.assert_array_equal(normalized[:3], again[:3])
        self.assertTrue(np.abs(normalized[:, 0]).max() <= 5)

    def test_future_row_has_no_role_in_historical_normalization(self):
        rng = np.random.default_rng(11)
        x = rng.normal(size=(3, 500, 4))
        member = np.ones(500, bool)
        before, _ = normalize_cross_section(x[0], member)
        x[1:] += 1e10
        after, _ = normalize_cross_section(x[0], member)
        np.testing.assert_array_equal(before, after)

    def test_normalization_is_invariant_to_tiny_numeric_units(self):
        raw = np.arange(500, dtype=np.float64)[:, None] + 1
        member = np.ones(500, bool)
        usual, _ = normalize_cross_section(raw, member)
        tiny, _ = normalize_cross_section(raw * 1e-16, member)
        np.testing.assert_allclose(tiny, usual, rtol=1e-6, atol=1e-7)

    def test_pit_query_date_must_be_strictly_previous(self):
        row = {'date': ['2020-05-11'], 'financial_available_date': ['2020-05-11'],
               'report_version_available_date': ['2020-05-11'], 'market_available_date': ['2020-05-11'],
               'event_query_date': ['2020-05-08'], 'report_query_date': ['2020-05-08'],
               'market_query_date': ['2020-05-08'], 'daily_source_date': ['2020-05-08'],
               'report_period': ['2020-03-31']}
        validate_slow_dates(pd.DataFrame(row))
        row['market_query_date'] = ['2020-05-11']
        with self.assertRaises(ValueError):
            validate_slow_dates(pd.DataFrame(row))

    def test_independent_price_tape_label_reconstruction(self):
        price = np.arange(10, 18, dtype=np.float32)[:, None]
        y = np.full((8, 1, 4), np.nan, np.float32)
        for delta in range(1, 5):
            y[:-delta, 0, delta - 1] = np.log(price[delta:, 0] / price[:-delta, 0])
        minute = 60 * 10**9
        times = np.arange(8, dtype=np.int64) * 100 * minute
        entry = times + 10 * minute
        ends = np.full((8, 4), -1, np.int64)
        for delta in range(1, 5):
            ends[:-delta, delta - 1] = entry[delta:] + 5 * minute
        check = verify_labels(price, y, times, entry, ends, np.ones((8, 1), bool))
        self.assertEqual(check['mismatches'], 0)
        shifted = ends.copy()
        shifted[0, 3] -= minute
        with self.assertRaisesRegex(ValueError, 'five-minute execution windows'):
            verify_labels(price, y, times, entry, shifted, np.ones((8, 1), bool))
        y[0, 0, 3] += 0.01
        with self.assertRaises(ValueError):
            verify_labels(price, y, times, entry, ends, np.ones((8, 1), bool))


if __name__ == '__main__':
    unittest.main()
