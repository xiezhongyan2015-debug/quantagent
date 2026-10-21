# -*- coding: utf-8 -*-

import numpy as np
import pytest

import historical_missing_minute_factors as factors


def _sample(days: int = 65, stocks: int = 6) -> dict[str, np.ndarray]:
    t = np.arange(days, dtype=np.float64)[:, None]
    s = np.arange(stocks, dtype=np.float64)[None, :]
    wave = np.sin(t / 4.0 + s / 3.0)
    close = 10.0 * np.exp(0.002 * t + 0.001 * s * t / max(days, 1))
    return {
        "close_auction_volume_share_5m": 0.04 + 0.0002 * t + 0.001 * s,
        "price_diff_pos_corr": 0.1 * wave + 0.01 * s,
        "price_diff_neg_corr": -0.08 * wave + 0.005 * s,
        "price_diff_pos_pair_corr": 0.07 * wave + 0.01 * s,
        "price_diff_neg_pair_corr": -0.06 * wave + 0.02 * s,
        "volume_diff_pos_corr": 0.09 * wave + 0.012 * s,
        "volume_diff_neg_corr": -0.04 * wave + 0.007 * s,
        "volume_diff_pos_pair_corr": 0.05 * wave + 0.013 * s,
        "volume_diff_neg_pair_corr": -0.03 * wave + 0.018 * s,
        "minute_volume_bin_entropy_trial": 1.0 + 0.1 * wave,
        "top10pct_minute_return": 0.01 + 0.001 * wave,
        "morning_return": 0.002 * wave + 0.0003 * s,
        "afternoon_return": -0.0015 * wave + 0.0001 * s,
        "semibeta_market_pos_asset_pos": 0.3 + 0.01 * wave,
        "semibeta_market_neg_asset_pos": 0.2 + 0.01 * wave,
        "semibeta_market_pos_asset_neg": 0.1 + 0.01 * wave,
        "semibeta_market_neg_asset_neg": 0.4 + 0.01 * wave,
        "abnormal_high_vol_return_ratio_trial": 0.02 + 0.002 * wave,
        "follow_wave_base": wave + 0.002 * t * s,
        "a22_intraday_snr_trial": 0.5 + 0.02 * wave,
        "close": close,
        "total_shares": np.ones_like(close) * (1_000_000.0 + 100_000.0 * s),
        "codes": np.array([f"{index:06d}" for index in range(stocks)]),
    }


def test_bcvp_and_entropy_use_twenty_days() -> None:
    data = _sample()
    bcvp = factors.compute_factor("BCVP", data)
    entropy = factors.compute_factor("成交量分桶熵", data)
    assert np.isnan(bcvp[18]).all()
    np.testing.assert_allclose(
        bcvp[19], data["close_auction_volume_share_5m"][:20].mean(axis=0), rtol=1e-6
    )
    np.testing.assert_allclose(
        entropy[19], data["minute_volume_bin_entropy_trial"][:20].std(axis=0, ddof=1),
        rtol=1e-5,
    )


def test_autocorrelation_formulas_and_optional_size_fields() -> None:
    data = _sample()
    single = factors.compute_factor("CDPP", data)
    pair = factors.compute_factor("CDPDP", data)
    assert np.isfinite(single[-1]).all()
    assert np.isfinite(pair[-1]).all()
    no_size = dict(data)
    no_size.pop("close")
    no_size.pop("total_shares")
    raw = factors.compute_factor("CDVV", no_size)
    assert np.isfinite(raw[-1]).all()


def test_direct_semibeta_and_membership() -> None:
    data = _sample()
    member = np.ones(data["close"].shape, dtype=bool)
    member[-1, 0] = False
    result = factors.compute_factor(
        "SemiBeta（正市场收益&正资产收益）", data, membership=member
    )
    assert np.isnan(result[-1, 0])
    np.testing.assert_allclose(
        result[-1, 1:], data["semibeta_market_pos_asset_pos"][-1, 1:], rtol=1e-6
    )


def test_apm_returns_momentum_neutral_cross_section() -> None:
    data = _sample(70, 8)
    result = factors.compute_factor("APM", data)
    assert np.isfinite(result[-1]).all()
    momentum = data["close"][-1] / data["close"][-21] - 1.0
    correlation = np.corrcoef(result[-1], momentum)[0, 1]
    assert abs(correlation) < 1e-8


def test_follow_wave_carries_month_end_value_until_next_month_end() -> None:
    data = _sample(45, 5)
    dates = np.arange("2024-01-01", "2024-02-15", dtype="datetime64[D]")
    result = factors.compute_factor("随波逐流", data, dates=dates)
    january_end = int(np.flatnonzero(dates.astype("datetime64[M]") == np.datetime64("2024-01"))[-1])
    february_end = len(dates) - 1
    assert np.isfinite(result[january_end]).all()
    assert np.isnan(result[:january_end]).all()
    carried = result[january_end:february_end]
    np.testing.assert_allclose(
        carried, np.broadcast_to(result[january_end], carried.shape)
    )
    assert np.isfinite(result[february_end]).all()


def test_follow_wave_stops_carrying_when_new_month_end_is_missing() -> None:
    dates = np.arange("2024-01-01", "2024-03-16", dtype="datetime64[D]")
    data = _sample(len(dates), 5)
    months = dates.astype("datetime64[M]")
    january_end = int(np.flatnonzero(months == np.datetime64("2024-01"))[-1])
    february_end = int(np.flatnonzero(months == np.datetime64("2024-02"))[-1])
    data["follow_wave_base"][february_end] = np.nan

    result = factors.compute_factor("随波逐流", data, dates=dates)

    assert np.isfinite(result[january_end]).all()
    carried = result[january_end:february_end]
    np.testing.assert_allclose(
        carried, np.broadcast_to(result[january_end], carried.shape)
    )
    assert np.isnan(result[february_end:]).all()


def test_multilayer_snr_trial_is_explicit_equal_weight_formula() -> None:
    data = _sample()
    result = factors.compute_factor("多层加权日内信噪比", data)
    base = data["a22_intraday_snr_trial"]
    expected = 0.5 * base[2] + 0.5 * base[:3].mean(axis=0)
    np.testing.assert_allclose(result[2], expected, rtol=1e-6)
    assert np.isnan(result[:2]).all()


def test_metadata_and_all_registered_factors() -> None:
    data = _sample(70, 8)
    dates = np.arange("2024-01-01", "2024-03-11", dtype="datetime64[D]")
    assert len(factors.supported_factor_names()) == 15
    assert factors.get_factor_metadata("BCVP")["status"] == "strict"
    assert factors.get_factor_metadata("随波逐流")["status"] == "trial"
    assert factors.REQUIRED_COLUMNS["APM"] == (
        "morning_return", "afternoon_return", "close"
    )
    for name in factors.supported_factor_names():
        result = factors.compute_factor(name, data, dates=dates)
        assert result.shape == data["close"].shape, name
        assert result.dtype == np.float32, name


def test_missing_field_has_clear_error() -> None:
    data = _sample()
    data.pop("morning_return")
    with pytest.raises(KeyError, match="morning_return"):
        factors.compute_factor("APM", data)
