# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import build_may_minute_features as target


def make_day(date: str) -> pd.DataFrame:
    minutes = list(range(9 * 60 + 31, 11 * 60 + 31)) + list(
        range(13 * 60 + 1, 15 * 60 + 1)
    )
    time_values = [
        pd.Timestamp(date) + pd.Timedelta(minutes=value) for value in minutes
    ]
    position = np.arange(target.MINUTES_PER_DAY, dtype=np.float64)
    close = 10.0 + 0.001 * position + 0.0001 * np.sin(position)
    return pd.DataFrame(
        {
            "datetime": time_values,
            "qfq_open": close - 0.001,
            "qfq_high": close + 0.002,
            "qfq_low": close - 0.002,
            "qfq_close": close,
            "volume": 1000.0 + position * position + 3.0 * position,
            "amount": close * (1000.0 + position * position + 3.0 * position),
            "float_shares": 1_000_000.0,
            "total_shares": 1_200_000.0,
            "qfq_ratio": 1.0,
            "source_id": "test",
        }
    )


def test_single_amount_share_entropy_uses_printed_product_without_renormalizing():
    close = np.array([[1.0, 3.0]])
    volume = np.array([[2.0, 2.0]])
    probability = np.array([0.5 * 0.25, 0.5 * 0.75])
    expected = -np.sum(probability * np.log(probability))
    result = target._single_amount_share_entropy(close, volume)
    np.testing.assert_allclose(result, [expected])


def test_b_volume_distribution_uses_distance_then_volume_order():
    close = np.array([[9.0, 10.0, 10.0, 11.0, 12.0]])
    volume = np.array([[10.0, 40.0, 10.0, 30.0, 10.0]])
    result = target._b_volume_distribution_gap(close, volume, np.array([9.0]))
    # VSP为10，先选VSP，再在等距离的9和11中选成交量更大的11。
    np.testing.assert_allclose(result, [2.0])


def test_realized_volatility_is_sum_of_squared_returns():
    values = np.array([[0.01, -0.02, 0.03]])
    np.testing.assert_allclose(
        target._realized_volatility(values), [0.01**2 + 0.02**2 + 0.03**2]
    )


def test_large_jump_asymmetry_changes_sign_when_returns_change_sign():
    values = np.full((1, 48), 0.0001)
    values[0, 10] = 0.08
    positive = target._large_jump_asymmetry(values)[0]
    negative = target._large_jump_asymmetry(-values)[0]
    assert positive > 0
    np.testing.assert_allclose(negative, -positive)


def test_night_sleep_regression_returns_signed_intercept_t_value():
    rng = np.random.default_rng(20260721)
    volume = rng.uniform(100.0, 500.0, size=(1, target.MINUTES_PER_DAY))
    difference = np.empty_like(volume)
    difference[:, 0] = volume[:, 0]
    difference[:, 1:] = np.diff(volume, axis=1)
    returns = np.full_like(volume, np.nan)
    noise = rng.normal(0.0, 0.00001, target.MINUTES_PER_DAY - 5)
    returns[:, 5:] = 0.001 + 0.0000001 * difference[:, 5:] + noise
    result = target._night_sleep_t_intercept(returns, volume)
    assert np.isfinite(result[0])
    assert result[0] > 0


def test_peak_ridge_correlation_uses_only_current_and_past_dates():
    rng = np.random.default_rng(7)
    volume = rng.uniform(90.0, 110.0, size=(50, target.MINUTES_PER_DAY))
    prefix = target._same_time_peak_ridge_correlation(volume[:45])
    changed = volume.copy()
    changed[45:] *= 1000.0
    whole = target._same_time_peak_ridge_correlation(changed)
    np.testing.assert_allclose(whole[:45], prefix, equal_nan=True)
    assert np.isnan(whole[target.HISTORY_DAYS - 2])


def test_peak_ridge_correlation_uses_one_shared_twenty_day_window():
    volume = np.ones((20, target.MINUTES_PER_DAY), dtype=np.float64)
    # 10号分钟是孤立喷发；30、31号分钟是连续喷发。
    volume[0, 10] = 100.0
    volume[1, 20] = 100.0
    volume[1, 21] = 100.0
    volume[2, 10] = 100.0
    volume[2, 11] = 100.0
    volume[3, 40] = 100.0
    actual = target._same_time_peak_ridge_correlation(volume, history_days=20)[-1]

    threshold = volume.mean(axis=0) + volume.std(axis=0, ddof=0)
    bursts = volume > threshold
    peaks, ridges = target.january.classify_volume_bursts(bursts)
    expected = np.corrcoef(peaks.sum(axis=0), ridges.sum(axis=0))[0, 1]
    np.testing.assert_allclose(actual, expected)


def test_build_daily_frame_has_only_may_columns():
    frame = pd.concat([make_day("2025-05-06"), make_day("2025-05-07")])
    result = target.build_daily_frame(frame, "sz000001")
    assert result.columns.tolist() == target.OUTPUT_COLUMNS
    assert result["code"].tolist() == ["sz000001", "sz000001"]
    assert result["realized_volatility_5m"].notna().all()


def test_process_writes_may_file_and_skips_existing(tmp_path: Path):
    input_path = tmp_path / "sz000001.parquet"
    output_dir = tmp_path / "output"
    pq.write_table(
        pa.Table.from_pandas(make_day("2025-05-06"), preserve_index=False),
        input_path,
    )
    first = target.process_one_stock(
        input_path, output_dir, "2025-05-01", "2025-05-31"
    )
    output_path = output_dir / "sz000001.parquet"
    saved = pq.read_table(output_path).to_pandas()
    modified_time = output_path.stat().st_mtime_ns
    second = target.process_one_stock(
        input_path, output_dir, "2025-05-01", "2025-05-31"
    )
    assert first == {"code": "sz000001", "status": "ok", "days": 1}
    assert saved.columns.tolist() == target.OUTPUT_COLUMNS
    assert second["status"] == "skipped"
    assert output_path.stat().st_mtime_ns == modified_time
    assert not list(output_dir.glob(".*.tmp"))


def test_outdated_file_is_rebuilt(tmp_path: Path):
    input_path = tmp_path / "sz000001.parquet"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    pq.write_table(
        pa.Table.from_pandas(make_day("2025-05-06"), preserve_index=False),
        input_path,
    )
    old = pd.DataFrame({"date": [pd.Timestamp("2025-05-06")], "code": ["sz000001"]})
    pq.write_table(
        pa.Table.from_pandas(old, preserve_index=False),
        output_dir / "sz000001.parquet",
    )
    result = target.process_one_stock(
        input_path, output_dir, "2025-05-01", "2025-05-31"
    )
    saved = pq.read_table(output_dir / "sz000001.parquet").to_pandas()
    assert result["status"] == "ok"
    assert saved.columns.tolist() == target.OUTPUT_COLUMNS


def test_old_feature_version_is_rebuilt(tmp_path: Path):
    input_path = tmp_path / "sz000001.parquet"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    pq.write_table(
        pa.Table.from_pandas(make_day("2025-05-06"), preserve_index=False),
        input_path,
    )
    old = target.build_daily_frame(make_day("2025-05-06"), "sz000001")
    old["may_feature_version"] = target.CURRENT_FEATURE_VERSION - 1
    pq.write_table(
        pa.Table.from_pandas(old, preserve_index=False),
        output_dir / "sz000001.parquet",
    )
    result = target.process_one_stock(
        input_path, output_dir, "2025-05-01", "2025-05-31"
    )
    saved = pq.read_table(output_dir / "sz000001.parquet").to_pandas()
    assert result["status"] == "ok"
    assert saved["may_feature_version"].eq(target.CURRENT_FEATURE_VERSION).all()


def test_default_output_directory_is_may_cache():
    assert target.DEFAULT_OUTPUT_DIR == (
        target.BASE_DIR / "may_factor_results" / "cache" / "may_daily_by_stock"
    )
