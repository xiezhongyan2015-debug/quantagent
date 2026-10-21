# -*- coding: utf-8 -*-

from pathlib import Path
from statistics import NormalDist

import math

import duckdb
import numpy as np
import pandas as pd

import build_july_august_minute_features as target


def test_lone_goose_matches_full_pairwise_correlation() -> None:
    codes = ["a", "b", "c", "d"]
    amount = np.array(
        [
            [1.0, 2.0, 4.0, 3.0, 7.0],
            [2.0, 1.0, 0.0, 3.0, 5.0],
            [7.0, 5.0, 4.0, 2.0, 1.0],
            [3.0, 8.0, 2.0, 6.0, 4.0],
        ]
    )
    correlation = np.corrcoef(amount)
    expected = (np.abs(correlation).sum(axis=1) - 1.0) / 3.0
    result = target.compute_lone_goose_day(codes, amount, block_size=2)
    assert result["code"].tolist() == codes
    np.testing.assert_allclose(
        result["lone_goose_daily"], expected, rtol=1e-6, atol=1e-6
    )


def test_lone_goose_drops_constant_and_incomplete_sequences() -> None:
    result = target.compute_lone_goose_day(
        ["a", "constant", "short", "b"],
        [
            [1.0, 2.0, 3.0],
            [2.0, 2.0, 2.0],
            [1.0, 2.0],
            [3.0, 1.0, 4.0],
        ],
    )
    assert result["code"].tolist() == ["a", "b"]
    expected = abs(np.corrcoef([1.0, 2.0, 3.0], [3.0, 1.0, 4.0])[0, 1])
    np.testing.assert_allclose(
        result["lone_goose_daily"], [expected, expected], rtol=1e-6
    )


def test_lone_goose_stock_batches_match_yearly_query_exactly(
    tmp_path: Path,
) -> None:
    dates = [pd.Timestamp("2024-12-31"), pd.Timestamp("2025-01-02")]
    timestamps = pd.DatetimeIndex(
        [
            day + pd.Timedelta(hours=9, minutes=31 + minute)
            for day in dates
            for minute in range(4)
        ]
    )
    selected = np.tile([True, False, True, True], len(dates))
    market = pd.DataFrame(
        {
            "date": timestamps.normalize(),
            "datetime": timestamps,
            "is_non_differentiation_pop": selected,
        }
    )
    market_path = tmp_path / "market.parquet"
    market.to_parquet(market_path, index=False)

    files = []
    for stock_index in range(5):
        amount = (
            np.arange(len(timestamps), dtype=np.float64) + 1.0
        ) ** (1.0 + stock_index * 0.12)
        minute = pd.DataFrame({"datetime": timestamps, "amount": amount})
        if stock_index == 4:
            minute = minute.drop(index=6)
        path = tmp_path / f"sh60000{stock_index + 1}.parquet"
        minute.to_parquet(path, index=False)
        files.append(path)

    yearly_dir = tmp_path / "yearly"
    batched_dir = tmp_path / "batched"
    yearly_temp = tmp_path / "yearly_temp"
    batch_temp = tmp_path / "batch_temp"
    for directory in [yearly_dir, batched_dir, yearly_temp, batch_temp]:
        directory.mkdir()

    target._build_lone_goose_files(
        files,
        market_path,
        yearly_dir,
        "2024-12-31",
        "2025-01-02",
        yearly_temp,
        threads=1,
        memory_limit="512MB",
        block_size=2,
        force=True,
        stock_batch_size=0,
    )
    summary = target._build_lone_goose_files(
        files,
        market_path,
        batched_dir,
        "2024-12-31",
        "2025-01-02",
        batch_temp,
        threads=1,
        memory_limit="512MB",
        block_size=2,
        force=True,
        stock_batch_size=2,
    )

    assert summary["mode"] == "stock_batches"
    assert summary["sequence_cache"]["batches"] == 3
    cache_dir = Path(summary["sequence_cache"]["cache_dir"])
    cached = pd.read_parquet(cache_dir / "batch_00000.parquet")
    assert cached.columns.tolist() == target.LONE_GOOSE_SEQUENCE_COLUMNS
    assert cached["amount_sequence"].map(len).eq(
        cached["selected_minute_count"]
    ).all()

    for year in [2024, 2025]:
        expected = pd.read_parquet(
            yearly_dir / f"lone_goose_{year}.parquet"
        )
        actual = pd.read_parquet(
            batched_dir / f"lone_goose_{year}.parquet"
        )
        pd.testing.assert_frame_equal(actual, expected)

    (batched_dir / "lone_goose_2025.parquet").unlink()
    resumed = target._build_lone_goose_files(
        files,
        market_path,
        batched_dir,
        "2024-12-31",
        "2025-01-02",
        batch_temp,
        threads=1,
        memory_limit="512MB",
        block_size=2,
        force=False,
        stock_batch_size=2,
    )
    assert resumed["sequence_cache"]["completed_now"] == 0
    assert resumed["sequence_cache"]["skipped_existing"] == 3
    pd.testing.assert_frame_equal(
        pd.read_parquet(batched_dir / "lone_goose_2025.parquet"),
        pd.read_parquet(yearly_dir / "lone_goose_2025.parquet"),
    )


def test_ideal_amplitude_uses_last_ten_market_days() -> None:
    daily = pd.DataFrame(
        {
            "market_day_index": np.arange(11),
            "minute_close_list": [
                np.array([day + 1.0, day + 2.0]) for day in range(11)
            ],
            "minute_amplitude_list": [
                np.array([day / 100.0, (day + 1.0) / 100.0])
                for day in range(11)
            ],
        }
    )
    result = target.compute_ideal_amplitude(daily, window_days=10, fraction=0.25)
    assert np.isnan(result[:9]).all()
    for day in [9, 10]:
        left = day - 9
        close = np.concatenate(daily.loc[left:day, "minute_close_list"].tolist())
        amplitude = np.concatenate(
            daily.loc[left:day, "minute_amplitude_list"].tolist()
        )
        take = int(len(close) * 0.25)
        order = np.argsort(close, kind="stable")
        expected = amplitude[order[-take:]].mean() - amplitude[order[:take]].mean()
        np.testing.assert_allclose(result[day], expected)


def _rolling_population_std(values: np.ndarray, window: int) -> np.ndarray:
    result = np.full(len(values), np.nan)
    for end in range(window - 1, len(values)):
        block = values[end - window + 1 : end + 1]
        if np.isfinite(block).all():
            result[end] = np.std(block, ddof=0)
    return result


def test_stock_daily_query_matches_independent_daily_formulas(tmp_path: Path) -> None:
    timestamps = pd.date_range("2025-07-01 09:31", periods=15, freq="min")
    close = np.array(
        [
            10.00,
            10.01,
            10.03,
            10.02,
            10.05,
            10.04,
            10.08,
            10.07,
            10.12,
            10.10,
            10.18,
            10.14,
            10.25,
            10.20,
            10.30,
        ]
    )
    volume = np.array(
        [100, 80, 120, 90, 150, 110, 180, 130, 210, 160, 250, 190, 300, 220, 350],
        dtype=float,
    )
    amount = volume * close
    minute = pd.DataFrame(
        {
            "datetime": timestamps,
            "open": close - 0.01,
            "high": close + 0.03,
            "low": close - 0.04,
            "close": close,
            "volume": volume,
            "amount": amount,
        }
    )
    market = pd.DataFrame(
        {
            "date": timestamps.normalize(),
            "datetime": timestamps,
            "market_day_index": np.zeros(len(timestamps), dtype=np.int32),
            "market_amount_total": amount * 10.0,
        }
    )
    minute_path = tmp_path / "sh600000.parquet"
    market_path = tmp_path / "market.parquet"
    minute.to_parquet(minute_path, index=False)
    market.to_parquet(market_path, index=False)

    with duckdb.connect() as connection:
        result = connection.execute(
            target.build_stock_daily_query(
                minute_path, market_path, "2025-07-01", "2025-07-01"
            )
        ).fetchdf()
    assert len(result) == 1
    row = result.iloc[0]

    simple_return = np.full(len(close), np.nan)
    simple_return[0] = close[0] / minute.loc[0, "open"] - 1.0
    simple_return[1:] = close[1:] / close[:-1] - 1.0
    log_return = np.full(len(close), np.nan)
    log_return[1:] = np.log(close[1:] / close[:-1])
    log_return[-1] = np.nan
    volatility = _rolling_population_std(log_return, 5)
    fuzziness = _rolling_population_std(volatility, 5)
    fuzzy_mean = np.nanmean(fuzziness)
    fog = fuzziness > fuzzy_mean
    expected_fuzzy = volume[fog].mean() / volume[1:-1].mean()
    np.testing.assert_allclose(row.fuzzy_quantity_ratio_daily, expected_fuzzy)

    median = np.nanmedian(simple_return)
    extreme = int(np.nanargmax(np.abs(simple_return - median)))
    np.testing.assert_allclose(row.extreme_return_daily, simple_return[extreme])
    np.testing.assert_allclose(
        row.pre_extreme_return_daily, simple_return[extreme - 1]
    )
    np.testing.assert_allclose(row.late_amount_share_30m, 1.0)
    np.testing.assert_allclose(row.minute_amount_variance_daily, np.var(amount))
    np.testing.assert_allclose(
        row.amount_autocorr_lag1_daily_trial,
        np.corrcoef(amount[:-1], amount[1:])[0, 1],
    )
    expected_log_return = np.r_[
        np.log(close[0] / minute.loc[0, "open"]),
        np.log(close[1:] / close[:-1]),
    ]
    np.testing.assert_allclose(
        row.absret_amount_corr_daily_trial,
        np.corrcoef(np.abs(expected_log_return), amount)[0, 1],
    )
    amount_percent = amount / (amount * 10.0)
    low_order = np.argsort(simple_return, kind="stable")[:30]
    prior_percent = np.r_[np.nan, amount_percent[:-1]]
    expected_amt_min = np.nansum(prior_percent[low_order]) / amount_percent.mean()
    np.testing.assert_allclose(row.amt_min_30_daily_trial, expected_amt_min)

    top_count = int(np.ceil(len(volume) / 3.0))
    top_volume = np.argsort(-volume, kind="stable")[:top_count]
    expected_corr = np.corrcoef(volume[top_volume], close[top_volume])[0, 1]
    np.testing.assert_allclose(row.large_volume_price_corr_daily, expected_corr)
    assert np.isfinite(row.disaster_rebuild_daily)
    assert row.minute_count == len(minute)


def test_market_query_marks_below_daily_mean_dispersion(tmp_path: Path) -> None:
    paths = []
    for code, scale in [("sh600001", 1.0), ("sh600002", 2.0), ("sh600003", -1.0)]:
        timestamps = pd.date_range("2025-07-01 09:31", periods=6, freq="min")
        returns = np.array([0.0, 0.01, -0.005, 0.02, -0.01, 0.003]) * scale
        close = 10.0 * np.cumprod(1.0 + returns)
        path = tmp_path / f"{code}.parquet"
        pd.DataFrame(
            {
                "datetime": timestamps,
                "open": np.r_[close[0], close[:-1]],
                "close": close,
                "amount": np.arange(1.0, 7.0) * 100.0,
            }
        ).to_parquet(path, index=False)
        paths.append(path)
    with duckdb.connect() as connection:
        market = connection.execute(
            target.build_market_query(paths, "2025-07-01", "2025-07-01")
        ).fetchdf()
    valid = market["market_std_return_pop"].notna()
    expected = (
        market.loc[valid, "market_std_return_pop"]
        < market.loc[valid, "market_std_return_pop"].mean()
    )
    assert (
        market.loc[valid, "is_non_differentiation_pop"].tolist()
        == expected.tolist()
    )
    assert market.loc[valid, "return_stock_count"].eq(3).all()
    assert market["market_std_return_samp"].notna().all()
    assert market["market_mean_return"].notna().all()


def test_significance_weighted_jump_asymmetry_matches_fixed_trial_formula() -> None:
    rng = np.random.default_rng(7)
    rows = []
    source_returns = []
    for day in range(25):
        returns = rng.normal(0.0, 0.002, 48)
        returns[10] = 0.04 if day % 2 == 0 else -0.04
        close_5m = np.exp(np.cumsum(returns))
        rows.append(
            {
                "minute_close_list": np.repeat(close_5m, 5),
                "day_open": 1.0,
                "market_day_index": day,
            }
        )
        source_returns.append(returns)
    daily = pd.DataFrame(rows)
    srjv, statistic = target.compute_daily_jump_asymmetry_and_statistic(daily)

    returns = source_returns[0]
    power = np.abs(returns) ** (2.0 / 3.0)
    mu_two_thirds = (
        2.0 ** (1.0 / 3.0)
        * math.gamma(5.0 / 6.0)
        / math.sqrt(math.pi)
    )
    integrated = mu_two_thirds ** -3 * np.sum(
        power[2:] * power[1:-1] * power[:-2]
    )
    positive = np.sum(np.where(returns > 0, returns**2, 0.0))
    negative = np.sum(np.where(returns < 0, returns**2, 0.0))
    expected_srjv = max(positive - 0.5 * integrated, 0.0) - max(
        negative - 0.5 * integrated, 0.0
    )
    np.testing.assert_allclose(srjv[0], expected_srjv)

    simple = np.expm1(returns)
    count = len(returns)
    rv = np.sum(returns**2)
    swv = 2.0 * np.sum(simple - returns)
    mu_one = math.sqrt(2.0 / math.pi)
    bipower = (
        mu_one ** -2
        * count
        / (count - 1.0)
        * np.sum(np.abs(returns[1:]) * np.abs(returns[:-1]))
    )
    mu_three_halves = 2.0 ** 0.75 * math.gamma(1.25) / math.sqrt(math.pi)
    powered = np.abs(returns) ** 1.5
    omega = (
        (15.0 / 9.0)
        * count**3
        / (count - 3.0)
        * mu_three_halves ** -4
        * np.sum(
            powered[:-3]
            * powered[1:-2]
            * powered[2:-1]
            * powered[3:]
        )
    )
    expected_statistic = bipower / np.sqrt(omega / count) * (1.0 - rv / swv)
    np.testing.assert_allclose(statistic[0], expected_statistic)

    result = target.compute_significance_weighted_jump_asymmetry(daily)
    weight = np.maximum(
        statistic / NormalDist().inv_cdf(0.99),
        0.0,
    )
    for end in range(19, 25):
        denominator = np.sum(weight[end - 19 : end + 1])
        expected = np.sum(
            srjv[end - 19 : end + 1] * weight[end - 19 : end + 1]
        ) / denominator
        np.testing.assert_allclose(result[end], expected)


def test_trial_parameter_record_marks_all_four_as_trial() -> None:
    assert set(target.TRIAL_PARAMETERS) == {
        "J07_D05_SIG_JUMP_ASYM",
        "J07_D08_AMOUNT_AUTOCORR",
        "J08_D08_ABSRET_AMOUNT_CORR",
        "J08_D25_AMT_MIN",
    }
    assert all(
        item["formula_mode"] == "trial"
        for item in target.TRIAL_PARAMETERS.values()
    )
