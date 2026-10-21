# -*- coding: utf-8 -*-
"""核对 DuckDB 与原有 Pandas 读取方式得到的数据是否一致。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

import duckdb_factor_store as factor_store
import run_february_march_backtest as pandas_loader
from run_july_august_backtest import BASE_COLUMNS, MAY_COLUMNS


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = (
    BASE_DIR
    / "outputs"
    / "july_august_duckdb"
    / "duckdb_pandas_consistency.json"
)


def _files(source_name: str) -> list[Path]:
    pattern = factor_store.DEFAULT_SOURCES[source_name]
    return sorted(pattern.parent.glob(pattern.name))


def _normalise(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["code"] = panel["code"].astype("string").str.lower()
    for column in [*BASE_COLUMNS, *MAY_COLUMNS]:
        if column != "vwap_outside_range_1pct":
            panel[column] = pd.to_numeric(panel[column], errors="coerce").astype(
                np.float32
            )
    return panel.sort_values(["date", "code"]).reset_index(drop=True)


def main() -> int:
    start = "2020-01-01"
    end = "2020-12-31"

    pandas_started = time.perf_counter()
    base = pandas_loader.load_panel(
        _files("base_daily"), start, end, ["date", "code", *BASE_COLUMNS]
    )
    may = pandas_loader.load_panel(
        _files("may_daily"), start, end, ["date", "code", *MAY_COLUMNS]
    )
    pandas_panel = base.merge(
        may, on=["date", "code"], how="inner", validate="one_to_one"
    )
    pandas_panel = _normalise(pandas_panel)
    pandas_seconds = time.perf_counter() - pandas_started

    duckdb_started = time.perf_counter()
    settings = factor_store.DuckDBSettings(
        database_path=factor_store.DEFAULT_DATABASE,
        temp_dir=factor_store.DEFAULT_TEMP_DIR,
        threads=8,
        memory_limit="8GB",
    )
    with factor_store.connect(settings) as connection:
        duckdb_panel = factor_store.read_joined_panel(
            connection,
            {"base_daily": BASE_COLUMNS, "may_daily": MAY_COLUMNS},
            start,
            end,
        )
    duckdb_panel = _normalise(duckdb_panel)
    duckdb_seconds = time.perf_counter() - duckdb_started

    key_columns = ["date", "code"]
    keys_equal = pandas_panel[key_columns].equals(duckdb_panel[key_columns])
    row_count_equal = len(pandas_panel) == len(duckdb_panel)

    column_checks: dict[str, dict[str, object]] = {}
    all_values_equal = True
    for column in [*BASE_COLUMNS, *MAY_COLUMNS]:
        left = pandas_panel[column]
        right = duckdb_panel[column]
        if column == "vwap_outside_range_1pct":
            equal = left.astype("boolean").equals(right.astype("boolean"))
            max_abs_diff = None
        else:
            left_values = left.to_numpy(dtype=np.float32, na_value=np.nan)
            right_values = right.to_numpy(dtype=np.float32, na_value=np.nan)
            equal = bool(
                np.array_equal(
                    left_values,
                    right_values,
                    equal_nan=True,
                )
            )
            finite = np.isfinite(left_values) & np.isfinite(right_values)
            max_abs_diff = (
                float(np.max(np.abs(left_values[finite] - right_values[finite])))
                if finite.any()
                else 0.0
            )
        column_checks[column] = {
            "equal_after_float32": equal,
            "max_abs_diff": max_abs_diff,
        }
        all_values_equal = all_values_equal and equal

    passed = bool(row_count_equal and keys_equal and all_values_equal)
    result = {
        "period": [start, end],
        "passed": passed,
        "rows": len(pandas_panel),
        "columns": len(pandas_panel.columns),
        "pandas_seconds": round(pandas_seconds, 3),
        "duckdb_seconds": round(duckdb_seconds, 3),
        "speedup": round(pandas_seconds / duckdb_seconds, 2),
        "row_count_equal": row_count_equal,
        "keys_equal": keys_equal,
        "all_values_equal": all_values_equal,
        "duplicate_keys_pandas": int(
            pandas_panel.duplicated(key_columns, keep=False).sum()
        ),
        "duplicate_keys_duckdb": int(
            duckdb_panel.duplicated(key_columns, keep=False).sum()
        ),
        "column_checks": column_checks,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not passed:
        raise AssertionError("DuckDB 与 Pandas 的读取结果不一致，请查看检查文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
