# -*- coding: utf-8 -*-

from argparse import Namespace
from pathlib import Path

import numpy as np
import pandas as pd

import run_september_december_backtest as target


def test_metadata_separates_strict_and_trial_formulas() -> None:
    modes = {meta["formula_mode"] for meta in target.FACTOR_META.values()}
    assert modes == {"strict", "trial"}
    assert all(
        meta["strict_status"] == f"{meta['formula_mode']}_formula"
        for meta in target.FACTOR_META.values()
    )
    conflict = target.FACTOR_META["J12_D16_INTRADAY_OR_OVERNIGHT_RETURN"]
    assert conflict["formula_mode"] == "trial"
    assert "定义冲突" in conflict["note"]
    counts = pd.Series(
        [meta["formula_mode"] for meta in target.FACTOR_META.values()]
    ).value_counts()
    assert len(target.FACTOR_META) == 45
    assert counts.to_dict() == {"strict": 27, "trial": 18}
    top_one = target.FACTOR_META["J11_D07_TOP1PCT_MINUTE_RETURN_PRODUCT"]
    assert top_one["signal_frequency"] == "daily"
    assert top_one["formula_mode"] == "strict"
    adjusted = target.FACTOR_META["J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR"]
    assert adjusted["formula_mode"] == "trial"
    assert "10个有效交易日" in adjusted["note"]


def test_load_panel_requests_only_two_daily_sources(monkeypatch) -> None:
    captured = {}

    class Connection:
        def execute(self, _sql, _parameters):
            return self

        def fetchone(self):
            return (1,)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(target.factor_store, "connect", lambda _settings: Connection())

    def fake_read(_connection, requested, start, end):
        captured["requested"] = requested
        captured["range"] = (start, end)
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2020-01-02"]),
                "code": ["sh600000"],
                "open": [10.0],
            }
        )

    monkeypatch.setattr(target.factor_store, "read_joined_panel", fake_read)
    args = Namespace(
        database="unused.duckdb",
        duckdb_temp_dir="tmp",
        duckdb_threads=2,
        duckdb_memory_limit="1GB",
        start="2020-01-01",
        end="2020-01-31",
    )
    panel, _elapsed = target.load_panel_with_duckdb(args)
    assert set(captured["requested"]) == {"base_daily", "minute_extra_daily"}
    assert captured["requested"]["base_daily"] == target.BASE_COLUMNS
    assert captured["requested"]["minute_extra_daily"] == target.MINUTE_EXTRA_COLUMNS
    assert captured["range"] == ("2020-01-01", "2020-01-31")
    assert panel["open"].dtype == np.float32


def test_abnormal_monthly_frequency_uses_previous_complete_months() -> None:
    dates = pd.date_range("2020-01-01", periods=14, freq="MS")
    condition = np.array([[True]] * 12 + [[False], [True]])
    valid = np.ones_like(condition, dtype=bool)
    result = target._abnormal_monthly_frequency(
        condition, valid, dates, history_months=12
    )
    assert np.isnan(result[:12]).all()
    np.testing.assert_allclose(result[12, 0], 0.0)
    np.testing.assert_allclose(result[13, 0], 12.0 / 11.0)


def test_weighted_frequency_uses_recent_observations_more() -> None:
    valid = np.ones((4, 1), dtype=bool)
    recent = target._weighted_frequency(
        np.array([[False], [False], [False], [True]]), valid, window=4, half_life=2
    )
    old = target._weighted_frequency(
        np.array([[True], [False], [False], [False]]), valid, window=4, half_life=2
    )
    assert recent[-1, 0] > old[-1, 0]


def test_weighted_frequency_counts_valid_stock_days_not_market_gaps() -> None:
    event = np.array([[True], [False], [False], [True], [False]])
    valid = np.array([[True], [True], [False], [True], [True]])
    result = target._weighted_frequency(event, valid, window=4, half_life=2)
    assert np.isnan(result[3, 0])
    expected = np.dot([1.0, 0.0, 1.0, 0.0], 0.5 ** (np.arange(3, -1, -1) / 2)) / 4
    np.testing.assert_allclose(result[4, 0], expected)


def test_neighbor_spillover_is_cross_sectional_regression_residual() -> None:
    values = np.array([[1.0, 2.0, 4.0, 8.0, 16.0]], dtype=np.float32)
    membership = np.ones_like(values, dtype=bool)
    result = target.compute_neighbor_spillover(
        values, membership, neighbor_count=2, min_stocks=3
    )
    assert np.isfinite(result).all()
    np.testing.assert_allclose(np.mean(result[0]), 0.0, atol=1e-6)
    np.testing.assert_allclose(np.sum(result[0] * values[0]), 0.0, atol=1e-5)


def test_neighbor_means_ignore_future_unlisted_code_slots() -> None:
    values = np.array([[1.0, np.nan, 3.0, 5.0]], dtype=np.float32)
    result = target._neighbor_means(values, neighbor_count=2)
    # 第二列代表未来才上市的代码；早期邻居应从当日已有股票中选择。
    np.testing.assert_allclose(result[0, 0], 3.0)
    np.testing.assert_allclose(result[0, 2], 3.0)


def test_pooled_monthly_cv_matches_raw_minutes() -> None:
    dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-02-03"])
    raw_days = [np.array([1.0, 3.0]), np.array([5.0, 7.0]), np.array([2.0, 6.0])]
    total = np.array([[day.sum()] for day in raw_days])
    count = np.full((3, 1), 2.0)
    cv = np.array([[day.std(ddof=1) / day.mean()] for day in raw_days])
    result = target._pooled_monthly_cv(total, count, cv, dates)
    np.testing.assert_allclose(
        result[1, 0], np.concatenate(raw_days[:2]).std(ddof=1) / 4.0
    )
    np.testing.assert_allclose(result[2, 0], raw_days[2].std(ddof=1) / 4.0)


def test_linear_weighted_monthly_volume_uses_weights_one_to_eleven() -> None:
    dates = pd.date_range("2020-01-01", periods=11, freq="MS")
    volume = np.arange(1.0, 12.0)[:, None]
    result = target._linear_weighted_monthly_volume(volume, dates, months=11)
    expected = np.average(np.arange(1.0, 12.0), weights=np.arange(1.0, 12.0))
    assert np.isnan(result[:10]).all()
    np.testing.assert_allclose(result[10, 0], expected)


def test_rolling_product_return_compounds_values() -> None:
    values = np.array([[0.10], [0.20], [-0.05]])
    result = target._rolling_product_return(values, 3)
    np.testing.assert_allclose(result[-1, 0], 1.10 * 1.20 * 0.95 - 1.0)


def test_valid_day_rolling_skips_suspension_rows() -> None:
    values = np.array([[1.0], [2.0], [np.nan], [3.0], [4.0]])
    rolling_sum = target._rolling_valid_sum(values, 3)
    rolling_mean, rolling_std = target._rolling_valid_mean_std(values, 3)
    assert np.isnan(rolling_sum[2, 0])
    np.testing.assert_allclose(rolling_sum[[3, 4], 0], [6.0, 9.0])
    np.testing.assert_allclose(rolling_mean[[3, 4], 0], [2.0, 3.0])
    np.testing.assert_allclose(
        rolling_std[[3, 4], 0],
        [np.std([1.0, 2.0, 3.0]), np.std([2.0, 3.0, 4.0])],
    )


def test_peak_gap_kurtosis_matches_pooled_raw_gaps() -> None:
    gaps = [np.array([1.0, 2.0]), np.array([3.0]), np.array([4.0, 5.0])]
    arrays = []
    for power in range(5):
        if power == 0:
            arrays.append(np.array([[len(day)] for day in gaps], dtype=float))
        else:
            arrays.append(
                np.array([[np.sum(day**power)] for day in gaps], dtype=float)
            )
    result = target.compute_peak_gap_kurtosis(*arrays, window=3)
    pooled = np.concatenate(gaps)
    expected = np.mean((pooled - pooled.mean()) ** 4) / np.var(pooled) ** 2
    np.testing.assert_allclose(result[-1, 0], expected)


def test_capital_gain_overhang_uses_reference_cost_denominator() -> None:
    close = np.array([[10.0], [20.0], [40.0]])
    turnover = np.array([[0.5], [0.25], [0.9]])
    result = target.compute_capital_gain_overhang(close, turnover, window=2)
    assert np.isnan(result[:2]).all()
    # 第三日只使用前两日：权重分别为0.25和0.5*(1-0.25)。
    reference = (20.0 * 0.25 + 10.0 * 0.5 * 0.75) / (0.25 + 0.5 * 0.75)
    np.testing.assert_allclose(result[2, 0], (40.0 - reference) / reference)


def test_capital_gain_overhang_four_day_example_excludes_current_price() -> None:
    close = np.array([[10.0], [20.0], [40.0], [80.0]])
    turnover = np.array([[0.5], [0.25], [0.1], [0.99]])
    result = target.compute_capital_gain_overhang(close, turnover, window=3)
    assert np.isnan(result[:3]).all()
    # 第四日三阶权重：0.1、0.25*(1-0.1)、0.5*(1-0.1)*(1-0.25)。
    weights = np.array([0.1, 0.25 * 0.9, 0.5 * 0.9 * 0.75])
    reference = np.dot([40.0, 20.0, 10.0], weights) / weights.sum()
    np.testing.assert_allclose(result[3, 0], (80.0 - reference) / reference)


def test_daily_turnover_uses_volume_divided_by_float_shares_for_cgo() -> None:
    volume = np.array([[268_800.0], [537_600.0], [806_400.0]])
    float_shares = np.full((3, 1), 100_000_000.0)
    turnover = target.compute_daily_turnover(volume, float_shares)
    np.testing.assert_allclose(turnover[:, 0], [0.002688, 0.005376, 0.008064])

    close = np.array([[10.0], [20.0], [40.0]])
    result = target.compute_capital_gain_overhang(close, turnover, window=2)
    reference = (
        20.0 * 0.005376 + 10.0 * 0.002688 * (1.0 - 0.005376)
    ) / (0.005376 + 0.002688 * (1.0 - 0.005376))
    np.testing.assert_allclose(result[2, 0], (40.0 - reference) / reference)


def test_returns_and_previous_close_skip_suspension_gap() -> None:
    close = np.array([[10.0], [np.nan], [11.0], [12.1]], dtype=np.float32)
    previous = target.previous_valid_values(close)
    returns = target.returns_from_previous_valid(close)
    np.testing.assert_allclose(previous[[2, 3], 0], [10.0, 11.0])
    assert np.isnan(returns[1, 0])
    np.testing.assert_allclose(returns[[2, 3], 0], [0.10, 0.10], rtol=1e-6)


def test_status_frame_reports_finite_counts_and_formula_mode() -> None:
    example = {
        code: np.array([[1.0], [np.nan]], dtype=np.float32)
        for code in target.FACTOR_META
    }
    status = target.build_status_frame(example)
    assert len(status) == len(target.FACTOR_META)
    assert set(status["formula_mode"]) == {"strict", "trial"}
    assert (status["finite_values"] == 1).all()


def test_build_factor_matrices_on_small_synthetic_panel() -> None:
    dates = pd.bdate_range("2018-01-02", periods=280)
    stocks = [f"sh60000{index}" for index in range(6)]
    rows = []
    for day, date in enumerate(dates):
        for stock_index, code in enumerate(stocks):
            base_price = 10.0 + stock_index + day * (0.005 + stock_index * 0.0002)
            day_return = 0.002 * np.sin(day / 7.0 + stock_index)
            open_price = base_price * (1.0 - day_return / 2.0)
            close_price = base_price * (1.0 + day_return / 2.0)
            amount = 10_000_000.0 + day * 1_000.0 + stock_index * 100_000.0
            row = {
                "date": date,
                "code": code,
                "open": open_price,
                "high": max(open_price, close_price) * 1.02,
                "low": min(open_price, close_price) * 0.98,
                "close": close_price,
                "volume": 1_000_000.0 + day * 100.0 + stock_index * 10_000.0,
                "amount": amount,
                "float_shares": 100_000_000.0,
                "qfq_ratio": 1.0,
                "vwap_outside_range_1pct": False,
                "overnight_return": 0.003 if (day + stock_index) % 2 else -0.003,
                # 故意写成错误的大值，确认脚本不会再读取这一列当日换手率。
                "turnvol_daily": 0.9,
                "peak_count": float((day + stock_index) % 4),
                "minute_count": 240.0,
                "absret_lag_amount_corr": 0.01 * np.sin(day / 5 + stock_index),
                "lag_absret_amount_corr": 0.01 * np.cos(day / 5 + stock_index),
                "morning_afternoon_volume_ratio": 0.8 + stock_index * 0.02,
                "amplitude_split_daily": day_return + stock_index * 0.0001,
                "top1pct_minute_return": 0.001 + stock_index * 0.0001,
                "normal_active_share": 0.45 + stock_index * 0.01,
                "uniform_active_share": 0.44 + stock_index * 0.01,
                "amount_cv_1m": 0.5 + stock_index * 0.01,
                "volume_std_1m": 100.0 + day * 0.1 + stock_index,
                "dazzling_return_base": day_return + stock_index * 0.0002,
                "time_avg_relative_price": 0.4 + stock_index * 0.02,
                "intraday_trend_ratio_5m": day_return * 10 + stock_index * 0.001,
                "realized_bipower_var_5m": 0.001 + stock_index * 0.0001,
                "realized_tripower_var_5m": 0.0009 + stock_index * 0.0001,
                "intraday_max_drawdown_5m": -0.03 + stock_index * 0.001,
                "realized_jump_volatility_5m": 0.0002 + stock_index * 0.00001,
                "up_jump_volatility_5m": 0.0001 + stock_index * 0.00001,
                "volume_share_skew_10m": -0.2 + stock_index * 0.05,
                "volume_share_std_60m": 0.05 + stock_index * 0.005,
                "smart_money_vwap_ratio_10d": 0.99 + stock_index * 0.001,
                "volume_peak_return_daily": day_return * 0.2,
                "valley_relative_vwap_daily": 1.0 + stock_index * 0.0002,
                "current_day_volume_peak_count": float(5 + (day + stock_index) % 4),
                "volume_tide_price_rate_daily": day_return * 0.01,
                "ridge_relative_vwap_daily": 1.0 - stock_index * 0.0002,
                "ridge_minute_count_daily": float(10 + (day + stock_index) % 5),
                "adj_lag_absret_amount_corr_daily": 0.01 * np.sin(day / 6 + stock_index),
                "fuzzy_amount_corr_daily": 0.01 * np.cos(day / 6 + stock_index),
                "peak_gap_count": 4.0,
                "peak_gap_sum1": 10.0 + stock_index,
                "peak_gap_sum2": 30.0 + stock_index,
                "peak_gap_sum3": 100.0 + stock_index,
                "peak_gap_sum4": 354.0 + stock_index,
                "valley_price_percentile_daily": 0.5 + stock_index * 0.01,
            }
            rows.append(row)
    panel = pd.DataFrame(rows)
    stocks_out, date_map, stock_map = target.january.make_axes(panel, dates)
    membership = np.ones((len(dates), len(stocks_out)), dtype=bool)
    settings = target.january.Settings(
        cache_dir=Path("."),
        output_dir=Path("."),
        qlib_dir=Path("."),
        min_stocks=3,
    )
    factors, arrays = target.build_factor_matrices(
        panel, dates, stocks_out, date_map, stock_map, membership, settings
    )
    assert set(factors) == set(target.FACTOR_META)
    assert all(values.shape == membership.shape for values in factors.values())
    assert arrays["open"].shape == membership.shape
    assert np.isfinite(factors["J09_D10_INTRADAY_TREND_RATIO"]).all()
    assert np.isfinite(factors["J12_D14_UP_JUMP_VOL"]).all()
    assert np.isfinite(factors["J09_D14_SMART_MONEY_VWAP_RATIO"]).all()
    assert np.isfinite(factors["J12_D13_FUZZY_AMOUNT_CORR"][19:]).all()
    np.testing.assert_allclose(
        factors["J11_D07_TOP1PCT_MINUTE_RETURN_PRODUCT"][:, 0],
        panel.loc[panel["code"] == stocks_out[0], "top1pct_minute_return"],
    )
