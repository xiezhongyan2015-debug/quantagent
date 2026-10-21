# -*- coding: utf-8 -*-
"""逐只股票检查一月分钟因子每日缓存，并生成四份检查结果。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = (
    BASE_DIR / "january_original_minute_results" / "cache" / "daily_by_stock"
)
DEFAULT_OUTPUT_DIR = BASE_DIR / "january_original_minute_results"

REQUIRED_COLUMNS = [
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "float_shares",
    "total_shares",
    "qfq_ratio",
    "vwap_qfq",
    "prev_close",
    "minute_count",
    "vwap_outside_range_1pct",
    "turnvol_daily",
    "rvjn_5m",
    "illiq_shortcut_5m_daily",
    "weighted_close_ratio_5m",
    "volume_share_kurt_10m",
    "realized_skew_1m",
    "realized_skew_5m",
    "last_30min_return",
    "price_resiliency_1m_daily",
    "volume_peak_price_position_daily",
    "gu_1m",
    "gd_1m",
    "ridge_gap_count",
    "ridge_gap_sum1",
    "ridge_gap_sum2",
    "ridge_gap_sum3",
    "peak_count",
]

# 这些列会被正式回测直接读取，用它们统计非空记录数。
FACTOR_BASE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "amount",
    "volume",
    "float_shares",
    "total_shares",
    "qfq_ratio",
    "vwap_qfq",
    "turnvol_daily",
    "rvjn_5m",
    "illiq_shortcut_5m_daily",
    "weighted_close_ratio_5m",
    "volume_share_kurt_10m",
    "realized_skew_1m",
    "realized_skew_5m",
    "last_30min_return",
    "price_resiliency_1m_daily",
    "volume_peak_price_position_daily",
    "gu_1m",
    "gd_1m",
    "ridge_gap_count",
    "ridge_gap_sum1",
    "ridge_gap_sum2",
    "ridge_gap_sum3",
]

QUALITY_COLUMNS = [
    "date",
    "code",
    "minute_count",
    "vwap_outside_range_1pct",
]
READ_COLUMNS = list(dict.fromkeys([*QUALITY_COLUMNS, *FACTOR_BASE_COLUMNS]))

SUMMARY_NAME = "cache_audit_summary.json"
BY_YEAR_NAME = "cache_audit_by_year.csv"
BAD_STOCKS_NAME = "cache_audit_bad_stocks.csv"
DAILY_COVERAGE_NAME = "cache_audit_daily_coverage.csv"

BAD_STOCK_COLUMNS = [
    "file",
    "code",
    "file_size_bytes",
    "rows",
    "date_start",
    "date_end",
    "empty_file",
    "schema_fingerprint",
    "schema_differs_from_most_common",
    "missing_required_columns",
    "unexpected_columns",
    "wrong_required_column_order",
    "invalid_date_rows",
    "out_of_range_rows",
    "missing_code_rows",
    "code_mismatch_rows",
    "distinct_codes",
    "duplicate_date_code_rows",
    "duplicate_date_code_pairs",
    "minute_count_missing_rows",
    "minute_count_not_240_rows",
    "vwap_outside_range_1pct_days",
    "vwap_outside_range_1pct_ratio",
    "all_null_factor_columns",
    "read_error",
    "issues",
]

DAILY_COVERAGE_COLUMNS = [
    "date",
    "year",
    "stock_count",
    "row_count",
    "duplicate_date_code_rows",
    "minute_count_bad_rows",
    "minute_count_bad_stocks",
    "vwap_outside_range_1pct_rows",
    "vwap_outside_range_1pct_stocks",
    "out_of_range_rows",
]

BY_YEAR_COLUMNS = [
    "year",
    "stock_count",
    "date_count",
    "stock_day_records",
    "row_count",
    "duplicate_date_code_rows",
    "minute_count_bad_rows",
    "minute_count_bad_stock_days",
    "vwap_outside_range_1pct_rows",
    "vwap_outside_range_1pct_stock_days",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--limit", type=int)
    return parser.parse_args(argv)


def list_cache_files(cache_dir: Path) -> list[Path]:
    """只在开始时取一次文件清单，避免扫描期间把新文件混进来。"""
    return sorted(cache_dir.glob("*.parquet"), key=lambda path: path.name.lower())


def _temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        frame.to_csv(temporary, index=False, encoding="utf-8-sig")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def schema_description(schema) -> str:
    schema = schema.remove_metadata()
    return "\n".join(
        f"{field.name}|{field.type}|nullable={field.nullable}" for field in schema
    )


def schema_fingerprint(schema) -> tuple[str, str]:
    description = schema_description(schema)
    digest = hashlib.sha256(description.encode("utf-8")).hexdigest()[:16]
    return digest, description


def normalize_dates(values: pd.Series) -> pd.Series:
    dates = pd.to_datetime(values, errors="coerce")
    if isinstance(dates.dtype, pd.DatetimeTZDtype):
        dates = dates.dt.tz_localize(None)
    return dates.dt.normalize()


def normalize_codes(values: pd.Series) -> pd.Series:
    return values.astype("string").str.strip().str.lower()


def parse_flags(values: pd.Series) -> pd.Series:
    """兼容布尔、0/1 和常见文本写法；空值按没有异常处理。"""
    if pd.api.types.is_bool_dtype(values.dtype):
        return values.fillna(False).astype(bool)
    if pd.api.types.is_numeric_dtype(values.dtype):
        numeric = pd.to_numeric(values, errors="coerce")
        return numeric.fillna(0).ne(0)
    return (
        values.astype("string")
        .str.strip()
        .str.lower()
        .isin(["1", "true", "yes", "y", "bad"])
        .fillna(False)
    )


def non_empty_count(column) -> int:
    """同时把 Parquet 空值和浮点 NaN 视为缺值。"""
    return int(pd.notna(column.to_pandas()).sum())


def make_quality_frame(table, row_count: int) -> pd.DataFrame:
    data: dict[str, pd.Series] = {}
    names = set(table.column_names)
    if "date" in names:
        data["date"] = normalize_dates(pd.Series(table.column("date").to_pandas()))
    else:
        data["date"] = pd.Series(pd.NaT, index=range(row_count), dtype="datetime64[ns]")
    if "code" in names:
        data["code"] = normalize_codes(pd.Series(table.column("code").to_pandas()))
    else:
        data["code"] = pd.Series(pd.NA, index=range(row_count), dtype="string")
    if "minute_count" in names:
        data["minute_count"] = pd.to_numeric(
            pd.Series(table.column("minute_count").to_pandas()), errors="coerce"
        )
    else:
        data["minute_count"] = pd.Series(np.nan, index=range(row_count))
    if "vwap_outside_range_1pct" in names:
        raw_flags = pd.Series(table.column("vwap_outside_range_1pct").to_pandas())
        data["vwap_bad"] = parse_flags(raw_flags)
    else:
        data["vwap_bad"] = pd.Series(False, index=range(row_count))
    return pd.DataFrame(data)


def group_pairs(frame: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    valid = frame["date"].notna() & frame["code"].notna() & frame["code"].ne("")
    subset = frame.loc[valid, ["date", "code", "vwap_bad"]].copy()
    if subset.empty:
        return pd.DataFrame(columns=["date", "code", "vwap_bad", "minute_bad"]), 0, 0
    subset["date"] = subset["date"].dt.strftime("%Y-%m-%d")
    subset["minute_bad"] = frame.loc[valid, "minute_count"].isna() | frame.loc[
        valid, "minute_count"
    ].ne(240)
    sizes = subset.groupby(["date", "code"], sort=False).size()
    duplicate_rows = int((sizes - 1).clip(lower=0).sum())
    duplicate_pairs = int(sizes.gt(1).sum())
    pairs = (
        subset.groupby(["date", "code"], sort=False, as_index=False)
        .agg(vwap_bad=("vwap_bad", "any"), minute_bad=("minute_bad", "any"))
    )
    return pairs, duplicate_rows, duplicate_pairs


def read_pair_state(path: Path, code: str) -> dict[str, tuple[bool, bool]]:
    """仅在同一代码出现在多个文件时，重读首个文件来核对跨文件重复。"""
    parquet = pq.ParquetFile(path)
    names = set(parquet.schema_arrow.names)
    columns = [name for name in QUALITY_COLUMNS if name in names]
    table = pq.read_table(path, columns=columns, use_threads=False)
    frame = make_quality_frame(table, table.num_rows)
    pairs, _, _ = group_pairs(frame)
    pairs = pairs[pairs["code"] == code]
    return {
        row.date: (bool(row.vwap_bad), bool(row.minute_bad))
        for row in pairs.itertuples(index=False)
    }


def _number_distribution(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {
            "min": None,
            "p01": None,
            "p05": None,
            "p25": None,
            "median": None,
            "mean": None,
            "p75": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    numbers = np.asarray(values, dtype=np.float64)
    return {
        "min": int(numbers.min()),
        "p01": float(np.quantile(numbers, 0.01)),
        "p05": float(np.quantile(numbers, 0.05)),
        "p25": float(np.quantile(numbers, 0.25)),
        "median": float(np.median(numbers)),
        "mean": float(numbers.mean()),
        "p75": float(np.quantile(numbers, 0.75)),
        "p95": float(np.quantile(numbers, 0.95)),
        "p99": float(np.quantile(numbers, 0.99)),
        "max": int(numbers.max()),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def audit_cache(
    cache_dir: Path,
    output_dir: Path,
    start: str,
    end: str,
    limit: int | None = None,
) -> dict[str, object]:
    cache_dir = cache_dir.resolve()
    output_dir = output_dir.resolve()
    if not cache_dir.is_dir():
        raise FileNotFoundError(f"缓存目录不存在：{cache_dir}")
    if limit is not None and limit < 0:
        raise ValueError("--limit 不能小于0")
    lower = pd.Timestamp(start).normalize()
    upper = pd.Timestamp(end).normalize()
    if upper < lower:
        raise ValueError("--end 不能早于 --start")

    all_files = list_cache_files(cache_dir)
    files = all_files if limit is None else all_files[:limit]
    print(f"找到 {len(all_files)} 个文件，本次检查 {len(files)} 个。", flush=True)

    schema_counts: Counter[str] = Counter()
    schema_details: dict[str, dict[str, object]] = {}
    factor_non_null = Counter({name: 0 for name in FACTOR_BASE_COLUMNS})
    factor_file_counts = Counter({name: 0 for name in FACTOR_BASE_COLUMNS})
    daily: dict[str, Counter[str]] = defaultdict(Counter)
    year_stocks: dict[int, set[str]] = defaultdict(set)
    all_codes: set[str] = set()
    bad_code_dates: dict[str, dict[str, tuple[bool, bool]]] = {}
    first_code_file: dict[str, Path] = {}
    duplicate_pairs_seen: set[tuple[str, str]] = set()
    vwap_bad_dates: set[str] = set()
    vwap_bad_stocks: set[str] = set()
    file_rows: list[dict[str, object]] = []
    file_sizes: list[int] = []

    total_rows = 0
    metadata_rows = 0
    invalid_date_rows = 0
    out_of_range_rows = 0
    missing_code_rows = 0
    code_mismatch_rows = 0
    minute_missing_rows = 0
    minute_not_240_rows = 0
    duplicate_extra_rows = 0
    vwap_bad_rows = 0
    vwap_bad_stock_days = 0
    read_error_count = 0

    for position, path in enumerate(files, start=1):
        size = path.stat().st_size
        file_sizes.append(size)
        item: dict[str, object] = {
            "file": path.name,
            "code": path.stem.lower(),
            "file_size_bytes": size,
            "rows": 0,
            "date_start": "",
            "date_end": "",
            "empty_file": False,
            "schema_fingerprint": "",
            "schema_differs_from_most_common": False,
            "missing_required_columns": "",
            "unexpected_columns": "",
            "wrong_required_column_order": False,
            "invalid_date_rows": 0,
            "out_of_range_rows": 0,
            "missing_code_rows": 0,
            "code_mismatch_rows": 0,
            "distinct_codes": 0,
            "duplicate_date_code_rows": 0,
            "duplicate_date_code_pairs": 0,
            "minute_count_missing_rows": 0,
            "minute_count_not_240_rows": 0,
            "vwap_outside_range_1pct_days": 0,
            "vwap_outside_range_1pct_ratio": 0.0,
            "all_null_factor_columns": "",
            "read_error": "",
            "issues": "",
        }
        issues: list[str] = []
        try:
            parquet = pq.ParquetFile(path)
            schema = parquet.schema_arrow
            metadata_count = int(parquet.metadata.num_rows)
            metadata_rows += metadata_count
            item["rows"] = metadata_count
            item["empty_file"] = metadata_count == 0
            if metadata_count == 0:
                issues.append("空文件")

            names = schema.names
            name_set = set(names)
            missing = [name for name in REQUIRED_COLUMNS if name not in name_set]
            unexpected = [name for name in names if name not in REQUIRED_COLUMNS]
            required_in_file = [name for name in names if name in REQUIRED_COLUMNS]
            wrong_order = required_in_file != [
                name for name in REQUIRED_COLUMNS if name in name_set
            ]
            item["missing_required_columns"] = ";".join(missing)
            item["unexpected_columns"] = ";".join(unexpected)
            item["wrong_required_column_order"] = wrong_order
            if missing:
                issues.append("缺少必需列")
            if unexpected:
                issues.append("有额外列")
            if wrong_order:
                issues.append("必需列顺序不同")

            fingerprint, description = schema_fingerprint(schema)
            item["schema_fingerprint"] = fingerprint
            schema_counts[fingerprint] += 1
            if fingerprint not in schema_details:
                schema_details[fingerprint] = {
                    "fingerprint": fingerprint,
                    "file_count": 0,
                    "example_file": path.name,
                    "description": description,
                }
            schema_details[fingerprint]["file_count"] = schema_counts[fingerprint]

            columns = [name for name in READ_COLUMNS if name in name_set]
            table = pq.read_table(path, columns=columns, use_threads=False)
            actual_rows = int(table.num_rows)
            total_rows += actual_rows
            item["rows"] = actual_rows
            if actual_rows != metadata_count:
                issues.append("表头行数与读取行数不同")

            all_null_factors: list[str] = []
            for name in FACTOR_BASE_COLUMNS:
                if name not in name_set:
                    all_null_factors.append(name)
                    continue
                factor_file_counts[name] += 1
                count = non_empty_count(table.column(name))
                factor_non_null[name] += count
                if actual_rows and count == 0:
                    all_null_factors.append(name)
            item["all_null_factor_columns"] = ";".join(all_null_factors)
            if actual_rows and all_null_factors:
                issues.append("因子基础列整列为空")

            frame = make_quality_frame(table, actual_rows)
            valid_dates = frame["date"].dropna()
            if not valid_dates.empty:
                item["date_start"] = valid_dates.min().strftime("%Y-%m-%d")
                item["date_end"] = valid_dates.max().strftime("%Y-%m-%d")
            invalid_count = int(frame["date"].isna().sum())
            outside = frame["date"].notna() & (
                (frame["date"] < lower) | (frame["date"] > upper)
            )
            outside_count = int(outside.sum())
            item["invalid_date_rows"] = invalid_count
            item["out_of_range_rows"] = outside_count
            invalid_date_rows += invalid_count
            out_of_range_rows += outside_count
            if invalid_count:
                issues.append("日期无效")
            if outside_count:
                issues.append("日期超出范围")

            valid_code = frame["code"].notna() & frame["code"].ne("")
            missing_codes = int((~valid_code).sum())
            mismatched = valid_code & frame["code"].ne(path.stem.lower())
            mismatch_count = int(mismatched.sum())
            codes = sorted(frame.loc[valid_code, "code"].unique().tolist())
            item["missing_code_rows"] = missing_codes
            item["code_mismatch_rows"] = mismatch_count
            item["distinct_codes"] = len(codes)
            missing_code_rows += missing_codes
            code_mismatch_rows += mismatch_count
            if missing_codes:
                issues.append("股票代码为空")
            if mismatch_count:
                issues.append("股票代码与文件名不同")

            minute_missing = int(frame["minute_count"].isna().sum())
            minute_wrong = int(
                (frame["minute_count"].notna() & frame["minute_count"].ne(240)).sum()
            )
            item["minute_count_missing_rows"] = minute_missing
            item["minute_count_not_240_rows"] = minute_wrong
            minute_missing_rows += minute_missing
            minute_not_240_rows += minute_wrong
            if minute_missing:
                issues.append("minute_count为空")
            if minute_wrong:
                issues.append("minute_count不等于240")

            vwap_count = int(frame["vwap_bad"].sum())
            vwap_bad_rows += vwap_count
            valid_pair_rows = int((frame["date"].notna() & valid_code).sum())
            item["vwap_outside_range_1pct_ratio"] = (
                vwap_count / valid_pair_rows if valid_pair_rows else 0.0
            )

            # 一次按日期汇总，避免对同一文件里的每个日期重复扫描整列。
            date_keys = frame["date"].dt.strftime("%Y-%m-%d")
            minute_bad = frame["minute_count"].isna() | frame["minute_count"].ne(240)
            daily_source = pd.DataFrame(
                {
                    "date": date_keys,
                    "minute_bad": minute_bad,
                    "vwap_bad": frame["vwap_bad"],
                    "outside": outside,
                }
            ).dropna(subset=["date"])
            daily_groups = daily_source.groupby("date", sort=False).agg(
                row_count=("date", "size"),
                minute_count_bad_rows=("minute_bad", "sum"),
                vwap_bad_rows=("vwap_bad", "sum"),
                out_of_range_rows=("outside", "sum"),
            )
            for date_key, values in daily_groups.iterrows():
                daily[date_key]["row_count"] += int(values["row_count"])
                daily[date_key]["minute_count_bad_rows"] += int(
                    values["minute_count_bad_rows"]
                )
                daily[date_key]["vwap_bad_rows"] += int(values["vwap_bad_rows"])
                daily[date_key]["out_of_range_rows"] += int(
                    values["out_of_range_rows"]
                )

            pairs, within_duplicate_rows, within_duplicate_pairs = group_pairs(frame)
            duplicate_extra_rows += within_duplicate_rows
            item["duplicate_date_code_rows"] = within_duplicate_rows
            item["duplicate_date_code_pairs"] = within_duplicate_pairs
            if within_duplicate_rows:
                issues.append("date/code重复")
            pair_sizes = (
                frame.loc[
                    frame["date"].notna() & valid_code, ["date", "code"]
                ]
                .assign(date=lambda value: value["date"].dt.strftime("%Y-%m-%d"))
                .groupby(["date", "code"], sort=False)
                .size()
            )
            for date_key, code in pair_sizes[pair_sizes.gt(1)].index:
                duplicate_pairs_seen.add((date_key, code))
                daily[date_key]["duplicate_date_code_rows"] += int(
                    pair_sizes.loc[(date_key, code)] - 1
                )

            file_vwap_days: set[tuple[str, str]] = set()
            cross_duplicates = 0
            cross_duplicate_pairs = 0
            for code, code_pairs in pairs.groupby("code", sort=False):
                code = str(code)
                owner = first_code_file.get(code)
                if owner is None:
                    first_code_file[code] = path
                    state = None
                else:
                    state = bad_code_dates.get(code)
                    if state is None:
                        state = read_pair_state(owner, code)
                        bad_code_dates[code] = state

                for pair in code_pairs.itertuples(index=False):
                    date_key = str(pair.date)
                    vwap_bad = bool(pair.vwap_bad)
                    minute_is_bad = bool(pair.minute_bad)
                    previous = state.get(date_key) if state is not None else None
                    if previous is not None:
                        cross_duplicates += 1
                        pair_key = (date_key, code)
                        if pair_key not in duplicate_pairs_seen:
                            cross_duplicate_pairs += 1
                            duplicate_pairs_seen.add(pair_key)
                        daily[date_key]["duplicate_date_code_rows"] += 1
                        old_vwap, old_minute = previous
                        if vwap_bad and not old_vwap:
                            daily[date_key]["vwap_bad_stock_count"] += 1
                            vwap_bad_stock_days += 1
                        if minute_is_bad and not old_minute:
                            daily[date_key]["minute_bad_stock_count"] += 1
                        state[date_key] = (old_vwap or vwap_bad, old_minute or minute_is_bad)
                    else:
                        daily[date_key]["stock_count"] += 1
                        if vwap_bad:
                            daily[date_key]["vwap_bad_stock_count"] += 1
                            vwap_bad_stock_days += 1
                        if minute_is_bad:
                            daily[date_key]["minute_bad_stock_count"] += 1
                        if state is not None:
                            state[date_key] = (vwap_bad, minute_is_bad)

                    year = int(date_key[:4])
                    year_stocks[year].add(code)
                    all_codes.add(code)
                    if vwap_bad:
                        file_vwap_days.add((date_key, code))
                        vwap_bad_dates.add(date_key)
                        vwap_bad_stocks.add(code)

            if cross_duplicates:
                duplicate_extra_rows += cross_duplicates
                item["duplicate_date_code_rows"] = int(
                    item["duplicate_date_code_rows"]
                ) + cross_duplicates
                item["duplicate_date_code_pairs"] = int(
                    item["duplicate_date_code_pairs"]
                ) + cross_duplicate_pairs
                if "date/code重复" not in issues:
                    issues.append("date/code重复")
            item["vwap_outside_range_1pct_days"] = len(file_vwap_days)
            if file_vwap_days:
                issues.append("vwap超出价格范围1%")

        except Exception as exc:
            read_error_count += 1
            item["read_error"] = f"{type(exc).__name__}: {exc}"
            issues.append("文件读取失败")
        item["issues"] = ";".join(dict.fromkeys(issues))
        file_rows.append(item)
        if position % 250 == 0 or position == len(files):
            print(f"已检查 {position}/{len(files)} 个文件。", flush=True)

    dominant_schema = ""
    if schema_counts:
        dominant_schema = max(schema_counts, key=lambda key: (schema_counts[key], key))
    for item in file_rows:
        fingerprint = str(item["schema_fingerprint"])
        differs = bool(fingerprint and fingerprint != dominant_schema)
        item["schema_differs_from_most_common"] = differs
        if differs:
            item["issues"] = ";".join(
                dict.fromkeys(filter(None, [str(item["issues"]), "列结构与多数文件不同"]))
            )

    daily_rows = []
    for date_key, counts in sorted(daily.items()):
        daily_rows.append(
            {
                "date": date_key,
                "year": int(date_key[:4]),
                "stock_count": int(counts["stock_count"]),
                "row_count": int(counts["row_count"]),
                "duplicate_date_code_rows": int(counts["duplicate_date_code_rows"]),
                "minute_count_bad_rows": int(counts["minute_count_bad_rows"]),
                "minute_count_bad_stocks": int(counts["minute_bad_stock_count"]),
                "vwap_outside_range_1pct_rows": int(counts["vwap_bad_rows"]),
                "vwap_outside_range_1pct_stocks": int(counts["vwap_bad_stock_count"]),
                "out_of_range_rows": int(counts["out_of_range_rows"]),
            }
        )
    daily_frame = pd.DataFrame(daily_rows, columns=DAILY_COVERAGE_COLUMNS)

    by_year_rows = []
    if not daily_frame.empty:
        for year, subset in daily_frame.groupby("year", sort=True):
            by_year_rows.append(
                {
                    "year": int(year),
                    "stock_count": len(year_stocks[int(year)]),
                    "date_count": len(subset),
                    "stock_day_records": int(subset["stock_count"].sum()),
                    "row_count": int(subset["row_count"].sum()),
                    "duplicate_date_code_rows": int(
                        subset["duplicate_date_code_rows"].sum()
                    ),
                    "minute_count_bad_rows": int(subset["minute_count_bad_rows"].sum()),
                    "minute_count_bad_stock_days": int(
                        subset["minute_count_bad_stocks"].sum()
                    ),
                    "vwap_outside_range_1pct_rows": int(
                        subset["vwap_outside_range_1pct_rows"].sum()
                    ),
                    "vwap_outside_range_1pct_stock_days": int(
                        subset["vwap_outside_range_1pct_stocks"].sum()
                    ),
                }
            )
    by_year_frame = pd.DataFrame(by_year_rows, columns=BY_YEAR_COLUMNS)

    bad_rows = [item for item in file_rows if item["issues"]]
    bad_frame = pd.DataFrame(bad_rows, columns=BAD_STOCK_COLUMNS)
    date_start = daily_frame["date"].min() if not daily_frame.empty else None
    date_end = daily_frame["date"].max() if not daily_frame.empty else None
    stock_day_records = int(daily_frame["stock_count"].sum()) if not daily_frame.empty else 0
    coverage_values = daily_frame["stock_count"].astype(int).tolist()
    factor_details = {
        name: {
            "non_null_count": int(factor_non_null[name]),
            "null_count": int(max(total_rows - factor_non_null[name], 0)),
            "non_null_ratio": _ratio(int(factor_non_null[name]), total_rows),
            "files_with_column": int(factor_file_counts[name]),
        }
        for name in FACTOR_BASE_COLUMNS
    }
    schema_variants = sorted(
        schema_details.values(), key=lambda value: (-int(value["file_count"]), str(value["fingerprint"]))
    )
    summary: dict[str, object] = {
        "cache_dir": str(cache_dir),
        "output_dir": str(output_dir),
        "expected_date_start": lower.strftime("%Y-%m-%d"),
        "expected_date_end": upper.strftime("%Y-%m-%d"),
        "limit": limit,
        "limited_scan": limit is not None,
        "available_file_count": len(all_files),
        "file_count": len(files),
        "read_error_file_count": read_error_count,
        "empty_file_count": sum(bool(item["empty_file"]) for item in file_rows),
        "bad_stock_file_count": len(bad_rows),
        "required_column_count": len(REQUIRED_COLUMNS),
        "required_columns": REQUIRED_COLUMNS,
        "all_required_columns_present": bool(files)
        and not any(item["missing_required_columns"] for item in file_rows)
        and read_error_count == 0,
        "schema_count": len(schema_counts),
        "schema_consistent": bool(files)
        and len(schema_counts) == 1
        and read_error_count == 0,
        "dominant_schema_fingerprint": dominant_schema or None,
        "schema_variants": schema_variants,
        "total_rows": total_rows,
        "metadata_rows": metadata_rows,
        "total_days": len(daily_frame),
        "total_stock_count": len(all_codes),
        "total_stock_day_records": stock_day_records,
        "date_start": date_start,
        "date_end": date_end,
        "invalid_date_rows": invalid_date_rows,
        "out_of_range_rows": out_of_range_rows,
        "missing_code_rows": missing_code_rows,
        "code_mismatch_rows": code_mismatch_rows,
        "minute_count_all_240": bool(total_rows)
        and minute_missing_rows == 0
        and minute_not_240_rows == 0,
        "minute_count_missing_rows": minute_missing_rows,
        "minute_count_not_240_rows": minute_not_240_rows,
        "duplicate_date_code_rows": duplicate_extra_rows,
        "duplicate_date_code_pairs": len(duplicate_pairs_seen),
        "vwap_outside_range_1pct_rows": vwap_bad_rows,
        "vwap_outside_range_1pct_stock_days": vwap_bad_stock_days,
        "vwap_outside_range_1pct_stock_day_ratio": _ratio(
            vwap_bad_stock_days, stock_day_records
        ),
        "vwap_outside_range_1pct_dates": len(vwap_bad_dates),
        "vwap_outside_range_1pct_date_ratio": _ratio(
            len(vwap_bad_dates), len(daily_frame)
        ),
        "vwap_outside_range_1pct_stocks": len(vwap_bad_stocks),
        "vwap_outside_range_1pct_stock_ratio": _ratio(
            len(vwap_bad_stocks), len(all_codes)
        ),
        "factor_base_columns": FACTOR_BASE_COLUMNS,
        "factor_base_non_null_counts": {
            name: int(factor_non_null[name]) for name in FACTOR_BASE_COLUMNS
        },
        "factor_base_non_null": factor_details,
        "daily_stock_coverage_distribution": _number_distribution(coverage_values),
        "cache_file_total_bytes": int(sum(file_sizes)),
        "cache_file_size_distribution_bytes": _number_distribution(file_sizes),
        "report_files": [SUMMARY_NAME, BY_YEAR_NAME, BAD_STOCKS_NAME, DAILY_COVERAGE_NAME],
    }

    atomic_write_csv(by_year_frame, output_dir / BY_YEAR_NAME)
    atomic_write_csv(bad_frame, output_dir / BAD_STOCKS_NAME)
    atomic_write_csv(daily_frame, output_dir / DAILY_COVERAGE_NAME)
    atomic_write_json(summary, output_dir / SUMMARY_NAME)
    print(
        f"检查完成：{len(files)} 个文件，{total_rows} 行，{len(bad_rows)} 个文件有问题。",
        flush=True,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    audit_cache(
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
