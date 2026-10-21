# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import run_april_backtest as target


CALCULATED_FACTORS = {
    "A03_TAIL_VOLUME_SHARE_20D",
    "A12_SMALL_DOWN_JUMP_VOL_5M",
    "A13_CORRECTED_OVERNIGHT_REVERSAL_20D",
    "A15_NOON_ANCIENT_TREE",
    "A19_BURST_AMOUNT_CORR_20D",
    "A21_DOWNSIDE_VOL_SHARE_5M_20D",
    "A25_PV_CORR_STD_20D_NEUTRAL",
    "A30_PCA_IDIOSYNCRATIC_VOL_20D",
}


def _base_cache_frame(dates: pd.DatetimeIndex, code: str) -> pd.DataFrame:
    count = len(dates)
    close = 10.0 + np.arange(count, dtype=float) * 0.1
    return pd.DataFrame(
        {
            "date": dates,
            "code": [code] * count,
            "open": close - 0.02,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "amount": np.full(count, 10_000_000.0),
            "total_shares": np.full(count, 100_000_000.0),
            "qfq_ratio": np.ones(count),
            "prev_close": np.r_[np.nan, close[:-1]],
            "vwap_outside_range_1pct": np.zeros(count, dtype=bool),
            "overnight_return": np.linspace(-0.01, 0.01, count),
            "upside_vol_share_5m": np.full(count, 0.6),
        }
    )


def _april_cache_frame(dates: pd.DatetimeIndex, code: str) -> pd.DataFrame:
    count = len(dates)
    return pd.DataFrame(
        {
            "date": dates,
            "code": [code] * count,
            "tail_volume_share_daily": np.full(count, 0.2),
            "small_down_jump_vol_5m": np.full(count, 0.01),
            "noon_ancient_tree_abs_t": np.full(count, 1.5),
            "noon_ancient_tree_f_all": np.full(count, 3.0),
            "burst_amount_corr_20d": np.full(count, -0.2),
            "pv_corr_daily": np.full(count, 0.1),
        }
    )


def test_load_merged_panel_reads_two_caches_by_date_and_code(tmp_path: Path) -> None:
    dates = pd.bdate_range("2020-01-02", periods=2)
    base_path = tmp_path / "base.parquet"
    april_path = tmp_path / "april.parquet"
    _base_cache_frame(dates, "SZ000001").to_parquet(base_path, index=False)
    _april_cache_frame(dates, "sz000001").to_parquet(april_path, index=False)

    panel = target.load_merged_panel(
        [base_path], [april_path], "2020-01-01", "2020-12-31"
    )

    assert len(panel) == 2
    assert panel["code"].tolist() == ["sz000001", "sz000001"]
    assert panel.loc[0, "tail_volume_share_daily"] == pytest.approx(0.2)
    assert panel.loc[1, "tail_volume_share_daily"] == pytest.approx(0.2)
    assert panel.attrs["april_matched_rows"] == 2


def test_load_merged_panel_rejects_incomplete_april_cache(tmp_path: Path) -> None:
    dates = pd.bdate_range("2020-01-02", periods=2)
    base_path = tmp_path / "base.parquet"
    april_path = tmp_path / "april.parquet"
    _base_cache_frame(dates, "SZ000001").to_parquet(base_path, index=False)
    _april_cache_frame(dates[:1], "sz000001").to_parquet(april_path, index=False)

    with pytest.raises(ValueError, match="必须完整一一对应"):
        target.load_merged_panel(
            [base_path], [april_path], "2020-01-01", "2020-12-31"
        )


def test_load_panel_reports_missing_april_column(tmp_path: Path) -> None:
    path = tmp_path / "april.parquet"
    frame = _april_cache_frame(pd.bdate_range("2020-01-02", periods=1), "sz000001")
    frame.drop(columns="pv_corr_daily").to_parquet(path, index=False)

    with pytest.raises(ValueError, match="四月缓存缺少本轮所需列.*pv_corr_daily"):
        target.load_panel(
            [path],
            "2020-01-01",
            "2020-12-31",
            target.APRIL_COLUMNS,
            "四月缓存",
        )


def test_pca_window_matches_direct_svd_projection() -> None:
    rng = np.random.default_rng(123)
    values = rng.normal(0.0, 0.01, size=(20, 9))

    actual, component_count, first_ratio = (
        target.pca_residual_volatility_one_window(values)
    )

    centered = values - values.mean(axis=0, keepdims=True)
    u, singular, _ = np.linalg.svd(centered, full_matrices=False)
    expected_ratio = singular[0] ** 2 / np.sum(singular**2)
    expected_count = 2 if expected_ratio < 0.20 else 1
    residual = centered - u[:, :expected_count] @ (
        u[:, :expected_count].T @ centered
    )
    expected = np.std(residual, axis=0, ddof=0)

    assert component_count == expected_count
    assert first_ratio == pytest.approx(expected_ratio)
    np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-12)


def test_pca_uses_two_components_when_first_explains_less_than_twenty_percent() -> None:
    rng = np.random.default_rng(7)
    raw = rng.normal(size=(20, 6))
    raw -= raw.mean(axis=0, keepdims=True)
    basis, _ = np.linalg.qr(raw)

    _, component_count, first_ratio = target.pca_residual_volatility_one_window(
        basis[:, :6]
    )

    assert first_ratio < 0.20
    assert component_count == 2


def test_pca_needs_twenty_returns_from_twenty_one_closes() -> None:
    rng = np.random.default_rng(5)
    daily_returns = rng.normal(0.0005, 0.01, size=(20, 6))
    close = np.empty((21, 6), dtype=float)
    close[0] = 10.0
    for day in range(20):
        close[day + 1] = close[day] * (1.0 + daily_returns[day])
    membership = np.ones(close.shape, dtype=bool)

    result = target.compute_pca_idiosyncratic_volatility(
        close, membership, window=20, min_stocks=6
    )
    expected, _, _ = target.pca_residual_volatility_one_window(daily_returns)

    assert np.isnan(result[:20]).all()
    np.testing.assert_allclose(result[20], expected, rtol=1e-10, atol=1e-12)


def _synthetic_panel() -> tuple[pd.DataFrame, pd.DatetimeIndex, list[str], dict, dict]:
    dates = pd.bdate_range("2020-01-01", periods=32)
    stocks = [f"sz{index:06d}" for index in range(8)]
    close_history = np.empty((len(dates), len(stocks)), dtype=float)
    close_history[0] = 10.0 + np.arange(len(stocks))
    for day in range(1, len(dates)):
        stock_index = np.arange(len(stocks), dtype=float)
        daily_return = (
            0.0004 * (stock_index - 3.5)
            + 0.002 * np.sin(day * (stock_index + 1.0) * 0.31)
            + 0.0005 * np.cos(day * 0.23)
        )
        close_history[day] = close_history[day - 1] * (1.0 + daily_return)

    rows = []
    for day, date in enumerate(dates):
        for stock_index, stock in enumerate(stocks):
            close = close_history[day, stock_index]
            previous = close_history[max(day - 1, 0), stock_index]
            rows.append(
                {
                    "date": date,
                    "code": stock,
                    "open": close * 0.999,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "amount": 10_000_000.0,
                    "total_shares": 100_000_000.0 * (stock_index + 1.0),
                    "qfq_ratio": 1.0,
                    "prev_close": previous,
                    "vwap_outside_range_1pct": False,
                    "overnight_return": (
                        0.001 * (stock_index - 3.5)
                        + 0.0004
                        * (stock_index + 1.0)
                        * np.sin(day * 0.41)
                    ),
                    "upside_vol_share_5m": 0.35
                    + 0.01 * stock_index
                    + 0.001 * day,
                    "tail_volume_share_daily": 0.10
                    + 0.002 * day
                    + 0.001 * stock_index,
                    "small_down_jump_vol_5m": 0.01
                    + 0.0001 * day
                    + 0.0002 * stock_index,
                    "noon_ancient_tree_abs_t": 1.0 + 0.1 * stock_index,
                    "noon_ancient_tree_f_all": float(stock_index),
                    "burst_amount_corr_20d": -0.4 + 0.05 * stock_index,
                    "pv_corr_daily": np.sin(day * (stock_index + 1.0) * 0.17),
                }
            )
    panel = pd.DataFrame(rows)
    date_map = {date: index for index, date in enumerate(dates)}
    stock_map = {stock: index for index, stock in enumerate(stocks)}
    return panel, dates, stocks, date_map, stock_map


def test_build_factor_matrices_calculates_exactly_the_eight_selected_factors() -> None:
    panel, dates, stocks, date_map, stock_map = _synthetic_panel()
    membership = np.ones((len(dates), len(stocks)), dtype=bool)

    factors, arrays = target.build_factor_matrices(
        panel,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        min_stocks=4,
    )

    assert set(factors) == CALCULATED_FACTORS
    assert len(target.FACTOR_META) == 16
    status = target.build_status_frame(factors)
    assert status["calculated"].sum() == 8

    tail = panel.pivot(index="date", columns="code", values="tail_volume_share_daily").to_numpy()
    expected_tail = tail[-20:, 0].mean()
    assert factors["A03_TAIL_VOLUME_SHARE_20D"][-1, 0] == pytest.approx(
        expected_tail
    )
    assert factors["A12_SMALL_DOWN_JUMP_VOL_5M"][-1, 0] == pytest.approx(
        panel.iloc[-8]["small_down_jump_vol_5m"]
    )

    assert np.all(factors["A15_NOON_ANCIENT_TREE"][-1, :4] < 0)
    assert np.all(factors["A15_NOON_ANCIENT_TREE"][-1, 4:] > 0)
    np.testing.assert_allclose(
        factors["A19_BURST_AMOUNT_CORR_20D"][-1],
        np.array([-0.4 + 0.05 * index for index in range(8)]),
        rtol=1e-6,
    )

    upside = panel.pivot(index="date", columns="code", values="upside_vol_share_5m").to_numpy()
    expected_downside = (1.0 - upside[-20:, 0]).mean()
    assert factors["A21_DOWNSIDE_VOL_SHARE_5M_20D"][-1, 0] == pytest.approx(
        expected_downside
    )

    pv_residual = factors["A25_PV_CORR_STD_20D_NEUTRAL"][-1].astype(float)
    log_market_cap = np.log(
        arrays["raw_close"][-1].astype(float)
        * arrays["total_shares"][-1].astype(float)
    )
    assert np.isfinite(pv_residual).all()
    assert pv_residual.mean() == pytest.approx(0.0, abs=2e-7)
    assert np.dot(pv_residual, log_market_cap) == pytest.approx(0.0, abs=2e-5)

    pca = factors["A30_PCA_IDIOSYNCRATIC_VOL_20D"][-1]
    assert np.isfinite(pca).all()
    assert np.all(pca >= 0)


def test_burst_amount_corr_requires_twenty_complete_market_days() -> None:
    panel, dates, stocks, date_map, stock_map = _synthetic_panel()
    membership = np.ones((len(dates), len(stocks)), dtype=bool)
    missing = (panel["date"] == dates[-5]) & (panel["code"] == stocks[0])
    panel.loc[missing, "tail_volume_share_daily"] = np.nan

    factors, _ = target.build_factor_matrices(
        panel,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        min_stocks=4,
    )

    assert np.isnan(factors["A19_BURST_AMOUNT_CORR_20D"][-1, 0])
    assert np.isfinite(factors["A19_BURST_AMOUNT_CORR_20D"][-1, 1])


def test_corrected_overnight_uses_distance_then_twenty_day_mean_and_std() -> None:
    panel, dates, stocks, date_map, stock_map = _synthetic_panel()
    membership = np.ones((len(dates), len(stocks)), dtype=bool)
    factors, _ = target.build_factor_matrices(
        panel,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        min_stocks=4,
    )

    overnight = panel.pivot(index="date", columns="code", values="overnight_return").to_numpy()
    distance = np.abs(overnight - overnight.mean(axis=1, keepdims=True))
    expected_mean, expected_std = target.january.rolling_mean_std(distance, 20)
    classifier_mean = expected_std[-1].mean()
    expected = expected_mean[-1].copy()
    expected[expected_std[-1] < classifier_mean] *= -1.0

    np.testing.assert_allclose(
        factors["A13_CORRECTED_OVERNIGHT_REVERSAL_20D"][-1],
        expected,
        rtol=1e-6,
        atol=1e-8,
    )


def test_selected_factor_frequencies_are_fixed() -> None:
    monthly = {
        "A03_TAIL_VOLUME_SHARE_20D",
        "A13_CORRECTED_OVERNIGHT_REVERSAL_20D",
        "A21_DOWNSIDE_VOL_SHARE_5M_20D",
        "A25_PV_CORR_STD_20D_NEUTRAL",
    }
    daily = CALCULATED_FACTORS - monthly
    unavailable = set(target.FACTOR_META) - CALCULATED_FACTORS

    assert {
        code
        for code, meta in target.FACTOR_META.items()
        if meta["signal_frequency"] == "monthly"
    } == monthly
    assert {
        code
        for code, meta in target.FACTOR_META.items()
        if meta["signal_frequency"] == "daily"
    } == daily
    assert {
        code
        for code, meta in target.FACTOR_META.items()
        if meta["signal_frequency"] == "unavailable"
    } == unavailable
