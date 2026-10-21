#!/usr/bin/env python3
"""核对本次补入的股票日线 CSV。"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="核对股票日线补齐结果")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def read_tail_lines(path: Path, minimum_bytes: int = 65536) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        block_size = min(size, minimum_bytes)
        handle.seek(-block_size, os.SEEK_END)
        block = handle.read()
    lines = block.splitlines()
    if block_size < size:
        lines = lines[1:]
    return [line.decode("gbk") for line in lines if line.strip()]


def number(value: str, path: Path, line_number: int) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{path} 检查区第 {line_number} 行有非数字字段：{value}") from exc


def main() -> None:
    args = parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    expected = {key: int(value) for key, value in report["downloaded_by_date"].items()}
    expected_dates = set(expected)
    start_date = min(expected_dates)
    counts: dict[str, int] = defaultdict(int)
    checked_rows = 0
    checked_files = 0
    errors: list[str] = []

    paths = sorted(args.data_dir.glob("*.csv"))
    for path in paths:
        seen_dates: set[str] = set()
        previous_date = ""
        file_checked = False
        try:
            lines = read_tail_lines(path)
        except Exception as exc:
            errors.append(f"{path.name} 读取失败：{exc}")
            continue
        for line_number, line in enumerate(lines, start=1):
            row = next(csv.reader([line]))
            if len(row) != 12 or row[0] == "股票代码":
                continue
            trade_date = row[2]
            if trade_date < start_date:
                continue
            file_checked = True
            if row[0].lower() != path.stem.lower():
                errors.append(f"{path.name} 的股票代码写成 {row[0]}")
            try:
                date.fromisoformat(trade_date)
            except ValueError:
                errors.append(f"{path.name} 日期格式错误：{trade_date}")
                continue
            if trade_date not in expected_dates:
                errors.append(f"{path.name} 出现计划外日期：{trade_date}")
            if trade_date in seen_dates:
                errors.append(f"{path.name} 日期重复：{trade_date}")
            if previous_date and trade_date <= previous_date:
                errors.append(f"{path.name} 日期没有递增：{previous_date} -> {trade_date}")
            seen_dates.add(trade_date)
            previous_date = trade_date

            try:
                open_price, high, low, close, pre_close, volume, amount, circ_mv, total_mv = (
                    number(value, path, line_number) for value in row[3:12]
                )
                if high < max(open_price, low, close) or low > min(open_price, high, close):
                    errors.append(f"{path.name} {trade_date} 开高低收关系错误")
                if min(open_price, high, low, close, pre_close, circ_mv, total_mv) <= 0:
                    errors.append(f"{path.name} {trade_date} 出现非正数价格或市值")
                if volume < 0 or amount < 0:
                    errors.append(f"{path.name} {trade_date} 出现负成交量或成交额")
            except ValueError as exc:
                errors.append(str(exc))

            counts[trade_date] += 1
            checked_rows += 1
        if file_checked:
            checked_files += 1

    for trade_date, expected_count in expected.items():
        actual_count = counts.get(trade_date, 0)
        if actual_count != expected_count:
            errors.append(f"{trade_date} 应有 {expected_count} 行，实际 {actual_count} 行")
    if checked_rows != int(report["inserted_rows"]):
        errors.append(f"本次应有 {report['inserted_rows']} 行，实际检查到 {checked_rows} 行")

    result = {
        "csv_files": len(paths),
        "checked_files": checked_files,
        "checked_rows": checked_rows,
        "checked_trade_days": len(counts),
        "latest_date": max(counts) if counts else None,
        "latest_date_rows": counts.get(max(counts), 0) if counts else 0,
        "errors": errors[:100],
        "error_count": len(errors),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
