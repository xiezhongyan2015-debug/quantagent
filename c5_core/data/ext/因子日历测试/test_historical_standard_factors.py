# -*- coding: utf-8 -*-

import numpy as np
import pytest

import historical_standard_factors as factors


def _sample(days: int = 1400, stocks: int = 3) -> dict[str, np.ndarray]:
    time = np.arange(days, dtype=np.float64)[:, None]
    stock = np.arange(stocks, dtype=np.float64)[None, :]
    close = 10.0 + 0.02 * time + stock
    opening = close * 0.995
    high = np.maximum(opening, close) * 1.01
    low = np.minimum(opening, close) * 0.99
    volume = 1000.0 + 3.0 * time + 10.0 * stock
    return {
        "open": opening,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": volume * close,
        "float_shares": np.full_like(close, 1_000_000.0),
        "total_shares": np.full_like(close, 2_000_000.0),
        "qfq_ratio": np.ones_like(close),
    }


def test_registry_alias_and_output_contract() -> None:
    data = _sample()
    direct = factors.calculate_factor("流通股市值", data)
    alias = factors.calculate_factor(" 自由流通股市值 ", data)
    np.testing.assert_allclose(direct, alias, equal_nan=True)
    assert direct.shape == data["close"].shape
    assert direct.dtype == np.float32
    assert len(factors.supported_factor_names()) >= 70


def test_exact_market_value_turnover_and_true_range() -> None:
    data = _sample(30, 2)
    market_value = factors.calculate_factor("总市值", data)
    np.testing.assert_allclose(
        market_value,
        data["close"] * data["total_shares"],
        rtol=1e-6,
    )
    turnover = factors.calculate_factor("换手率", data)
    expected = np.mean(data["volume"][-20:] / data["float_shares"][-20:], axis=0)
    np.testing.assert_allclose(turnover[-1], expected, rtol=1e-6)
    true_range = factors.calculate_factor("真实波动范围 TR", data)
    previous = data["close"][-2]
    expected_tr = np.maximum.reduce(
        [
            data["high"][-1] - data["low"][-1],
            np.abs(data["high"][-1] - previous),
            np.abs(data["low"][-1] - previous),
        ]
    )
    np.testing.assert_allclose(true_range[-1], expected_tr, rtol=1e-6)


def test_momentum_macd_and_volume_indicators_are_finite() -> None:
    data = _sample(300, 2)
    for name in (
        "短期反转",
        "最近 52 周最高价因子",
        "平滑异同均线指标 MACD",
        "相对强弱指标 RSI",
        "成交量变动速率 VROC",
        "价量趋势指标 PVT",
        "Amihud 非流动性因子",
    ):
        result = factors.calculate_factor(name, data)
        assert np.isfinite(result[-1]).all(), name


def test_metadata_marks_trial_parameters() -> None:
    metadata = factors.get_factor_metadata("动量指标 MTM")
    assert metadata["name"] == "动量指标 MTM"
    assert "试算" in str(metadata["note"])
    assert metadata["required_fields"] == ("close",)


def test_missing_field_and_wrong_shape_are_rejected() -> None:
    data = _sample(30, 2)
    incomplete = {"close": data["close"]}
    with pytest.raises(KeyError, match="缺少字段"):
        factors.calculate_factor("总市值", incomplete)
    broken = dict(data)
    broken["total_shares"] = np.ones(30)
    with pytest.raises(ValueError, match="二维数组"):
        factors.calculate_factor("总市值", broken)


def test_all_registered_factors_run_on_complete_panel() -> None:
    data = _sample()
    for name in factors.supported_factor_names():
        result = factors.calculate_factor(name, data)
        assert result.shape == data["close"].shape, name
        assert result.dtype == np.float32, name

