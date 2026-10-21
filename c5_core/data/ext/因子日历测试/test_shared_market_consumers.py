# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import build_july_august_minute_features as july_builder
import build_market_minute_return_stats as legacy_early_market
import build_shared_market_minute_stats as shared_market
import build_trial_minute_features as trial_builder
import run_early_four_minute_backtest as early_runner


def _write_minute_files(directory: Path) -> list[Path]:
    directory.mkdir(parents=True)
    timestamps = pd.date_range("2025-07-01 09:31", periods=8, freq="min")
    base_returns = np.array(
        [0.001, 0.003, -0.002, 0.006, -0.004, 0.002, -0.001, 0.004]
    )
    paths = []
    for stock_index, code in enumerate(
        ["sh600000", "sh600001", "sh600002", "sz000001", "sz000002"]
    ):
        scale = np.array([0.5, 1.0, 1.7, -0.8, -1.4])[stock_index]
        returns = base_returns * scale
        first_open = 10.0 + stock_index
        close = first_open * np.cumprod(1.0 + returns)
        previous = np.r_[first_open, close[:-1]]
        volume = 1000.0 + 100.0 * stock_index + np.arange(len(timestamps))
        path = directory / f"{code}.parquet"
        pd.DataFrame(
            {
                "datetime": timestamps,
                "open": previous,
                "high": np.maximum(previous, close) + 0.01,
                "low": np.minimum(previous, close) - 0.01,
                "close": close,
                "volume": volume,
                "amount": volume * close,
            }
        ).to_parquet(path, index=False)
        paths.append(path)
    return paths


def _write_shared_file(paths: list[Path], output: Path) -> pd.DataFrame:
    with duckdb.connect() as connection:
        query = shared_market.build_query(paths, "2025-07-01", "2025-07-01")
        connection.execute(
            f"COPY ({query}) TO '{output.as_posix()}' (FORMAT PARQUET)"
        )
        return connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY datetime", [str(output)]
        ).fetchdf()


def test_three_consumers_share_one_market_file_without_value_changes(
    tmp_path: Path,
) -> None:
    paths = _write_minute_files(tmp_path / "minute")
    shared_dir = tmp_path / "shared_parts"
    shared_dir.mkdir()
    shared_path = shared_dir / "shared_market_minute_stats_2025.parquet"
    shared = _write_shared_file(paths, shared_path)

    assert july_builder.DEFAULT_MARKET_FILE == shared_market.DEFAULT_OUTPUT
    assert early_runner.DEFAULT_MARKET_STATS == shared_market.DEFAULT_OUTPUT
    assert trial_builder.DEFAULT_MARKET_FILE == shared_market.DEFAULT_OUTPUT

    # MAY25过去读取的样本标准差，与共用表的样本标准差逐分钟一致。
    with duckdb.connect() as connection:
        legacy_early = connection.execute(
            legacy_early_market.build_query(
                str(tmp_path / "minute" / "*.parquet"),
                "2025-07-01",
                "2025-07-01",
            )
        ).fetchdf()
    np.testing.assert_allclose(
        shared["market_mean_return"],
        legacy_early["market_mean_return"],
        rtol=1e-12,
        atol=1e-14,
    )
    np.testing.assert_allclose(
        shared["market_std_return_samp"],
        legacy_early["market_std_return"],
        rtol=1e-12,
        atol=1e-14,
    )
    early_input = shared[
        [
            "date",
            "minute_of_day",
            "market_mean_return",
            "market_std_return_samp",
        ]
    ]
    _, early_std = early_runner.market_stats_to_arrays(
        early_input,
        pd.DatetimeIndex([pd.Timestamp("2025-07-01")]),
    )
    np.testing.assert_allclose(
        early_std[0, shared["minute_of_day"].to_numpy(dtype=int)],
        shared["market_std_return_samp"],
        rtol=1e-7,
        atol=1e-9,
    )

    # 七八月股票查询只使用共用表中同名的成交额和交易日编号，日值保持不变。
    legacy_july_path = tmp_path / "legacy_july_market.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            f"""
            COPY (
                SELECT
                    date,
                    datetime,
                    market_day_index,
                    market_amount_total,
                    market_mean_return,
                    market_std_return_pop AS market_std_return,
                    market_std_return_pop AS market_return_std,
                    daily_market_std_pop_mean AS daily_market_return_std_mean,
                    return_stock_count,
                    amount_stock_count,
                    is_non_differentiation_pop AS is_non_differentiation
                FROM read_parquet('{shared_path.as_posix()}')
            ) TO '{legacy_july_path.as_posix()}' (FORMAT PARQUET)
            """
        )
        shared_daily = connection.execute(
            july_builder.build_stock_daily_query(
                paths[0], shared_dir, "2025-07-01", "2025-07-01"
            )
        ).fetchdf()
        legacy_daily = connection.execute(
            july_builder.build_stock_daily_query(
                paths[0], legacy_july_path, "2025-07-01", "2025-07-01"
            )
        ).fetchdf()
    pd.testing.assert_frame_equal(
        shared_daily,
        legacy_daily,
        check_dtype=False,
        rtol=1e-12,
        atol=1e-14,
    )

    # F02直接使用总体标准差对应的不分化分钟。
    with duckdb.connect() as connection:
        lone_goose_rows = connection.execute(
            july_builder.build_lone_goose_query(
                paths, shared_dir, "2025-07-01", "2025-07-01"
            )
        ).fetchdf()
    selected_times = set(
        pd.to_datetime(
            shared.loc[
                shared["is_non_differentiation_pop"], "datetime"
            ]
        )
    )
    assert selected_times
    assert lone_goose_rows["selected_minute_count"].eq(
        len(selected_times)
    ).all()
    source_amount = pd.read_parquet(paths[0], columns=["datetime", "amount"])
    expected_amount = source_amount.loc[
        source_amount["datetime"].isin(selected_times), "amount"
    ].to_numpy()
    actual_amount = lone_goose_rows.loc[
        lone_goose_rows["code"] == paths[0].stem, "amount_sequence"
    ].iloc[0]
    np.testing.assert_allclose(actual_amount, expected_amount)

    # 九项试算读取总体标准差、方向均值和偏度，均来自同一文件。
    with duckdb.connect() as connection:
        trial_rows = connection.execute(
            trial_builder.build_stock_query(
                paths[0], shared_dir, "2025-07-01", "2025-07-01"
            )
        ).fetchdf()
    joined = trial_rows.merge(
        shared[
            [
                "datetime",
                "market_mean_return",
                "market_std_return_pop",
                "positive_return_mean",
                "negative_return_mean",
                "return_stock_count",
                "deviation_skew",
            ]
        ],
        on="datetime",
        suffixes=("_consumer", "_shared"),
        validate="one_to_one",
    )
    for column in [
        "market_mean_return",
        "market_std_return_pop",
        "positive_return_mean",
        "negative_return_mean",
        "return_stock_count",
        "deviation_skew",
    ]:
        np.testing.assert_allclose(
            joined[f"{column}_consumer"].to_numpy(dtype=float),
            joined[f"{column}_shared"].to_numpy(dtype=float),
            rtol=1e-12,
            atol=1e-14,
        )
