# -*- coding: utf-8 -*-

import numpy as np
import pytest

import historical_cached_factors as cached


def _sample(days: int = 300, stocks: int = 4) -> dict[str, np.ndarray]:
    t = np.arange(days, dtype=np.float64)[:, None]
    s = np.arange(stocks, dtype=np.float64)[None, :]
    close = 10.0 + 0.01 * t + s
    opening = close * (1.0 + 0.002 * np.sin(t / 5.0))
    volume = 1000.0 + 2.0 * t + 3.0 * s
    data = {
        "open": opening,
        "high": np.maximum(opening, close) * 1.01,
        "low": np.minimum(opening, close) * 0.99,
        "close": close,
        "volume": volume,
        "amount": volume * close,
        "float_shares": np.full_like(close, 1_000_000.0),
        "total_shares": np.full_like(close, 2_000_000.0) * (1.0 + s / 10.0),
        "morning_afternoon_volume_ratio": 0.8 + 0.001 * t + 0.01 * s,
        "time_avg_relative_price": 0.4 + 0.01 * s + np.zeros_like(t),
        "realized_skew_1m": np.sin(t / 10.0) + 0.01 * s,
        "realized_skew_5m": np.sin(t / 12.0) + 0.01 * s,
        "pv_corr_daily": np.cos(t / 9.0) + 0.02 * s,
        "absret_lag_amount_corr": np.sin(t / 8.0) + 0.01 * s,
        "lag_absret_amount_corr": np.cos(t / 8.0) + 0.01 * s,
        "adj_lag_absret_amount_corr_daily": np.sin(t / 7.0) + 0.01 * s,
        "j11_d04_adj_absret_lag_amount_corr_daily_trial": np.cos(t / 6.0) + 0.02 * s,
        "j09_d11_naive_active_share_trial": 0.5 + 0.001 * t + 0.01 * s,
        "consistent_trading_share_daily_trial": 0.3 + 0.001 * t + 0.01 * s,
        "consistent_buying_share_daily_trial": 0.2 + 0.001 * t + 0.01 * s,
        "consistent_selling_share_daily_trial": 0.1 + 0.001 * t + 0.01 * s,
        "large_up_jump_5m": 0.02 + np.zeros_like(close),
        "rvjn_5m": 0.03 + np.zeros_like(close),
        "small_down_jump_vol_5m": 0.01 + np.zeros_like(close),
        "up_jump_volatility_5m": 0.025 + np.zeros_like(close),
        "overnight_return": 0.005 * np.sin(t) + np.zeros_like(s),
        "volume_std_1m": 10.0 + 0.1 * t + np.zeros_like(s),
    }
    return data


def test_direct_cached_field_and_alias() -> None:
    data = _sample()
    first = cached.compute_factor("时间加权平均的相对价格位置 ARPP", data)
    second = cached.calculate_factor("时间加权平均的股票相对价格位置 ARPP", data)
    np.testing.assert_allclose(first, data["time_avg_relative_price"], rtol=1e-6)
    np.testing.assert_allclose(first, second, rtol=1e-6)
    assert first.dtype == np.float32


def test_rolling_volume_ratio_and_consistent_trade() -> None:
    data = _sample(40, 2)
    ratio = cached.compute_factor("成交量比值", data)
    expected = data["morning_afternoon_volume_ratio"][-20:].mean(axis=0)
    np.testing.assert_allclose(ratio[-1], expected, rtol=1e-6)
    buying = cached.compute_factor("一致买入交易因子 PCV", data)
    np.testing.assert_allclose(
        buying[-1], data["consistent_buying_share_daily_trial"][-20:].mean(axis=0), rtol=1e-6
    )


def test_jump_components_use_cached_daily_values() -> None:
    data = _sample(30, 2)
    large_down = cached.compute_factor("大的下行跳跃波动率", data)
    small_up = cached.compute_factor("小的上行跳跃波动率", data)
    asymmetry = cached.compute_factor("小的上下行跳跃波动不对称性", data)
    np.testing.assert_allclose(large_down[-1], 0.02, rtol=1e-6)
    np.testing.assert_allclose(small_up[-1], 0.005, rtol=1e-6)
    np.testing.assert_allclose(asymmetry[-1], -0.005, rtol=1e-6)


def test_daily_amount_statistics_and_standard_delegate() -> None:
    data = _sample(300, 3)
    average = cached.compute_factor("成交额", data)
    np.testing.assert_allclose(average[-1], data["amount"][-20:].mean(axis=0), rtol=1e-6)
    psy = cached.compute_factor("心理线", data)
    assert np.isfinite(psy[-1]).all()
    assert psy.dtype == np.float32


def test_adjusted_abs_return_lag_amount_corr_uses_correct_lag_direction() -> None:
    data = _sample(40, 3)
    actual = cached.compute_factor("绝对收益与调整后滞后成交量相关性", data)
    correct = data["j11_d04_adj_absret_lag_amount_corr_daily_trial"][-10:].mean(axis=0)
    reversed_direction = data["adj_lag_absret_amount_corr_daily"][-10:].mean(axis=0)
    np.testing.assert_allclose(actual[-1], correct, rtol=1e-6)
    assert not np.allclose(actual[-1], reversed_direction)


def test_unsupported_and_missing_column_messages() -> None:
    data = _sample()
    with pytest.raises(NotImplementedError, match="重新读取分钟数据"):
        cached.compute_factor("收盘集合竞价成交量占比 BCVP", data)
    broken = dict(data)
    broken.pop("realized_skew_1m")
    with pytest.raises(KeyError, match="realized_skew_1m"):
        cached.compute_factor("高频收益偏度", broken)


def test_all_supported_factors_return_float32_panel() -> None:
    data = _sample()
    for name in cached.supported_factor_names():
        result = cached.compute_factor(name, data)
        assert result.shape == data["close"].shape, name
        assert result.dtype == np.float32, name


def test_required_columns_and_metadata_are_exposed() -> None:
    assert cached.REQUIRED_COLUMNS["成交量比值"] == ("morning_afternoon_volume_ratio",)
    assert cached.REQUIRED_COLUMNS["一致买入交易因子 PCV"] == (
        "consistent_buying_share_daily_trial",
    )
    metadata = cached.get_factor_metadata("一致交易 TCV")
    assert metadata["required_columns"] == ("consistent_trading_share_daily_trial",)
    assert "20 日" in str(metadata["note"])
