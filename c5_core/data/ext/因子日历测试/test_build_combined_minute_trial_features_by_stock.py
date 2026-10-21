# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

import build_combined_minute_trial_features_by_stock as target
import build_july_august_minute_features as july_builder
import build_october_december_trial_daily as octdec_builder
import build_trial_minute_features as early_builder


def test_resume_requires_the_same_inputs_and_dates() -> None:
    signature = {
        "combined_cache_version": target.CACHE_VERSION,
        "start": "2015-01-01",
        "end": "2026-05-29",
        "source_fingerprint": "same-files",
    }
    assert target._resume_manifest_matches(
        {**signature, "status": "running"}, signature
    )
    assert not target._resume_manifest_matches(
        {**signature, "end": "2025-12-31", "status": "completed"}, signature
    )
    assert not target._resume_manifest_matches(None, signature)


def _sample_files(tmp_path: Path) -> tuple[Path, Path]:
    morning = pd.date_range("2000-01-01 09:31", periods=120, freq="min").time
    afternoon = pd.date_range("2000-01-01 13:01", periods=120, freq="min").time
    dates = pd.bdate_range("2026-01-05", periods=25)
    datetimes = pd.DatetimeIndex(
        [
            pd.Timestamp.combine(day.date(), minute)
            for day in dates
            for minute in [*morning, *afternoon]
        ]
    )
    position = np.tile(np.arange(240, dtype=np.float64), len(dates))
    day_number = np.repeat(np.arange(len(dates), dtype=np.float64), 240)
    close = 10.0 + day_number * 0.02 + position * 0.0005
    minute = pd.DataFrame(
        {
            "datetime": datetimes,
            "open": close - 0.001,
            "high": close + 0.003,
            "low": close - 0.004,
            "close": close,
            "volume": 1000.0 + (position % 37) * 13.0 + day_number,
            "amount": (1000.0 + (position % 37) * 13.0) * close,
        }
    )
    minute_path = tmp_path / "sh600000.parquet"
    minute.to_parquet(minute_path, index=False)

    market = pd.DataFrame(
        {
            "datetime": datetimes,
            "market_amount_total": minute["amount"].to_numpy() * 3.0,
            "market_day_index": np.repeat(np.arange(len(dates)), 240),
            "market_mean_return": np.sin(position / 20.0) * 0.0001,
            "market_std_return_pop": np.full(len(minute), 0.001),
            "positive_return_mean": np.full(len(minute), 0.0002),
            "negative_return_mean": np.full(len(minute), -0.0002),
            "return_stock_count": np.full(len(minute), 3),
            "deviation_skew": np.full(len(minute), 0.5),
        }
    )
    market_path = tmp_path / "market.parquet"
    market.to_parquet(market_path, index=False)
    return minute_path, market_path


def _reference_frames(
    minute_path: Path, market_path: Path
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with duckdb.connect() as connection:
        july_raw = connection.execute(
            july_builder.build_stock_daily_query(
                minute_path, market_path, "2026-01-01", "2026-02-28"
            )
        ).fetchdf()
        early_raw = connection.execute(
            early_builder.build_stock_query(
                minute_path, market_path, "2026-01-01", "2026-02-28"
            )
        ).fetchdf()
        octdec_raw = connection.execute(
            octdec_builder.build_stock_query(
                minute_path, "2026-01-01", "2026-02-28"
            )
        ).fetchdf()
    return (
        july_builder.finalize_stock_daily_frame(july_raw),
        early_builder.build_daily_frame(early_raw, "sh600000", 2),
        octdec_builder.build_daily_frame(octdec_raw, "sh600000"),
    )


def test_shared_raw_table_keeps_all_three_results_exact(tmp_path: Path) -> None:
    minute_path, market_path = _sample_files(tmp_path)
    expected_july, expected_early, expected_octdec = _reference_frames(
        minute_path, market_path
    )
    materialize = target.build_materialize_query(
        minute_path, "2026-01-01", "2026-02-28"
    )
    assert materialize.lower().count("read_parquet") == 1

    with duckdb.connect() as connection:
        connection.execute(materialize)
        july_raw = connection.execute(
            july_builder.build_stock_daily_query(
                minute_path,
                market_path,
                "2026-01-01",
                "2026-02-28",
                source_relation="combined_raw",
                code="sh600000",
            )
        ).fetchdf()
        early_raw = connection.execute(
            early_builder.build_stock_query(
                minute_path,
                market_path,
                "2026-01-01",
                "2026-02-28",
                source_relation="combined_raw",
            )
        ).fetchdf()
        octdec_raw = connection.execute(
            octdec_builder.build_stock_query(
                minute_path,
                "2026-01-01",
                "2026-02-28",
                source_relation="combined_raw",
            )
        ).fetchdf()

    actual_july = july_builder.finalize_stock_daily_frame(july_raw)
    actual_early = early_builder.build_daily_frame(early_raw, "sh600000", 2)
    actual_octdec = octdec_builder.build_daily_frame(octdec_raw, "sh600000")
    assert_frame_equal(actual_july, expected_july, check_exact=True)
    assert_frame_equal(actual_early, expected_early, check_exact=True)
    assert_frame_equal(actual_octdec, expected_octdec, check_exact=True)


def test_one_stock_write_is_current_for_all_three_groups(tmp_path: Path) -> None:
    minute_path, market_path = _sample_files(tmp_path)
    july_dir = tmp_path / "july"
    early_dir = tmp_path / "early"
    octdec_dir = tmp_path / "octdec"
    temp_dir = tmp_path / "temp"
    for directory in [july_dir, early_dir, octdec_dir, temp_dir]:
        directory.mkdir()
    july_output = july_dir / minute_path.name
    early_output = early_dir / minute_path.name
    octdec_output = octdec_dir / minute_path.name

    try:
        target._init_worker(str(temp_dir), "1GB")
        result = target._build_one_stock(
            str(minute_path),
            str(market_path),
            str(july_output),
            str(early_output),
            str(octdec_output),
            "2026-01-01",
            "2026-02-28",
            2,
        )
    finally:
        if target._WORKER_CONNECTION is not None:
            target._WORKER_CONNECTION.close()
            target._WORKER_CONNECTION = None

    assert result["status"] == "completed"
    assert result["raw_reads"] == 1
    assert target.outputs_are_current(
        july_output, early_output, octdec_output, 2
    )
    expected = _reference_frames(minute_path, market_path)
    for output, reference in zip(
        [july_output, early_output, octdec_output], expected
    ):
        assert_frame_equal(
            pd.read_parquet(output), reference, check_exact=True
        )
