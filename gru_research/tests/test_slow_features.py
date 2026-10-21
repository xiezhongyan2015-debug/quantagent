import sys
import unittest
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import slow_features as sf


class SlowFeatureTimingTest(unittest.TestCase):
    def setUp(self):
        self.calendar = pd.bdate_range("2018-01-01", "2020-01-10")
        self.financial = pd.DataFrame([
            {"asof_date": "2018-05-01", "thscode": "000001.SZ", "report_period": "2018-03-31", "parent_net_profit": 10., "revenue": 100., "total_assets": 1000., "parent_equity": 500.},
            {"asof_date": "2019-04-01", "thscode": "000001.SZ", "report_period": "2018-12-31", "parent_net_profit": 40., "revenue": 400., "total_assets": 1100., "parent_equity": 550.},
            {"asof_date": "2019-05-03", "thscode": "000001.SZ", "report_period": "2019-03-31", "parent_net_profit": 13., "revenue": 120., "total_assets": 1200., "parent_equity": 600.},
        ])
        self.market = pd.DataFrame([{"asof_date": "2019-04-26", "thscode": "000001.SZ", "total_market_cap_cny": 2000., "sw_l1_provider_code": "S43"}])
        self.members = pd.DataFrame({"date": ["2019-05-02", "2019-05-03", "2019-05-06", "2019-05-07"], "symbol": ["000001.SZ"] * 4})

    def test_query_version_cannot_enter_on_same_day_and_ttm_uses_visible_operands(self):
        result = sf.pit_join(self.members, self.financial, self.market, self.calendar)
        self.assertEqual(result.ttm_parent_profit.tolist(), [40., 40., 43., 43.])
        self.assertAlmostEqual(result.f_roe_ttm.iloc[2], 43. / 550.)
        self.assertAlmostEqual(result.f_roa_ttm.iloc[2], 43. / 1100.)
        self.assertAlmostEqual(result.f_parent_profit_yoy.iloc[2], .3)
        self.assertEqual(result.age_version_days.tolist(), [30., 31., 0., 1.])
        self.assertTrue((result.financial_available_date <= result.date).all())
        self.assertTrue((result.industry_code == 0).all())

    def test_future_report_mutation_does_not_change_prior_inputs(self):
        original = sf.pit_join(self.members, self.financial, self.market, self.calendar)
        changed = self.financial.copy()
        changed.loc[changed.asof_date == "2019-05-03", "parent_net_profit"] = 99999.
        altered = sf.pit_join(self.members, changed, self.market, self.calendar)
        pd.testing.assert_frame_equal(original.iloc[:2], altered.iloc[:2])
        self.assertNotEqual(original.ttm_parent_profit.iloc[2], altered.ttm_parent_profit.iloc[2])

    def test_missing_ttm_endpoint_is_not_zero_or_backfilled(self):
        financial = self.financial[self.financial.report_period != "2018-03-31"]
        result = sf.pit_join(self.members, financial, self.market, self.calendar)
        self.assertTrue(np.isnan(result.ttm_parent_profit.iloc[2]))
        self.assertTrue(np.isnan(result.f_parent_profit_yoy.iloc[2]))
        self.assertTrue(np.isnan(result.f_roe_ttm.iloc[2]))

    def test_revision_to_old_report_is_visible_only_after_its_own_query(self):
        revised = pd.concat([self.financial, pd.DataFrame([{"asof_date": "2019-05-07", "thscode": "000001.SZ", "report_period": "2018-12-31", "parent_net_profit": 100., "revenue": 400., "total_assets": 1100., "parent_equity": 550.}])], ignore_index=True)
        target = pd.DataFrame({"date": ["2019-05-07", "2019-05-08"], "symbol": ["000001.SZ"] * 2})
        result = sf.pit_join(target, revised, self.market, self.calendar)
        self.assertEqual(result.ttm_parent_profit.tolist(), [43., 103.])
        self.assertEqual(result.report_period.dt.strftime("%Y-%m-%d").tolist(), ["2019-03-31", "2019-03-31"])

    def test_market_query_stays_missing_until_next_session(self):
        target = pd.DataFrame({"date": ["2019-04-26", "2019-04-29"], "symbol": ["000001.SZ"] * 2})
        result = sf.pit_join(target, self.financial, self.market, self.calendar)
        self.assertTrue(np.isnan(result.f_earnings_price.iloc[0]))
        self.assertEqual(result.f_earnings_price.iloc[1], .02)

    def test_daily_shift_is_previous_calendar_session_without_gap_fill(self):
        index = pd.bdate_range("2020-01-01", periods=8)
        close = pd.DataFrame({"A": [10., 11., np.nan, 13., 14., 15., 16., 17.]}, index=index)
        fields = {"close": close, "open": close, "volume": close * 100., "amount": close * 1000., "turnover": close * .01}
        result = sf.daily_state(fields)
        self.assertAlmostEqual(result["d_ret1"].iloc[2, 0], np.log(11 / 10))
        self.assertTrue(np.isnan(result["d_ret1"].iloc[3, 0]))
        self.assertTrue(np.isnan(result["d_ret1"].iloc[4, 0]))
        changed = {k: v.copy() for k, v in fields.items()}
        changed["close"].iloc[-1] = 5000.
        altered = sf.daily_state(changed)
        for name in result:
            pd.testing.assert_frame_equal(result[name], altered[name])

    def test_legacy_formula_rebase_gate_rejects_absolute_price(self):
        compiler = sf.load_legacy_compiler()
        self.assertTrue(sf.price_scale_invariant(compiler, "close / lag(close, 20) - 1"))
        self.assertFalse(sf.price_scale_invariant(compiler, "cs_rank(close)"))

    def test_correlation_is_mean_of_cross_sections_not_global(self):
        a = np.r_[np.arange(100.), np.arange(100.) + 1000]
        b = np.r_[np.arange(100.), -np.arange(100.) + 2000]
        correlation = sf.mean_cross_section_corr(a, b, [np.arange(100), np.arange(100, 200)])
        self.assertAlmostEqual(correlation, 0.)
        self.assertGreater(abs(np.corrcoef(a, b)[0, 1]), .9)

    def test_numeric_utc_ns_training_projection_and_buffer_endpoint(self):
        signals = pd.to_datetime(["2019-01-02 10:30", "2022-12-27 15:00", "2022-12-28 15:00", "2023-01-03 10:30"]).tz_localize("Asia/Shanghai")
        ends = pd.to_datetime(["2019-01-03 10:36", "2022-12-29 09:36", "2022-12-30 09:36", "2023-01-04 10:36"]).tz_localize("Asia/Shanghai")
        data = pd.DataFrame({"signal_time": signals.tz_convert("UTC").as_unit("ns").asi8, "label_end_time4": ends.tz_convert("UTC").as_unit("ns").asi8.astype(float), "symbol": ["000001.SZ"] * 4, "y4": [.1] * 4, "is_member": [True] * 4, "window80_complete": [True] * 4})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "labels.parquet"
            data.to_parquet(path, index=False)
            read = sf.read_training_labels(path)
        self.assertEqual(len(read), 3)
        self.assertEqual(read.signal_time.iloc[0], pd.Timestamp("2019-01-02 10:30"))
        purged = sf.purge_training_labels(read, pd.bdate_range("2019-01-01", "2022-12-30"))
        # Dec 29/30 are the two isolation sessions: Dec 27 end on Dec 29
        # is excluded even though its signal predates both isolation sessions.
        self.assertEqual(len(purged), 1)

    def test_training_projection_uses_membership_and_complete_history(self):
        signal = pd.Timestamp("2021-01-04 10:30", tz="Asia/Shanghai").tz_convert("UTC").value
        end = pd.Timestamp("2021-01-05 10:36", tz="Asia/Shanghai").tz_convert("UTC").value
        data = pd.DataFrame({"signal_time": [signal] * 3, "label_end_time4": [end] * 3, "symbol": ["A", "B", "C"], "y4": [.1] * 3, "is_member": [True, False, True], "window80_complete": [True, True, False]})
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "labels.parquet"
            data.to_parquet(path, index=False)
            read = sf.read_training_labels(path)
        self.assertEqual(read.symbol.tolist(), ["A"])

    def test_vector_rank_ic_matches_pairwise_pandas_rank_with_ties_and_missing(self):
        rng = np.random.default_rng(741)
        x = rng.integers(-5, 5, (3, 200)).astype(float)
        y = rng.normal(size=(3, 200))
        x[0, :12] = np.nan
        y[0, 170:] = np.nan
        x[1, 150:] = np.nan
        x[2, 90:] = np.nan
        result = sf.row_rank_ic(x, y)
        for row in [0, 1]:
            mask = np.isfinite(x[row]) & np.isfinite(y[row])
            expected = pd.Series(x[row, mask]).rank().corr(pd.Series(y[row, mask]).rank())
            self.assertAlmostEqual(result[row], expected)
        self.assertTrue(np.isnan(result[2]))

    def test_price_probe_preserves_valid_long_nested_history(self):
        compiler = sf.load_legacy_compiler()
        previous = "close"
        for _ in range(12):
            previous = f"lag({previous}, 120)"
        self.assertTrue(sf.price_scale_invariant(compiler, f"close / {previous} - 1"))

    def test_normalization_is_invariant_to_small_units_and_retains_missing(self):
        values = np.r_[np.arange(101.), np.repeat(5., 100), np.nan]
        codes = np.r_[np.zeros(101, dtype=int), np.ones(101, dtype=int)]
        within = np.tile(np.arange(101), 2)
        normalized = sf.normalize_training_factor(values, codes, within)
        small = sf.normalize_training_factor(values * 1e-16, codes, within)
        np.testing.assert_allclose(normalized, small, atol=1e-6, rtol=1e-6, equal_nan=True)
        self.assertTrue(np.isnan(normalized[-1]))
        self.assertTrue((normalized[101:-1] == 0).all())
        self.assertTrue((np.abs(normalized[:101]) <= 5).all())

    def test_price_probe_rejects_hidden_mixed_price_and_currency_formula(self):
        compiler = sf.load_legacy_compiler()
        self.assertFalse(sf.price_scale_invariant(compiler, "rolling_cov(abs(high)-(volume+amount),amount,10)"))


if __name__ == "__main__":
    unittest.main()
