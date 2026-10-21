# -*- coding: utf-8 -*-
"""一月分钟因子汇总程序的关键计算检查。"""

import sys

import numpy as np
import pandas as pd
import pytest

import run_january_minute_backtest as target


def test_panel_matrix_columns_reuse_coordinates_and_keep_exact_values():
    dates = pd.bdate_range("2025-01-02", periods=3)
    panel = pd.DataFrame(
        {
            "date": [dates[0], dates[0], dates[1], dates[2], dates[2] + pd.Timedelta(days=3)],
            "code": ["sh600000", "sz000001", "sh600000", "sz000001", "sh600000"],
            "first": [1.0, np.nan, 3.0, 4.0, 99.0],
            "second": [10.0, 20.0, np.nan, 40.0, 999.0],
        }
    )
    stocks, date_map, stock_map = target.make_axes(
        panel.loc[panel["date"].isin(dates)], dates
    )

    first = target.panel_to_matrix(
        panel, "first", dates, stocks, date_map, stock_map, dtype=np.float32
    )
    cached = panel.attrs["_panel_matrix_coordinates"]
    second = target.panel_to_matrix(
        panel, "second", dates, stocks, date_map, stock_map, dtype=np.float32
    )

    assert panel.attrs["_panel_matrix_coordinates"] is cached
    assert np.array_equal(
        first,
        np.array([[1.0, np.nan], [3.0, np.nan], [np.nan, 4.0]], dtype=np.float32),
        equal_nan=True,
    )
    assert np.array_equal(
        second,
        np.array([[10.0, 20.0], [np.nan, np.nan], [np.nan, 40.0]], dtype=np.float32),
        equal_nan=True,
    )


def test_panel_flag_matrix_reuses_coordinates_for_text_and_number_flags():
    dates = pd.bdate_range("2025-01-02", periods=2)
    panel = pd.DataFrame(
        {
            "date": [dates[0], dates[0], dates[1], dates[1]],
            "code": ["a", "b", "a", "b"],
            "text_flag": ["yes", "no", None, "bad"],
            "number_flag": [1.0, 0.0, np.nan, 2.0],
        }
    )
    stocks, date_map, stock_map = target.make_axes(panel, dates)

    text = target.panel_flag_to_matrix(
        panel, "text_flag", dates, stocks, date_map, stock_map
    )
    number = target.panel_flag_to_matrix(
        panel, "number_flag", dates, stocks, date_map, stock_map
    )

    expected = np.array([[True, False], [False, True]])
    assert np.array_equal(text, expected)
    assert np.array_equal(number, expected)


def test_select_factor_codes_keeps_requested_order_and_rejects_bad_lists():
    factors = {
        "A": np.array([[1.0]]),
        "B": np.array([[2.0]]),
        "C": np.array([[3.0]]),
    }

    assert target.select_factor_codes(factors, None) is factors
    selected = target.select_factor_codes(factors, ["C", "A"])
    assert list(selected) == ["C", "A"]
    assert selected["C"] is factors["C"]
    with pytest.raises(ValueError, match="重复"):
        target.select_factor_codes(factors, ["A", "A"])
    with pytest.raises(ValueError, match="没有这些因子"):
        target.select_factor_codes(factors, ["Z"])


def test_rolling_mean_does_not_use_future():
    values = np.arange(1.0, 7.0)[:, None]
    result = target.rolling_mean(values, 3)
    assert np.isnan(result[1, 0])
    assert result[2, 0] == 2.0
    changed = values.copy()
    changed[5, 0] = 1_000_000.0
    changed_result = target.rolling_mean(changed, 3)
    assert changed_result[4, 0] == result[4, 0]


def test_exponential_moving_average_matches_recursive_formula():
    values = np.arange(1.0, 8.0)[:, None]
    result = target.exponential_moving_average(values, 3)
    assert np.isnan(result[1, 0])
    # span=3 时 alpha=0.5：1→1.5→2.25。
    assert np.isclose(result[2, 0], 2.25)


def test_cgo_uses_more_recent_turnover_survival_weight():
    close = np.array([[10.0], [20.0], [30.0]])
    vwap = close.copy()
    turnover = np.array([[0.2], [0.3], [0.4]])
    result = target.compute_cgo(close, vwap, turnover, 3)
    weights = np.array([0.2 * 0.7 * 0.6, 0.3 * 0.6, 0.4])
    reference = np.dot(weights, [10.0, 20.0, 30.0]) / weights.sum()
    assert np.isclose(result[2, 0], (30.0 - reference) / 30.0)


def test_cgo_block_calculation_matches_direct_formula():
    rng = np.random.default_rng(20260720)
    close = rng.uniform(8.0, 30.0, size=(9, 3))
    vwap = close * rng.uniform(0.98, 1.02, size=close.shape)
    turnover = rng.uniform(0.01, 0.35, size=close.shape)
    vwap[4, 1] = np.nan
    window = 4
    result = target.compute_cgo(close, vwap, turnover, window)

    expected = np.full_like(close, np.nan)
    for stock in range(close.shape[1]):
        for end in range(window - 1, len(close)):
            prices = vwap[end - window + 1 : end + 1, stock]
            rates = turnover[end - window + 1 : end + 1, stock]
            if not np.isfinite(prices).all() or not np.isfinite(rates).all():
                continue
            weights = np.empty(window)
            survival = 1.0
            for offset in range(window - 1, -1, -1):
                weights[offset] = rates[offset] * survival
                survival *= 1.0 - rates[offset]
            reference = np.dot(weights, prices) / weights.sum()
            expected[end, stock] = (close[end, stock] - reference) / close[end, stock]

    assert np.allclose(result, expected, equal_nan=True)


def test_gap_skew_combines_twenty_days_of_raw_moments():
    gaps = np.array([1.0, 2.0, 4.0])
    count = np.zeros((20, 1))
    sum1 = np.zeros((20, 1))
    sum2 = np.zeros((20, 1))
    sum3 = np.zeros((20, 1))
    count[-1, 0] = len(gaps)
    sum1[-1, 0] = gaps.sum()
    sum2[-1, 0] = (gaps**2).sum()
    sum3[-1, 0] = (gaps**3).sum()
    result = target.compute_gap_skew(count, sum1, sum2, sum3, 20)
    mean = gaps.mean()
    expected = np.mean((gaps - mean) ** 3) / np.mean((gaps - mean) ** 2) ** 1.5
    assert np.isclose(result[-1, 0], expected)


def test_neutralization_removes_linear_size_relation():
    factor = np.array([[3.0, 5.0, 7.0, 9.0]])
    size = np.array([[1.0, 2.0, 3.0, 4.0]])
    member = np.ones_like(factor, dtype=bool)
    residual = target.neutralize_one_factor(factor, size, member, min_stocks=3)
    assert np.allclose(residual[0], 0.0, atol=1e-12)


def test_future_return_uses_next_open_to_sixth_open():
    prices = np.arange(10.0, 20.0)[:, None]
    result = target.future_open_returns(prices, hold_days=5)
    assert np.isclose(result[0, 0], prices[6, 0] / prices[1, 0] - 1.0)


def test_future_return_uses_last_close_mark_when_exit_open_is_missing():
    prices = np.arange(10.0, 20.0)[:, None]
    prices[6, 0] = np.nan
    close = np.arange(10.0, 20.0)[:, None]
    exit_mark, _ = target.build_exit_price_matrices(prices, close)
    result = target.future_open_returns(prices, hold_days=5, exit_mark_price=exit_mark)
    assert np.isclose(result[0, 0], close[5, 0] / prices[1, 0] - 1.0)


def test_confirmed_windows_are_command_line_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_january_minute_backtest.py"])
    args = target.parse_args()
    assert args.cgo_window == 100
    assert args.illiq_window == 5


def test_original_signal_dates_follow_each_report_frequency():
    dates = pd.bdate_range("2024-01-29", periods=10)
    daily = target.original_signal_mask(dates, "daily", "2024-02-01")
    weekly = target.original_signal_mask(dates, "weekly_tuesday", "2024-02-01")
    every_five = target.original_signal_mask(dates, "every_5_days", "2024-02-01")
    monthly = target.original_signal_mask(dates, "monthly", "2024-02-01")

    assert daily.all()
    assert dates[weekly].weekday.tolist() == [1, 1]
    assert np.flatnonzero(every_five).tolist() == [3, 8]
    assert dates[monthly].tolist() == [pd.Timestamp("2024-01-31"), dates[-1]]


def test_monthly_period_keeps_december_signal_for_january_return():
    dates = pd.bdate_range("2015-12-28", "2016-02-29")
    dates = dates[dates != pd.Timestamp("2016-01-01")]
    pairs = target.month_pairs(dates, "2016-01-04")
    signal, entry, exit_index = pairs[0]
    assert dates[signal] == pd.Timestamp("2015-12-31")
    assert dates[entry] == pd.Timestamp("2016-01-04")
    assert dates[exit_index] == pd.Timestamp("2016-01-29")


def test_renamed_stock_accepts_both_qlib_codes():
    dates = pd.bdate_range("2025-06-26", periods=4)
    rows = pd.DataFrame(
        {
            "instrument": ["SZ300114", "SZ302132"],
            "start": [dates[0], dates[2]],
            "end": [dates[1], dates[3]],
        }
    )
    membership, missing = target.build_membership(dates, ["sz302132"], rows)
    assert membership[:, 0].all()
    assert missing.empty


def test_price_amount_bad_column_names_are_compatible():
    dates = pd.DatetimeIndex(["2024-01-02"])
    stocks = ["sz000001"]
    date_map = {dates[0]: 0}
    stock_map = {stocks[0]: 0}
    for column in target.PRICE_AMOUNT_BAD_COLUMNS:
        panel = pd.DataFrame(
            {"date": dates, "code": stocks, column: [True]}
        )
        result = target.build_price_amount_bad_matrix(
            panel, dates, stocks, date_map, stock_map
        )
        assert result[0, 0]


def test_bad_daily_value_invalidates_the_full_observation_window():
    values = np.arange(1.0, 11.0)[:, None]
    bad_days = np.zeros_like(values, dtype=bool)
    bad_days[4, 0] = True
    result = target.rolling_mean(target.mask_bad_days(values, bad_days), 5)
    assert not np.isfinite(result[3, 0])
    assert np.isnan(result[4:9, 0]).all()
    assert np.isclose(result[9, 0], 8.0)


def test_factor_builder_adds_verified_variants_and_uses_ridge_columns():
    dates = pd.bdate_range("2023-01-02", periods=130)
    stocks = ["sz000001", "sz000002"]
    rows = []
    for day, date in enumerate(dates):
        for stock_index, stock in enumerate(stocks):
            close = 10.0 + day * 0.01 + stock_index
            rows.append(
                {
                    "date": date,
                    "code": stock,
                    "open": close - 0.01,
                    "high": close + 0.10,
                    "low": close - 0.10,
                    "close": close,
                    "amount": 10_000_000.0,
                    "volume": 1_000_000.0 + day,
                    "float_shares": 100_000_000.0,
                    "total_shares": 120_000_000.0 + stock_index * 10_000_000.0,
                    "qfq_ratio": 1.0,
                    "vwap_qfq": close - 0.02,
                    "turnvol_daily": day + 1.0 + stock_index,
                    "rvjn_5m": day + 1.0,
                    "illiq_shortcut_5m_daily": day + 2.0,
                    "weighted_close_ratio_5m": 0.1 + stock_index,
                    "volume_share_kurt_10m": 3.0 + stock_index,
                    "realized_skew_1m": day * 0.01,
                    "realized_skew_5m": day * 0.02,
                    "last_30min_return": day * 0.001,
                    "price_resiliency_1m_daily": day * 0.01 + stock_index * 0.1,
                    "volume_peak_price_position_daily": day * 0.01 + stock_index,
                    "gu_1m": day + 1.0 + stock_index,
                    "gd_1m": day * 0.5 + stock_index,
                    "ridge_gap_count": 3.0,
                    "ridge_gap_sum1": 7.0,
                    "ridge_gap_sum2": 21.0,
                    "ridge_gap_sum3": 73.0,
                    "vwap_outside_range_1pct": day == 120 and stock_index == 0,
                }
            )
    panel = pd.DataFrame(rows)
    date_map = {date: index for index, date in enumerate(dates)}
    stock_map = {stock: index for index, stock in enumerate(stocks)}
    membership = np.ones((len(dates), len(stocks)), dtype=bool)

    factors, _ = target.build_factor_matrices(
        panel,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        cgo_window=100,
        illiq_window=5,
        min_stocks=2,
    )

    assert len(factors) == 16
    assert "ILLIQ_SHORTCUT_5M_5D" in factors
    assert "REALIZED_SKEW_5M_20D" in factors
    assert "LAST_30MIN_RETURN_MA5" in factors
    assert "VOLUME_RIDGE_GAP_SKEW_20D" in factors
    assert "VOLUME_PEAK_GAP_SKEW_20D" not in factors
    assert np.isclose(factors["RVJN_5M"][4, 0], 3.0)
    assert np.isclose(factors["REALIZED_SKEW_5M_20D"][19, 0], 0.19)
    assert np.isclose(factors["LAST_30MIN_RETURN_MA5"][4, 0], 0.002)
    assert np.isnan(factors["CGO"][120, 0])
    assert np.isnan(factors["CGO"][129, 0])
    assert np.isnan(factors["ILLIQ_SHORTCUT_5M_5D"][125, 0])
    assert np.isfinite(factors["ILLIQ_SHORTCUT_5M_5D"][126, 0])


def test_monthly_original_backtest_uses_next_month_open_and_close(tmp_path):
    dates = pd.bdate_range("2024-01-29", "2024-03-29")
    stock_count = 20
    shape = (len(dates), stock_count)
    signal = np.tile(np.arange(stock_count, dtype=float), (len(dates), 1))
    open_price = np.full(shape, 10.0)
    close_price = np.full(shape, 10.0)
    february_end = dates.get_loc(pd.Timestamp("2024-02-29"))
    close_price[february_end] = np.arange(11.0, 31.0)
    open_exit, close_exit = target.build_exit_price_matrices(open_price, close_price)
    arrays = {
        "open": open_price,
        "close": close_price,
        "open_exit_mark": open_exit,
        "close_exit_mark": close_exit,
    }
    eligible = np.ones(shape, dtype=bool)
    settings = target.Settings(
        cache_dir=tmp_path,
        output_dir=tmp_path,
        qlib_dir=tmp_path,
        backtest_start="2024-01-01",
        min_stocks=10,
    )

    result = target.run_original_frequency_backtest(
        "CGO",
        signal,
        target.future_open_returns(open_price, settings.hold_days),
        eligible,
        arrays,
        dates,
        settings,
    )

    first = result.iloc[0]
    assert first["signal_date"] == pd.Timestamp("2024-01-31")
    assert first["entry_date"] == pd.Timestamp("2024-02-01")
    assert first["exit_date"] == pd.Timestamp("2024-02-29")
    assert np.isclose(first["low_raw_return"], 0.15)
    assert np.isclose(first["low_net_return"], 0.15 - settings.buy_cost - settings.sell_cost)


def test_original_frequency_summary_decides_direction_from_2016_2020():
    periods = 12
    signal_dates = pd.date_range("2019-01-31", periods=periods, freq="ME")
    rebalance = pd.DataFrame(
        {
            "factor": ["CGO"] * periods,
            "signal_frequency": ["monthly"] * periods,
            "signal_date": signal_dates,
            "entry_date": signal_dates + pd.Timedelta(days=1),
            "exit_date": signal_dates + pd.Timedelta(days=20),
            "low_net_return": np.linspace(-0.02, -0.01, periods),
            "low_turnover": np.full(periods, 0.4),
            "high_net_return": np.linspace(0.005, 0.015, periods),
            "high_turnover": np.full(periods, 0.3),
            "benchmark_return": np.linspace(0.0, 0.002, periods),
            "group_stocks": np.full(periods, 10),
        }
    )
    ic_summary = pd.DataFrame(
        [
            {
                "factor": "CGO",
                "evaluation_frequency": "unified_5d",
                "period": "2016_2020",
                "ic_mean": -0.8,
            },
            {
                "factor": "CGO",
                "evaluation_frequency": "monthly",
                "period": "full",
                "ic_mean": -0.6,
            },
            {
                "factor": "CGO",
                "evaluation_frequency": "monthly",
                "period": "2016_2020",
                "ic_mean": 0.2,
            },
        ]
    )

    summary, _ = target.summarize_original_frequency_backtest(
        rebalance, ic_summary, "2020-12-31"
    )

    assert rebalance["selected_direction"].eq("high").all()
    assert summary["direction"].eq("high").all()
    assert summary["in_sample_ic"].eq(0.2).all()
    assert summary["periods_per_year"].between(12.0, 13.0).all()


def test_original_backtest_keeps_stock_and_marks_missing_exit_at_last_close(tmp_path):
    dates = pd.bdate_range("2024-01-01", periods=20)
    stock_count = 20
    shape = (len(dates), stock_count)
    signal = np.full(shape, np.nan)
    signal[dates.weekday == 1] = np.arange(stock_count, dtype=float)
    open_price = np.full(shape, 10.0)
    first_tuesday = int(np.flatnonzero(dates.weekday == 1)[0])
    first_exit = first_tuesday + 6
    open_price[first_exit] = 11.0
    open_price[first_exit, 0] = np.nan
    close_price = open_price.copy()
    open_exit, close_exit = target.build_exit_price_matrices(open_price, close_price)
    arrays = {
        "open": open_price,
        "close": close_price,
        "open_exit_mark": open_exit,
        "close_exit_mark": close_exit,
    }
    eligible = np.ones(shape, dtype=bool)
    settings = target.Settings(
        cache_dir=tmp_path,
        output_dir=tmp_path,
        qlib_dir=tmp_path,
        backtest_start="2024-01-01",
        min_stocks=10,
    )

    result = target.run_original_frequency_backtest(
        "RVJN_5M",
        signal,
        target.future_open_returns(open_price, settings.hold_days),
        eligible,
        arrays,
        dates,
        settings,
    )

    first = result.iloc[0]
    assert first["eligible_stocks"] == 20
    assert first["group_stocks"] == 2
    assert first["exit_price_fallback_stocks"] == 1
    assert first["exit_price_missing_stocks"] == 0
    assert np.isclose(first["low_raw_return"], 0.05)


def test_split_uses_exit_for_training_and_entry_for_later_period():
    frame = pd.DataFrame(
        {
            "factor": ["X", "X", "X"],
            "date": pd.to_datetime(["2020-12-20", "2020-12-31", "2021-01-04"]),
            "entry_date": pd.to_datetime(["2020-12-21", "2021-01-04", "2021-01-05"]),
            "exit_date": pd.to_datetime(["2020-12-28", "2021-01-29", "2021-01-12"]),
            "ic": [0.1, 0.9, -0.2],
            "stock_count": [100, 100, 100],
        }
    )
    summary = target.summarize_ic(frame, "date", "daily", "2020-12-31")
    train = summary.loc[summary["period"] == "2016_2020"].iloc[0]
    later = summary.loc[summary["period"] == "2021_2025"].iloc[0]
    assert np.isclose(train["ic_mean"], 0.1)
    assert np.isclose(later["ic_mean"], 0.35)
    assert np.isclose(
        later["icir_annualized"],
        later["icir_raw"] * np.sqrt(later["periods_per_year"]),
    )


def test_max_drawdown_includes_first_period_loss():
    returns = pd.Series([-0.2] + [0.0] * 9)
    benchmark = pd.Series([0.0] * 10)
    metrics = target.return_metrics(returns, benchmark, hold_days=5)
    assert np.isclose(metrics["max_drawdown"], -0.2)


def test_weekly_gap_charges_exit_and_new_entry(tmp_path):
    dates = pd.bdate_range("2024-01-01", "2024-03-15")
    dates = dates[dates != pd.Timestamp("2024-01-09")]
    stock_count = 20
    shape = (len(dates), stock_count)
    signal = np.full(shape, np.nan)
    signal[dates.weekday == 1] = np.arange(stock_count, dtype=float)
    open_price = np.full(shape, 10.0)
    close_price = np.full(shape, 10.0)
    open_exit, close_exit = target.build_exit_price_matrices(open_price, close_price)
    arrays = {
        "open": open_price,
        "close": close_price,
        "open_exit_mark": open_exit,
        "close_exit_mark": close_exit,
    }
    settings = target.Settings(
        cache_dir=tmp_path,
        output_dir=tmp_path,
        qlib_dir=tmp_path,
        backtest_start="2024-01-01",
        min_stocks=10,
    )
    result = target.run_original_frequency_backtest(
        "RVJN_5M",
        signal,
        target.future_open_returns(open_price, settings.hold_days, open_exit),
        np.ones(shape, dtype=bool),
        arrays,
        dates,
        settings,
    )
    gaps = result["entry_date"].iloc[1:].reset_index(drop=True) != result[
        "exit_date"
    ].iloc[:-1].reset_index(drop=True)
    gap_index = int(np.flatnonzero(gaps.to_numpy())[0]) + 1
    assert result.iloc[gap_index - 1]["low_turnover"] >= 0.5
    assert result.iloc[gap_index]["low_turnover"] >= 0.5


def test_weekly_trade_periods_do_not_overlap_after_holidays():
    dates = pd.to_datetime(
        [
            "2024-01-01",
            "2024-01-02",
            "2024-01-03",
            "2024-01-08",
            "2024-01-09",
            "2024-01-10",
            "2024-01-11",
            "2024-01-12",
            "2024-01-15",
            "2024-01-16",
            "2024-01-17",
            "2024-01-18",
            "2024-01-19",
        ]
    )
    periods = target.original_backtest_periods(
        pd.DatetimeIndex(dates), "weekly_tuesday", "2024-01-01", 5
    )
    assert all(current[1] >= previous[2] for previous, current in zip(periods, periods[1:]))
