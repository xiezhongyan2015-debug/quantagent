# -*- coding: utf-8 -*-

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

import build_minute_daily_extra as full_target
import build_minute_daily_extra_incremental_by_stock as target
import duckdb_factor_store as factor_store


SAMPLE_CODE = "sh600000"
SAMPLE_MINUTE = factor_store.DEFAULT_MINUTE_DIR / f"{SAMPLE_CODE}.parquet"
SAMPLE_V2 = target.DEFAULT_V2_DIR / f"{SAMPLE_CODE}.parquet"
START = "2015-01-01"
END = "2015-02-28"


def _require_sample() -> None:
    if not SAMPLE_MINUTE.is_file() or not SAMPLE_V2.is_file():
        pytest.skip("本机没有分钟样本或 v2 样本。")


def test_column_lists_match_the_current_v3_shape():
    assert len(target.V2_COLUMNS) == 22
    assert len(target.NEW_COLUMNS) == 23
    assert len(target.EXPECTED_COLUMNS) == 45
    assert len(set(target.EXPECTED_COLUMNS)) == 45
    assert set(target.V2_COLUMNS) | set(target.NEW_COLUMNS) == set(
        target.EXPECTED_COLUMNS
    )


def test_incremental_result_matches_full_query_and_skip_check(tmp_path: Path):
    _require_sample()
    output = tmp_path / f"{SAMPLE_CODE}.parquet"
    temp_dir = tmp_path / "duckdb_temp"

    if target._WORKER_CONNECTION is not None:
        target._WORKER_CONNECTION.close()
        target._WORKER_CONNECTION = None
    target._init_worker(str(temp_dir), "750MB")
    try:
        summary = target._build_one(
            str(SAMPLE_MINUTE),
            str(SAMPLE_V2),
            str(output),
            "2025-12-31",
            START,
            END,
        )
    finally:
        assert target._WORKER_CONNECTION is not None
        target._WORKER_CONNECTION.close()
        target._WORKER_CONNECTION = None

    actual = pd.read_parquet(output).sort_values(["date", "code"])
    connection = duckdb.connect()
    try:
        expected = connection.execute(
            full_target.build_query(str(SAMPLE_MINUTE), START, END)
        ).fetchdf()
        assert target._is_complete(
            connection,
            output,
            SAMPLE_MINUTE,
            SAMPLE_V2,
            START,
            END,
        )

        meta_path = target._meta_path(output)
        meta = json.loads(meta_path.read_text("utf-8"))
        meta["end"] = "2015-03-01"
        meta_path.write_text(json.dumps(meta), "utf-8")
        assert not target._is_complete(
            connection,
            output,
            SAMPLE_MINUTE,
            SAMPLE_V2,
            START,
            END,
        )
    finally:
        connection.close()

    expected = expected.sort_values(["date", "code"])
    assert summary["rows"] == len(expected) == len(actual)
    assert actual.columns.tolist() == target.EXPECTED_COLUMNS
    assert expected.columns.tolist() == target.EXPECTED_COLUMNS
    assert actual["code"].reset_index(drop=True).equals(
        expected["code"].reset_index(drop=True)
    )
    pd.testing.assert_series_equal(
        pd.to_datetime(actual["date"]).reset_index(drop=True),
        pd.to_datetime(expected["date"]).reset_index(drop=True),
        check_names=False,
        check_dtype=False,
    )
    np.testing.assert_allclose(
        actual[target.EXPECTED_COLUMNS[2:]].to_numpy(dtype=float),
        expected[target.EXPECTED_COLUMNS[2:]].to_numpy(dtype=float),
        rtol=1e-12,
        atol=1e-12,
        equal_nan=True,
    )
