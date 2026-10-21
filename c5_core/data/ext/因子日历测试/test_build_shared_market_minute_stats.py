# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import build_shared_market_minute_stats as target


RETURNS_BY_TIME = {
    pd.Timestamp("2026-05-28 09:31:00"): np.array(
        [0.01, 0.02, 0.04, -0.01, -0.05], dtype=np.float64
    ),
    pd.Timestamp("2026-05-28 09:32:00"): np.array(
        [0.001, 0.002, 0.004, -0.001, -0.005], dtype=np.float64
    ),
    pd.Timestamp("2026-05-29 09:31:00"): np.array(
        [-0.02, -0.01, 0.01, 0.03, 0.06], dtype=np.float64
    ),
    pd.Timestamp("2026-05-29 09:32:00"): np.array(
        [-0.01, -0.005, 0.005, 0.015, 0.03], dtype=np.float64
    ),
}


def _write_stocks(directory: Path) -> list[Path]:
    directory.mkdir(parents=True)
    paths = []
    timestamps = list(RETURNS_BY_TIME)
    for stock_index in range(5):
        rows = []
        previous_close = np.nan
        previous_date = None
        for minute_index, timestamp in enumerate(timestamps):
            date = timestamp.normalize()
            is_first_minute = date != previous_date
            if is_first_minute:
                minute_open = 100.0 + stock_index + 50.0 * (
                    date.day - timestamps[0].day
                )
                base_price = minute_open
            else:
                # 故意给一个完全不同的开盘价，检查后续分钟只使用前收盘价。
                minute_open = 1.0 + stock_index
                base_price = previous_close
            minute_return = RETURNS_BY_TIME[timestamp][stock_index]
            minute_close = base_price * (1.0 + minute_return)
            rows.append(
                {
                    "datetime": timestamp,
                    "open": minute_open,
                    "close": minute_close,
                    "amount": float(100 * (stock_index + 1) + minute_index),
                }
            )
            previous_close = minute_close
            previous_date = date
        path = directory / f"sh60000{stock_index}.parquet"
        pd.DataFrame(rows).to_parquet(path, index=False)
        paths.append(path)
    return paths


def _adjusted_sample_skew(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=np.float64)
    count = len(values)
    centered = values - values.mean()
    moment2 = np.mean(centered**2)
    moment3 = np.mean(centered**3)
    return (
        np.sqrt(count * (count - 1))
        / (count - 2)
        * moment3
        / moment2**1.5
    )


def _expected_frame() -> pd.DataFrame:
    rows = []
    for minute_index, (timestamp, returns) in enumerate(
        RETURNS_BY_TIME.items()
    ):
        positive_mean = returns[returns > 0].mean()
        negative_mean = returns[returns < 0].mean()
        deviations = np.where(
            returns > 0,
            returns - positive_mean,
            np.where(returns < 0, returns - negative_mean, 0.0),
        )
        rows.append(
            {
                "date": timestamp.normalize(),
                "datetime": timestamp,
                "minute_of_day": timestamp.hour * 60 + timestamp.minute,
                "market_day_index": 0 if timestamp.day == 28 else 1,
                "market_amount_total": sum(
                    100 * (stock_index + 1) + minute_index
                    for stock_index in range(5)
                ),
                "market_mean_return": returns.mean(),
                "market_std_return_samp": returns.std(ddof=1),
                "market_std_return_pop": returns.std(ddof=0),
                "positive_return_mean": positive_mean,
                "negative_return_mean": negative_mean,
                "return_stock_count": len(returns),
                "amount_stock_count": len(returns),
                "deviation_skew": _adjusted_sample_skew(deviations),
            }
        )
    expected = pd.DataFrame(rows)
    expected["daily_market_std_samp_mean"] = expected.groupby("date")[
        "market_std_return_samp"
    ].transform("mean")
    expected["daily_market_std_pop_mean"] = expected.groupby("date")[
        "market_std_return_pop"
    ].transform("mean")
    expected["is_non_differentiation_samp"] = (
        expected["market_std_return_samp"]
        < expected["daily_market_std_samp_mean"]
    )
    expected["is_non_differentiation_pop"] = (
        expected["market_std_return_pop"]
        < expected["daily_market_std_pop_mean"]
    )
    return expected


def test_shared_market_formulas_match_independent_calculation(
    tmp_path: Path,
) -> None:
    paths = _write_stocks(tmp_path / "minute")
    connection = duckdb.connect()
    try:
        actual = connection.execute(
            target.build_query(paths, "2026-05-28", "2026-05-29")
        ).fetchdf()
    finally:
        connection.close()
    expected = _expected_frame()

    assert actual.columns.tolist() == list(target.OUTPUT_COLUMNS)
    pd.testing.assert_series_equal(
        pd.to_datetime(actual["datetime"]),
        expected["datetime"],
        check_names=False,
    )
    np.testing.assert_array_equal(
        actual[
            [
                "minute_of_day",
                "market_day_index",
                "return_stock_count",
                "amount_stock_count",
            ]
        ].to_numpy(),
        expected[
            [
                "minute_of_day",
                "market_day_index",
                "return_stock_count",
                "amount_stock_count",
            ]
        ].to_numpy(),
    )
    numeric_columns = [
        "market_amount_total",
        "market_mean_return",
        "market_std_return_samp",
        "market_std_return_pop",
        "daily_market_std_samp_mean",
        "daily_market_std_pop_mean",
        "positive_return_mean",
        "negative_return_mean",
        "deviation_skew",
    ]
    np.testing.assert_allclose(
        actual[numeric_columns].to_numpy(dtype=float),
        expected[numeric_columns].to_numpy(dtype=float),
        rtol=1e-12,
        atol=1e-14,
    )
    np.testing.assert_array_equal(
        actual[
            [
                "is_non_differentiation_samp",
                "is_non_differentiation_pop",
            ]
        ].to_numpy(dtype=bool),
        expected[
            [
                "is_non_differentiation_samp",
                "is_non_differentiation_pop",
            ]
        ].to_numpy(dtype=bool),
    )

    # 每天09:31均按 close/open-1，09:32均按 close/前一分钟close-1。
    np.testing.assert_allclose(
        actual["market_mean_return"].to_numpy(float),
        [returns.mean() for returns in RETURNS_BY_TIME.values()],
        rtol=1e-12,
        atol=1e-14,
    )


def test_main_writes_meta_and_reuses_verified_file(
    tmp_path: Path,
    capsys,
) -> None:
    minute_dir = tmp_path / "minute"
    _write_stocks(minute_dir)
    output = tmp_path / "shared_market.parquet"
    meta = tmp_path / "shared_market.meta.json"
    arguments = [
        "--source-glob",
        str(minute_dir / "*.parquet"),
        "--output",
        str(output),
        "--meta",
        str(meta),
        "--start",
        "2026-05-28",
        "--end",
        "2026-05-29",
        "--threads",
        "2",
        "--memory-limit",
        "256MB",
        "--temp-dir",
        str(tmp_path / "duckdb_temp"),
        "--row-group-size",
        "2",
    ]

    assert target.main(arguments) == 0
    first_output_mtime = output.stat().st_mtime_ns
    first_meta_mtime = meta.stat().st_mtime_ns
    metadata = json.loads(meta.read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"
    assert metadata["signature"]["source"]["file_count"] == 5
    assert metadata["signature"]["columns"] == list(target.OUTPUT_COLUMNS)
    assert metadata["output"]["rows"] == 4
    assert metadata["output"]["trading_days"] == 2
    assert metadata["output"]["sha256"] == target._sha256(output)

    capsys.readouterr()
    assert target.main(arguments) == 0
    second_output = capsys.readouterr().out
    assert "跳过生成" in second_output
    assert output.stat().st_mtime_ns == first_output_mtime
    assert meta.stat().st_mtime_ns == first_meta_mtime
    assert not list(tmp_path.glob("*.tmp.parquet"))


def test_yearly_parts_resume_only_missing_year(tmp_path: Path) -> None:
    minute_dir = tmp_path / "cross_year_minute"
    minute_dir.mkdir()
    timestamps = pd.to_datetime(
        [
            "2024-12-31 09:31:00",
            "2024-12-31 09:32:00",
            "2025-01-02 09:31:00",
            "2025-01-02 09:32:00",
        ]
    )
    for stock_index in range(3):
        returns = np.array([0.001, 0.002, -0.001, 0.003]) * (
            stock_index + 1
        )
        rows = []
        previous_date = None
        previous_close = np.nan
        for minute_index, timestamp in enumerate(timestamps):
            date = timestamp.normalize()
            if date != previous_date:
                minute_open = 10.0 + stock_index
                base = minute_open
            else:
                minute_open = previous_close
                base = previous_close
            minute_close = base * (1.0 + returns[minute_index])
            rows.append(
                {
                    "datetime": timestamp,
                    "open": minute_open,
                    "close": minute_close,
                    "amount": 1000.0 + stock_index * 10 + minute_index,
                }
            )
            previous_date = date
            previous_close = minute_close
        pd.DataFrame(rows).to_parquet(
            minute_dir / f"sh60000{stock_index}.parquet", index=False
        )

    yearly_dir = tmp_path / "yearly"
    arguments = [
        "--source-glob",
        str(minute_dir / "*.parquet"),
        "--yearly-dir",
        str(yearly_dir),
        "--start",
        "2024-12-31",
        "--end",
        "2025-01-02",
        "--threads",
        "2",
        "--memory-limit",
        "256MB",
        "--temp-dir",
        str(tmp_path / "temp"),
    ]
    assert target.main(arguments) == 0

    part_2024 = yearly_dir / "shared_market_minute_stats_2024.parquet"
    part_2025 = yearly_dir / "shared_market_minute_stats_2025.parquet"
    meta_2025 = part_2025.with_suffix(".meta.json")
    first_part_mtime = part_2024.stat().st_mtime_ns
    manifest_path = yearly_dir / "shared_market_minute_stats.yearly.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert [part["market_day_offset"] for part in manifest["parts"]] == [0, 1]

    with duckdb.connect() as connection:
        combined = connection.execute(
            f"SELECT * FROM {target.market_read_sql(yearly_dir)} "
            "ORDER BY datetime"
        ).fetchdf()
    assert combined["market_day_index"].tolist() == [0, 0, 1, 1]

    part_2025.unlink()
    meta_2025.unlink()
    assert target.main(arguments) == 0
    assert part_2024.stat().st_mtime_ns == first_part_mtime
    resumed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [part["run_status"] for part in resumed["parts"]] == [
        "reused",
        "completed",
    ]


def test_streaming_scan_matches_original_duckdb_query_value_by_value(
    tmp_path: Path,
) -> None:
    minute_dir = tmp_path / "minute_streaming_compare"
    paths = _write_stocks(minute_dir)
    partial = tmp_path / "partial.parquet"
    target._streaming_partial_worker(
        (
            [str(path) for path in paths],
            "2026-05-28",
            "2026-05-29",
            str(partial),
        )
    )
    streaming = target._merge_streaming_partials(
        [partial], "2026-05-28", "2026-05-29"
    )
    with duckdb.connect() as connection:
        original = connection.execute(
            target.build_query(paths, "2026-05-28", "2026-05-29")
        ).fetchdf()

    assert streaming.columns.tolist() == original.columns.tolist()
    pd.testing.assert_series_equal(
        pd.to_datetime(streaming["datetime"]),
        pd.to_datetime(original["datetime"]),
        check_names=False,
    )
    for column in target.OUTPUT_COLUMNS:
        if pd.api.types.is_numeric_dtype(original[column]):
            np.testing.assert_allclose(
                streaming[column].to_numpy(dtype=float),
                original[column].to_numpy(dtype=float),
                rtol=1e-12,
                atol=1e-14,
                equal_nan=True,
            )
        else:
            pd.testing.assert_series_equal(
                streaming[column].reset_index(drop=True),
                original[column].reset_index(drop=True),
                check_names=False,
                check_dtype=False,
            )
