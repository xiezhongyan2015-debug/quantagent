# -*- coding: utf-8 -*-

import numpy as np
import pytest

import historical_additional_factors as target


def _sample(days: int = 90, stocks: int = 6) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(23)
    market = rng.normal(0.0003, 0.012, days)
    returns = np.column_stack(
        [(0.5 + index * 0.2) * market + rng.normal(0, 0.006, days) for index in range(stocks)]
    )
    close = 10.0 * np.cumprod(1.0 + returns, axis=0)
    opening = close * (1.0 + rng.normal(0, 0.002, close.shape))
    volume = 1_000_000.0 + rng.uniform(0, 100_000, close.shape)
    float_shares = np.tile(np.linspace(1e8, 6e8, stocks), (days, 1))
    return {
        "returns": returns,
        "market_return": market,
        "open": opening,
        "high": np.maximum(opening, close) * 1.01,
        "low": np.minimum(opening, close) * 0.99,
        "close": close,
        "volume": volume,
        "amount": volume * close,
        "float_shares": float_shares,
        "total_shares": float_shares * 1.5,
        "turnover": volume / float_shares,
        "pv_corr_daily": rng.uniform(-0.8, 0.8, close.shape),
        "a22_intraday_snr_trial": rng.uniform(0, 1, close.shape),
    }


def _slow_price_delay(arrays: dict[str, np.ndarray]) -> np.ndarray:
    returns = arrays["returns"]
    market = arrays["market_return"]
    groups, ends = target._month_groups(len(returns), None)
    monthly = np.full((len(groups), returns.shape[1]), np.nan)
    lags = np.column_stack([target._lag(market[:, None], lag)[:, 0] for lag in range(6)])
    for month, rows in enumerate(groups):
        x = lags[rows]
        for stock in range(returns.shape[1]):
            y = returns[rows, stock]
            valid = np.isfinite(y) & np.isfinite(x).all(axis=1)
            if valid.sum() < 10:
                continue
            full = np.column_stack([np.ones(valid.sum()), x[valid]])
            restricted = full[:, :2]
            current = y[valid]
            full_fit = full @ np.linalg.lstsq(full, current, rcond=None)[0]
            restricted_fit = restricted @ np.linalg.lstsq(restricted, current, rcond=None)[0]
            total = np.sum((current - current.mean()) ** 2)
            if total <= target.EPS:
                continue
            full_r2 = 1.0 - np.sum((current - full_fit) ** 2) / total
            restricted_r2 = 1.0 - np.sum((current - restricted_fit) ** 2) / total
            if full_r2 > target.EPS:
                monthly[month, stock] = 1.0 - restricted_r2 / full_r2
    return target._expand_monthly(monthly, ends, returns.shape)


def _slow_spread_bias(arrays: dict[str, np.ndarray]) -> np.ndarray:
    close = arrays["close"]
    returns = arrays["returns"]
    _, ends = target._month_groups(len(close), None)
    result = np.full(close.shape, np.nan)
    for end in ends:
        if end < 249:
            continue
        start = end - 249
        block = returns[start : end + 1]
        stocks = np.flatnonzero(np.isfinite(block).sum(axis=0) >= 200)
        correlation_input = block[:, stocks].copy()
        means = np.nanmean(correlation_input, axis=0)
        missing = ~np.isfinite(correlation_input)
        correlation_input[missing] = np.broadcast_to(means, correlation_input.shape)[missing]
        correlation = np.corrcoef(correlation_input, rowvar=False)
        normalized = close[start : end + 1, stocks] / close[start, stocks]
        history = np.full((60, len(stocks)), np.nan)
        for local in range(len(stocks)):
            order = np.argsort(
                np.where(np.isfinite(correlation[local]), correlation[local], -np.inf)
            )
            peers = order[order != local][-min(10, len(stocks) - 1) :]
            reference = np.nanmean(normalized[-60:, peers], axis=1)
            own = normalized[-60:, local]
            valid = (own > 0) & (reference > 0)
            history[valid, local] = np.log(own[valid]) - np.log(reference[valid])
        mean = np.nanmean(history, axis=0)
        standard = np.nanstd(history, axis=0, ddof=1)
        valid = np.isfinite(history[-1]) & (standard > target.EPS)
        result[end, stocks[valid]] = (history[-1, valid] - mean[valid]) / standard[valid]
    return result


def _slow_monthly_beta(arrays: dict[str, np.ndarray]) -> np.ndarray:
    stock, market, ends = target._monthly_returns(arrays, None, None)
    monthly = np.full(stock.shape, np.nan)
    for month in range(23, len(stock)):
        start = max(0, month - 59)
        x = market[start : month + 1]
        y = stock[start : month + 1]
        for item in range(stock.shape[1]):
            valid = np.isfinite(x) & np.isfinite(y[:, item])
            if valid.sum() < 24:
                continue
            centered_x = x[valid] - x[valid].mean()
            denominator = np.dot(centered_x, centered_x)
            if denominator > target.EPS:
                monthly[month, item] = np.dot(
                    centered_x, y[valid, item] - y[valid, item].mean()
                ) / denominator
    return target._expand_monthly(monthly, ends, arrays["returns"].shape)


def _slow_zhu_coskewness(arrays: dict[str, np.ndarray]) -> np.ndarray:
    returns = arrays["returns"]
    market = arrays["market_return"]
    result = np.full(returns.shape, np.nan)
    for end in range(19, len(returns)):
        y = returns[end - 19 : end + 1]
        x = market[end - 19 : end + 1]
        for stock in range(returns.shape[1]):
            valid = np.isfinite(x) & np.isfinite(y[:, stock])
            if valid.sum() < 15:
                continue
            centered_x = x[valid] - x[valid].mean()
            centered_y = y[valid, stock] - y[valid, stock].mean()
            denominator = np.sum(centered_x**3)
            if abs(denominator) > target.EPS:
                result[end, stock] = np.sum(centered_y * centered_x**2) / denominator
    return result


def _slow_tail(arrays: dict[str, np.ndarray], mode: str) -> np.ndarray:
    returns = arrays["returns"]
    market = arrays["market_return"]
    result = np.full(returns.shape, np.nan)
    bins = np.linspace(1.5, 4.5, 13)
    for end in range(62, len(returns)):
        x = market[end - 62 : end + 1]
        for stock in range(returns.shape[1]):
            y = returns[end - 62 : end + 1, stock]
            valid = np.isfinite(x) & np.isfinite(y)
            if valid.sum() < 30:
                continue
            design = np.column_stack([np.ones(valid.sum()), x[valid], x[valid] ** 2])
            residual = y[valid] - design @ np.linalg.lstsq(design, y[valid], rcond=None)[0]
            standard = residual.std(ddof=1)
            if standard <= target.EPS:
                continue
            residual = (residual - residual.mean()) / standard
            e_value = np.mean(residual >= 1.5) - np.mean(residual <= -1.5)
            if mode == "e":
                result[end, stock] = e_value
                continue
            positive, _ = np.histogram(residual[residual >= 1.5], bins=bins)
            negative, _ = np.histogram(-residual[residual <= -1.5], bins=bins)
            p = positive / max(positive.sum(), 1)
            q = negative / max(negative.sum(), 1)
            distance = 0.5 * np.sum((np.sqrt(p) - np.sqrt(q)) ** 2)
            result[end, stock] = np.sign(e_value) * distance
    return result


def _slow_conditional_liquidity(arrays: dict[str, np.ndarray]) -> np.ndarray:
    returns = arrays["returns"]
    daily = np.abs(returns) / arrays["amount"]
    groups, ends = target._month_groups(len(returns), None)
    monthly = np.array([np.nanmean(daily[rows], axis=0) for rows in groups])
    result = np.full(monthly.shape, np.nan)
    for month in range(24, len(monthly)):
        start = max(0, month - 60)
        for stock in range(monthly.shape[1]):
            history = monthly[start:month, stock]
            previous = history[:-1]
            current = history[1:]
            valid = np.isfinite(previous) & np.isfinite(current)
            if valid.sum() < 23 or not np.isfinite(monthly[month - 1, stock]):
                continue
            design = np.column_stack([np.ones(valid.sum()), previous[valid]])
            coefficient = np.linalg.lstsq(design, current[valid], rcond=None)[0]
            expected = coefficient[0] + coefficient[1] * monthly[month - 1, stock]
            result[month, stock] = expected - monthly[month, stock]
    return target._expand_monthly(result, ends, returns.shape)


def _slow_gammar(arrays: dict[str, np.ndarray]) -> np.ndarray:
    returns = arrays["returns"]
    excess = returns - arrays["market_return"][:, None]
    groups, ends = target._month_groups(len(returns), None)
    monthly = np.full((len(groups), returns.shape[1]), np.nan)
    for month, rows in enumerate(groups):
        for stock in range(returns.shape[1]):
            x1 = returns[rows[:-1], stock]
            x2 = np.sign(excess[rows[:-1], stock]) * arrays["amount"][rows[:-1], stock]
            y = excess[rows[1:], stock]
            valid = np.isfinite(x1) & np.isfinite(x2) & np.isfinite(y)
            if valid.sum() < 8:
                continue
            design = np.column_stack([np.ones(valid.sum()), x1[valid], x2[valid]])
            coefficient = np.linalg.lstsq(design, y[valid], rcond=None)[0]
            monthly[month, stock] = -abs(coefficient[2])
    return target._expand_monthly(monthly, ends, returns.shape)


def test_registry_metadata_has_strict_trial_and_unsupported() -> None:
    metadata = target.factor_metadata()
    assert {row["status"] for row in metadata} == {"strict", "trial", "unsupported"}
    assert target.get_factor_metadata("RI")["name"] == "区域指数 RI"
    assert target.get_factor_metadata("基于月度收益的市场 Beta")["status"] == "trial"
    assert target.get_factor_metadata("基于排序动量")["name"] == "基于排序的动量"
    assert target.get_factor_metadata("带条件冲击")["status"] == "trial"
    assert target.get_factor_metadata("CGO")["required"] == (
        "close",
        "vwap_qfq",
        "turnover或volume+float_shares",
    )
    assert "21" in str(target.get_factor_metadata("月度市场Beta")["note"])


def test_strict_daily_formulas_return_float32_panel() -> None:
    arrays = _sample()
    for name in ("区域指数 RI", "摇摆指数 SWI", "市值调整换手率", "协偏度（朱剑涛版）"):
        result = target.compute_factor(name, arrays)
        assert result.shape == arrays["close"].shape, name
        assert result.dtype == np.float32, name
        assert np.isfinite(result[-1]).any(), name


def test_zhu_coskewness_matches_written_formula() -> None:
    market = np.linspace(-0.04, 0.05, 20) ** 2 - 0.0005
    stock = 1.5 * market + 0.8 * market**2
    arrays = {"returns": stock[:, None], "market_return": market}
    result = target.compute_factor("朱剑涛协偏度", arrays)
    centered_market = market - market.mean()
    centered_stock = stock - stock.mean()
    expected = np.sum(centered_stock * centered_market**2) / np.sum(centered_market**3)
    assert result[-1, 0] == pytest.approx(expected, rel=1e-5)


def test_expand_monthly_keeps_value_until_next_month_end_and_resets_missing() -> None:
    monthly = np.array([[1.0, 10.0], [2.0, np.nan], [np.nan, 30.0]])
    ends = np.array([2, 5, 8])
    result = target._expand_monthly(monthly, ends, (10, 2))
    np.testing.assert_allclose(result[ends], monthly, equal_nan=True)
    np.testing.assert_allclose(result[2:5], np.tile(monthly[0], (3, 1)))
    np.testing.assert_allclose(result[5:8, 0], 2.0)
    assert np.isnan(result[5:8, 1]).all()
    assert np.isnan(result[8:, 0]).all()
    np.testing.assert_allclose(result[8:, 1], 30.0)


def test_monthly_factors_accept_real_dates_and_keep_month_end_values() -> None:
    days = np.arange(np.datetime64("2021-01-01"), np.datetime64("2023-03-01"))
    weekdays = days[np.is_busday(days)]
    arrays = _sample(len(weekdays), 4)
    result = target.compute_factor("收益季节性", arrays, dates=weekdays)
    months = weekdays.astype("datetime64[M]")
    month_ends = np.r_[months[1:] != months[:-1], True]
    stock, market, ends = target._monthly_returns(arrays, None, weekdays)
    expected_monthly = np.full(stock.shape, np.nan)
    expected_monthly[12:] = (stock - market[:, None])[:-12]
    np.testing.assert_allclose(result[ends], expected_monthly, equal_nan=True)
    assert np.isfinite(result[~month_ends]).any()
    assert np.isfinite(result[month_ends][-10:]).any()
    beta = target.compute_factor("月度市场Beta", arrays, dates=weekdays)
    assert np.isfinite(beta[month_ends][-3:]).any()


def test_pv_composites_tail_probabilities_and_snr_reversal() -> None:
    arrays = _sample(100, 8)
    for name in ("PV_corr_std", "PV_corr_trend", "PV_corr", "CPV", "异常尾部概率E", "异常尾部概率S", "信噪比增强反转"):
        result = target.compute_factor(name, arrays)
        assert result.dtype == np.float32, name
        assert np.isfinite(result[-1]).any(), name


def test_price_delay_has_month_end_values() -> None:
    arrays = _sample(84, 6)
    result = target.compute_factor("价格时滞", arrays)
    assert np.isfinite(result[[20, 41, 62, 83]]).any()


def test_liquidity_gammar_and_tsmom_use_monthly_output() -> None:
    arrays = _sample(630, 4)
    for name in ("流动性冲击", "带条件的流动性冲击", "Gammar", "TSMOM"):
        result = target.compute_factor(name, arrays)
        assert result.dtype == np.float32, name
        assert np.isfinite(result).any(), name


def test_unsupported_and_unknown_names_fail_explicitly() -> None:
    with pytest.raises(target.UnsupportedFactorError, match="FF3"):
        target.compute_factor("月度特质波动率", _sample())
    with pytest.raises(target.UnsupportedFactorError, match="公告日期"):
        target.compute_factor("盈余公告股票跳动", _sample())
    with pytest.raises(KeyError, match="没有登记"):
        target.compute_factor("不存在的名称", _sample())


def test_array_regressions_match_previous_small_sample_results() -> None:
    short = _sample(84, 7)
    np.testing.assert_allclose(
        target.price_delay(short), _slow_price_delay(short), rtol=1e-8, atol=1e-10, equal_nan=True
    )
    np.testing.assert_allclose(
        target.gammar(short), _slow_gammar(short), rtol=1e-7, atol=1e-16, equal_nan=True
    )

    long = _sample(630, 7)
    np.testing.assert_allclose(
        target.monthly_market_beta(long),
        _slow_monthly_beta(long),
        rtol=1e-10,
        atol=1e-12,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        target.conditional_liquidity_shock(long),
        _slow_conditional_liquidity(long),
        rtol=1e-7,
        atol=1e-18,
        equal_nan=True,
    )
    constant = {
        "returns": np.full((630, 2), 0.01),
        "amount": np.full((630, 2), 1e8),
        "market_return": np.zeros(630),
    }
    np.testing.assert_allclose(
        target.conditional_liquidity_shock(constant),
        _slow_conditional_liquidity(constant),
        atol=1e-24,
        equal_nan=True,
    )


def test_array_rolling_statistics_match_previous_small_sample_results() -> None:
    arrays = _sample(80, 7)
    np.testing.assert_allclose(
        target.zhu_coskewness(arrays),
        _slow_zhu_coskewness(arrays),
        rtol=1e-8,
        atol=1e-10,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        target.abnormal_tail_e(arrays),
        _slow_tail(arrays, "e"),
        rtol=0,
        atol=1e-12,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        target.abnormal_tail_s(arrays),
        _slow_tail(arrays, "s"),
        rtol=0,
        atol=1e-12,
        equal_nan=True,
    )


def test_array_spread_bias_matches_previous_peer_selection() -> None:
    arrays = _sample(280, 14)
    current = target.spread_bias(arrays)
    previous = _slow_spread_bias(arrays)
    _, ends = target._month_groups(len(arrays["close"]), None)
    np.testing.assert_allclose(
        current[ends],
        previous[ends],
        rtol=1e-10,
        atol=1e-12,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        current[ends[-2] : ends[-1]],
        np.tile(current[ends[-2]], (ends[-1] - ends[-2], 1)),
        equal_nan=True,
    )

    missing_new_month = {key: value.copy() for key, value in arrays.items()}
    missing_new_month["close"][ends[-1] :] = np.nan
    reset = target.spread_bias(missing_new_month)
    assert np.isfinite(reset[ends[-2]]).any()
    assert np.isnan(reset[ends[-1] :]).all()
