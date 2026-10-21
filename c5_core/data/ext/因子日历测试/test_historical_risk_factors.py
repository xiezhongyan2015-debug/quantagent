# -*- coding: utf-8 -*-

import numpy as np
import pytest

import historical_risk_factors as target


def sample_arrays() -> dict[str, np.ndarray]:
    market = np.array([0.01, -0.02, 0.03, -0.01, 0.02, -0.03], dtype=float)
    returns = np.column_stack([2.0 * market, -market, market + 0.005])
    close = 10.0 * np.cumprod(1.0 + returns, axis=0)
    return {
        "returns": returns,
        "close": close,
        "market_return": market,
        "volume": np.tile(np.arange(1.0, 7.0)[:, None], (1, 3)) * 100.0,
        "float_shares": np.full((6, 3), 10_000.0),
        "amount": np.tile(np.arange(10.0, 70.0, 10.0)[:, None], (1, 3)),
        "pv_corr_daily": np.column_stack([
            np.arange(1.0, 7.0),
            np.arange(6.0, 0.0, -1.0),
            np.ones(6),
        ]),
    }


def test_name_normalization_and_registry_aliases() -> None:
    assert target.normalize_chinese_name(" Amihud 非流动性因子 ") == "amihud非流动性"
    assert target.get_factor_spec("PV_corr_std").name == "价量相关性波动性"
    assert target.get_factor_spec("基于日度收益的市场 Beta").name == "市场Beta"


def test_market_beta_and_equal_weight_market() -> None:
    arrays = sample_arrays()
    beta = target.compute_factor("市场Beta", arrays, window=4, min_periods=4)
    assert beta.dtype == np.float32
    assert beta[-1, 0] == pytest.approx(2.0, abs=1e-6)
    assert beta[-1, 1] == pytest.approx(-1.0, abs=1e-6)

    without_market = dict(arrays)
    without_market.pop("market_return")
    membership = np.ones(arrays["returns"].shape, dtype=bool)
    expected = arrays["returns"].mean(axis=1)
    np.testing.assert_allclose(
        target.market_equal_weight_return(without_market, membership), expected
    )

    close_only = {"close": np.array([[10.0], [11.0], [9.9]])}
    np.testing.assert_allclose(
        target._returns(close_only)[1:, 0], [0.10, -0.10], atol=1e-12
    )

    downside = target.compute_factor(
        "下行贝塔", arrays, window=6, min_periods=2
    )
    assert downside[-1, 0] == pytest.approx(2.0, abs=1e-6)
    assert downside[-1, 1] == pytest.approx(-1.0, abs=1e-6)


def test_dimson_beta_sums_three_market_coefficients() -> None:
    market = np.array([0.02, -0.01, 0.03, -0.02, 0.01, 0.04, -0.03, 0.02])
    lag = np.roll(market, 1)
    lead = np.roll(market, -1)
    stock = 0.5 * lag + 1.2 * market - 0.3 * lead
    arrays = {"returns": stock[:, None], "market_return": market}
    result = target.compute_factor("Dimson贝塔", arrays, window=6, min_periods=5)
    assert result[-2, 0] == pytest.approx(1.4, abs=1e-5)


def test_turnover_and_amount_statistics() -> None:
    arrays = sample_arrays()
    mean = target.compute_factor("换手率", arrays, window=3)
    expected = np.mean(np.array([4.0, 5.0, 6.0]) * 100.0 / 10_000.0)
    assert mean[-1, 0] == pytest.approx(expected)

    coefficient = target.compute_factor("成交额波动系数", arrays, window=3)
    assert coefficient[-1, 0] == pytest.approx(50.0 / 10.0)


def test_amihud_negative_illiquidity_and_cv() -> None:
    arrays = sample_arrays()
    amihud = target.compute_factor("Amihud非流动性", arrays, window=3)
    expected = np.mean(np.abs(arrays["returns"][-3:, 0]) / arrays["amount"][-3:, 0])
    assert amihud[-1, 0] == pytest.approx(expected)

    negative = target.compute_factor(
        "负收益非流动性", arrays, window=6, min_periods=1
    )
    mask = arrays["returns"][:, 0] < 0
    expected_negative = np.mean(
        np.abs(arrays["returns"][mask, 0]) / arrays["amount"][mask, 0]
    )
    assert negative[-1, 0] == pytest.approx(expected_negative)
    assert np.isfinite(target.compute_factor("CVILLIQ", arrays, window=3)[-1, 0])


def test_return_statistics_and_var() -> None:
    arrays = sample_arrays()
    volatility = target.compute_factor("总波动率", arrays, window=4)
    assert volatility[-1, 0] == pytest.approx(
        np.std(arrays["returns"][-4:, 0], ddof=1)
    )
    maximum = target.compute_factor("最大收益率", arrays, window=4, top_n=2)
    assert maximum[-1, 0] == pytest.approx(
        np.sort(arrays["returns"][-4:, 0])[-2:].mean()
    )
    var = target.compute_factor("在险价值", arrays, window=4, alpha=0.25)
    assert var[-1, 0] == pytest.approx(
        np.quantile(arrays["returns"][-4:, 0], 0.25)
    )


def test_semibeta_quadrants() -> None:
    arrays = sample_arrays()
    result = target.compute_factor(
        "SemiBeta（负市场收益、负资产收益）",
        arrays,
        window=6,
        min_periods=2,
    )
    assert result[-1, 0] == pytest.approx(2.0)


def test_coskewness_duvol_and_tail_beta_are_finite() -> None:
    rng = np.random.default_rng(7)
    market = rng.normal(0.0, 0.02, 80)
    returns = np.column_stack([
        market + 0.5 * market**2 + rng.normal(0, 0.005, 80),
        -0.5 * market + rng.normal(0, 0.01, 80),
        rng.normal(0, 0.02, 80),
        rng.normal(0, 0.03, 80),
    ])
    arrays = {"returns": returns, "market_return": market}
    membership = np.ones(returns.shape, dtype=bool)
    assert np.isfinite(target.compute_factor("协偏度", arrays, window=40)[-1]).all()
    assert np.isfinite(
        target.compute_factor("上下行波动率DUVOL", arrays, window=40)[-1]
    ).all()
    assert np.isfinite(
        target.compute_factor(
            "尾部Beta", arrays, membership, window=40, alpha=0.25, min_periods=8
        )[-1]
    ).all()


def test_pv_corr_transforms() -> None:
    arrays = sample_arrays()
    mean = target.compute_factor("PV_corr_avg", arrays, window=3, neutralize=False)
    trend = target.compute_factor(
        "价量相关性趋势因子", arrays, window=3, neutralize=False
    )
    assert mean[-1, 0] == pytest.approx(5.0)
    assert trend[-1, 0] == pytest.approx(1.0)
    assert trend[-1, 1] == pytest.approx(-1.0)


def test_pv_corr_mean_can_remove_market_cap_effect() -> None:
    days, stocks = 4, 6
    log_cap = np.tile(np.arange(1.0, stocks + 1.0), (days, 1))
    daily = 2.0 + 3.0 * log_cap
    arrays = {
        "pv_corr_daily": daily,
        "log_market_cap": log_cap,
    }
    result = target.compute_factor(
        "价量相关性平均数",
        arrays,
        window=2,
        min_stocks=6,
    )
    np.testing.assert_allclose(result[-1], 0.0, atol=1e-6)


def test_unsupported_factors_fail_explicitly_and_metadata_is_unique() -> None:
    with pytest.raises(target.UnsupportedFactorError, match="FF3"):
        target.compute_factor("月度特质波动率", sample_arrays())
    metadata = target.factor_metadata()
    names = [row["normalized_name"] for row in metadata]
    assert len(names) == len(set(names))
    assert {row["status"] for row in metadata} == {
        "strict",
        "trial",
        "unsupported",
    }
