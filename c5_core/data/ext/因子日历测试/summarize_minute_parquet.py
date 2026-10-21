#!/usr/bin/env python3
"""核对转换结果并生成简明报告。"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pyarrow.parquet as pq


COUNT_FIELDS = [
    "rows",
    "input_rows_before_auction_merge",
    "opening_auction_rows_merged",
    "q1_overlap_rows_ignored",
    "rolling_overlap_rows_ignored",
    "days_with_missing_standard_minutes",
    "missing_standard_minute_rows",
    "input_rows_before_dedup",
    "premerge_duplicate_rows_removed",
    "premerge_conflicting_duplicate_rows",
    "duplicate_input_rows_removed",
    "conflicting_duplicate_input_rows",
    "empty_nontrading_days_removed",
    "empty_nontrading_rows_removed",
    "known_corrupt_days_removed",
    "known_corrupt_day_rows_removed",
    "known_corrupt_timestamp_rows_removed",
    "zero_no_trade_rows_removed",
    "unusable_rows_removed",
    "null_core_rows",
    "invalid_ohlc_rows",
    "negative_volume_or_amount_rows",
    "off_session_rows",
    "extreme_price_rows",
    "non_increasing_timestamps",
    "duplicate_timestamps",
    "large_intraday_jump_rows",
    "missing_float_shares_rows",
    "missing_total_shares_rows",
    "source_1_rows",
    "source_2_rows",
    "source_3_rows",
    "source_4_rows",
    "daily_common_days",
    "daily_price_mismatch_days",
    "daily_volume_mismatch_days",
    "daily_amount_mismatch_days",
    "minute_days_missing_daily_reference",
    "daily_days_missing_minutes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def load_latest(path: Path) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    if not path.exists():
        return latest
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            code = str(item.get("code") or "")
            if code:
                latest[code] = item
    return latest


def integer(item: dict[str, object], field: str) -> int:
    value = item.get(field)
    return int(value) if value is not None else 0


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    metadata_dir = output_dir / "_metadata"
    data_dir = output_dir / "data"
    latest = load_latest(metadata_dir / "conversion_manifest.jsonl")
    factors = load_latest(metadata_dir / "adj_factors_manifest.jsonl")
    ok = {code: item for code, item in latest.items() if item.get("status") == "ok"}
    errors = {code: item for code, item in latest.items() if item.get("status") != "ok"}

    totals = {field: sum(integer(item, field) for item in ok.values()) for field in COUNT_FIELDS}
    parquet_files = sorted(data_dir.glob("*.parquet"))
    footer_rows = 0
    footer_errors: list[dict[str, str]] = []
    row_mismatches: list[dict[str, object]] = []
    schemas: set[str] = set()
    for path in parquet_files:
        try:
            parquet = pq.ParquetFile(path)
            rows = parquet.metadata.num_rows
            footer_rows += rows
            schemas.add(str(parquet.schema_arrow))
            expected = integer(ok.get(path.stem, {}), "rows")
            if expected and rows != expected:
                row_mismatches.append({"code": path.stem, "manifest_rows": expected, "file_rows": rows})
        except Exception as exc:
            footer_errors.append({"code": path.stem, "error": str(exc)})

    issue_fields = [
        "duplicate_input_rows_removed",
        "conflicting_duplicate_input_rows",
        "empty_nontrading_days_removed",
        "known_corrupt_days_removed",
        "known_corrupt_timestamp_rows_removed",
        "zero_no_trade_rows_removed",
        "invalid_ohlc_rows",
        "negative_volume_or_amount_rows",
        "off_session_rows",
        "extreme_price_rows",
        "large_intraday_jump_rows",
        "missing_float_shares_rows",
        "missing_total_shares_rows",
        "days_with_missing_standard_minutes",
        "missing_standard_minute_rows",
        "daily_price_mismatch_days",
        "daily_volume_mismatch_days",
        "daily_amount_mismatch_days",
        "daily_days_missing_minutes",
    ]
    issue_rows = []
    for code, item in sorted(ok.items()):
        row = {
            "code": code,
            "first_time": item.get("first_time"),
            "last_time": item.get("last_time"),
            "rows": integer(item, "rows"),
            "days": integer(item, "days"),
            **{field: integer(item, field) for field in issue_fields},
            "daily_common_days": integer(item, "daily_common_days"),
            "daily_price_error_p99": item.get("daily_price_error_p99"),
            "daily_price_error_max": item.get("daily_price_error_max"),
            "daily_volume_relative_error_p99": item.get("daily_volume_relative_error_p99"),
            "daily_volume_relative_error_max": item.get("daily_volume_relative_error_max"),
            "daily_amount_relative_error_p99": item.get("daily_amount_relative_error_p99"),
            "daily_amount_relative_error_max": item.get("daily_amount_relative_error_max"),
        }
        if any(integer(item, field) for field in issue_fields):
            issue_rows.append(row)

    issue_csv = metadata_dir / "quality_issues.csv"
    if issue_rows:
        with issue_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(issue_rows[0]))
            writer.writeheader()
            writer.writerows(issue_rows)

    latest_path = metadata_dir / "latest_conversion_results.jsonl"
    with latest_path.open("w", encoding="utf-8", newline="\n") as handle:
        for code in sorted(latest):
            handle.write(json.dumps(latest[code], ensure_ascii=False, separators=(",", ":")) + "\n")

    top_issues: dict[str, list[dict[str, object]]] = {}
    for field in issue_fields:
        ranked = sorted(ok.values(), key=lambda item: integer(item, field), reverse=True)
        top_issues[field] = [
            {"code": item["code"], "count": integer(item, field)}
            for item in ranked[:20]
            if integer(item, field) > 0
        ]

    summary = {
        "conversion_codes": len(latest),
        "successful_codes": len(ok),
        "failed_codes": len(errors),
        "factor_codes": len(factors),
        "factor_ok_codes": sum(item.get("status") == "ok" for item in factors.values()),
        "parquet_files": len(parquet_files),
        "parquet_bytes": sum(path.stat().st_size for path in parquet_files),
        "parquet_footer_rows": footer_rows,
        "schema_count": len(schemas),
        "footer_errors": footer_errors,
        "row_mismatches": row_mismatches,
        "totals": totals,
        "failed_items": list(errors.values()),
        "top_issues": top_issues,
    }
    summary_path = metadata_dir / "conversion_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
