# -*- coding: utf-8 -*-

import duckdb
import numpy as np
import pandas as pd
import pytest

import build_minute_daily_extra_incremental_by_stock as incremental
import build_smart_money_numpy as target
import duckdb_factor_store as factor_store


SAMPLE = factor_store.DEFAULT_MINUTE_DIR / "sh600000.parquet"
START = "2015-01-01"
END = "2015-02-28"


def test_numpy_smart_money_matches_duckdb_definition():
    if not SAMPLE.is_file():
        pytest.skip("本机没有分钟样本。")
    actual = target.compute_smart_money_numpy(SAMPLE, START, END)
    connection = duckdb.connect()
    try:
        connection.execute("SET threads = 1")
        connection.execute("SET preserve_insertion_order = false")
        expected = connection.execute(
            incremental.build_smart_query(str(SAMPLE), START, END)
        ).fetchdf()
    finally:
        connection.close()

    actual = actual.sort_values(["date", "code"]).reset_index(drop=True)
    expected = expected.sort_values(["date", "code"]).reset_index(drop=True)
    assert actual["code"].tolist() == expected["code"].tolist()
    pd.testing.assert_series_equal(
        pd.to_datetime(actual["date"]),
        pd.to_datetime(expected["date"]),
        check_names=False,
        check_dtype=False,
    )
    np.testing.assert_array_equal(
        actual["smart_money_vwap_ratio_10d"].to_numpy(float),
        expected["smart_money_vwap_ratio_10d"].to_numpy(float),
    )


def test_fewer_than_ten_days_returns_typed_empty_result():
    if not SAMPLE.is_file():
        pytest.skip("本机没有分钟样本。")
    result = target.compute_smart_money_numpy(
        SAMPLE, "2015-01-05", "2015-01-09"
    )
    assert result.empty
    assert result.columns.tolist() == [
        "code",
        "date",
        "smart_money_vwap_ratio_10d",
    ]
    assert str(result["smart_money_vwap_ratio_10d"].dtype) == "float64"
