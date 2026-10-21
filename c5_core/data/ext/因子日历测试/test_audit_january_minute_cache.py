# -*- coding: utf-8 -*-
"""每日分钟缓存检查脚本的合成数据测试。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import audit_january_minute_cache as target


def make_rows(code: str, dates: list[str]) -> pd.DataFrame:
    rows = []
    for position, date in enumerate(dates):
        row = {name: float(position + 1) for name in target.REQUIRED_COLUMNS}
        row.update(
            {
                "date": pd.Timestamp(date),
                "code": code,
                "minute_count": np.int16(240),
                "vwap_outside_range_1pct": False,
                "peak_count": np.int32(position),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows, columns=target.REQUIRED_COLUMNS)


def write_parquet(frame: pd.DataFrame, path: Path) -> None:
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)


def test_audit_writes_four_reports_and_counts_quality_issues(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "reports"
    cache_dir.mkdir()
    first = make_rows("sh600000", ["2020-01-02", "2020-01-03"])
    first.loc[0, "rvjn_5m"] = np.nan
    second = make_rows("sz000001", ["2020-01-02", "2020-01-03"])
    second.loc[1, "minute_count"] = 239
    second.loc[1, "vwap_outside_range_1pct"] = True
    second = pd.concat([second, second.iloc[[0]]], ignore_index=True)
    write_parquet(first, cache_dir / "sh600000.parquet")
    write_parquet(second, cache_dir / "sz000001.parquet")

    result = target.main(
        [
            "--cache-dir",
            str(cache_dir),
            "--output-dir",
            str(output_dir),
            "--start",
            "2020-01-01",
            "--end",
            "2020-12-31",
        ]
    )

    assert result == 0
    expected = {
        target.SUMMARY_NAME,
        target.BY_YEAR_NAME,
        target.BAD_STOCKS_NAME,
        target.DAILY_COVERAGE_NAME,
    }
    assert {path.name for path in output_dir.iterdir()} == expected
    summary = json.loads((output_dir / target.SUMMARY_NAME).read_text(encoding="utf-8"))
    assert summary["file_count"] == 2
    assert summary["total_rows"] == 5
    assert summary["total_days"] == 2
    assert summary["total_stock_day_records"] == 4
    assert summary["schema_consistent"] is True
    assert summary["all_required_columns_present"] is True
    assert summary["minute_count_all_240"] is False
    assert summary["minute_count_not_240_rows"] == 1
    assert summary["duplicate_date_code_rows"] == 1
    assert summary["duplicate_date_code_pairs"] == 1
    assert summary["vwap_outside_range_1pct_stock_days"] == 1
    assert summary["factor_base_non_null_counts"]["rvjn_5m"] == 4

    coverage = pd.read_csv(output_dir / target.DAILY_COVERAGE_NAME)
    assert coverage["stock_count"].tolist() == [2, 2]
    assert coverage["duplicate_date_code_rows"].tolist() == [1, 0]
    bad = pd.read_csv(output_dir / target.BAD_STOCKS_NAME)
    assert bad["code"].tolist() == ["sz000001"]
    assert not list(output_dir.glob(".*.tmp"))


def test_limit_and_missing_required_column_are_reported(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "reports"
    cache_dir.mkdir()
    empty = make_rows("sh600000", []).iloc[:0]
    missing = make_rows("sz000001", ["2020-01-02"]).drop(columns="rvjn_5m")
    write_parquet(empty, cache_dir / "a_empty.parquet")
    write_parquet(missing, cache_dir / "b_missing.parquet")

    summary = target.audit_cache(
        cache_dir, output_dir, "2020-01-01", "2020-12-31", limit=1
    )

    assert summary["available_file_count"] == 2
    assert summary["file_count"] == 1
    assert summary["limited_scan"] is True
    assert summary["empty_file_count"] == 1

    summary = target.audit_cache(
        cache_dir, output_dir, "2020-01-01", "2020-12-31"
    )
    assert summary["all_required_columns_present"] is False
    bad = pd.read_csv(output_dir / target.BAD_STOCKS_NAME, keep_default_na=False)
    missing_row = bad[bad["file"] == "b_missing.parquet"].iloc[0]
    assert "rvjn_5m" in missing_row["missing_required_columns"]


def test_cross_file_date_code_duplicate_is_counted_once(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "reports"
    cache_dir.mkdir()
    write_parquet(make_rows("same", ["2020-01-02"]), cache_dir / "first.parquet")
    write_parquet(make_rows("same", ["2020-01-02"]), cache_dir / "second.parquet")

    summary = target.audit_cache(
        cache_dir, output_dir, "2020-01-01", "2020-12-31"
    )

    assert summary["total_rows"] == 2
    assert summary["total_stock_day_records"] == 1
    assert summary["duplicate_date_code_rows"] == 1
    assert summary["duplicate_date_code_pairs"] == 1


def test_only_columns_needed_for_checks_are_read(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "reports"
    cache_dir.mkdir()
    write_parquet(make_rows("sh600000", ["2020-01-02"]), cache_dir / "sh600000.parquet")
    original = target.pq.read_table
    calls = []

    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(target.pq, "read_table", counted)
    target.audit_cache(cache_dir, output_dir, "2020-01-01", "2020-12-31")

    assert len(calls) == 1
    assert calls[0]["use_threads"] is False
    assert set(calls[0]["columns"]) == set(target.READ_COLUMNS)
    assert "prev_close" not in calls[0]["columns"]
    assert "peak_count" not in calls[0]["columns"]
