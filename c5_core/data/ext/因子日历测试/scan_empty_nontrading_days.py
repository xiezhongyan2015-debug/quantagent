#!/usr/bin/env python3
"""找出 Parquet 中整日量额为0且本地日线没有该日的记录。"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--daily-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def load_latest_status(manifest: Path) -> dict[str, str]:
    latest: dict[str, str] = {}
    with manifest.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            code = str(item.get("code") or "")
            if code:
                latest[code] = str(item.get("status") or "")
    return latest


def scan_one(parquet_path: Path, daily_dir: Path) -> dict[str, object]:
    code = parquet_path.stem
    table = pq.read_table(parquet_path, columns=["datetime", "volume", "amount"])
    frame = table.to_pandas()
    date_values = (
        frame["datetime"].dt.year * 10000
        + frame["datetime"].dt.month * 100
        + frame["datetime"].dt.day
    ).to_numpy(dtype=np.int32)
    totals = pd.DataFrame(
        {
            "date_int": date_values,
            "volume_abs": frame["volume"].abs().to_numpy(dtype=np.float64),
            "amount_abs": frame["amount"].abs().to_numpy(dtype=np.float64),
        }
    ).groupby("date_int", sort=False).agg(
        volume_abs=("volume_abs", "sum"),
        amount_abs=("amount_abs", "sum"),
        rows=("date_int", "size"),
    )
    daily_path = daily_dir / f"{code}.csv"
    daily_dates: set[int] = set()
    if daily_path.exists():
        daily = pd.read_csv(
            daily_path,
            skiprows=1,
            usecols=["交易日期"],
            encoding="gb18030",
        )
        dates = pd.to_datetime(daily["交易日期"], errors="coerce").dropna()
        daily_dates = {
            int(value.year * 10000 + value.month * 100 + value.day)
            for value in dates
        }
    days = [
        {"date": int(day), "rows": int(row["rows"])}
        for day, row in totals.iterrows()
        if float(row["volume_abs"]) == 0.0
        and float(row["amount_abs"]) == 0.0
        and int(day) not in daily_dates
    ]
    return {
        "code": code,
        "days": days,
        "day_count": len(days),
        "row_count": sum(int(item["rows"]) for item in days),
    }


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    daily_dir = args.daily_dir.resolve()
    metadata_dir = output_dir / "_metadata"
    status = load_latest_status(metadata_dir / "conversion_manifest.jsonl")
    paths = [
        path
        for path in sorted((output_dir / "data").glob("*.parquet"))
        if status.get(path.stem) == "ok"
    ]
    results: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [(path, executor.submit(scan_one, path, daily_dir)) for path in paths]
        for index, (path, future) in enumerate(futures, start=1):
            try:
                item = future.result()
                if int(item["day_count"]):
                    results.append(item)
            except Exception as exc:
                errors.append({"code": path.stem, "error": str(exc)})
            if index % 500 == 0:
                print(f"已扫描 {index}/{len(paths)}", flush=True)

    results.sort(key=lambda item: str(item["code"]))
    report_path = metadata_dir / "empty_nontrading_days.jsonl"
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in results:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    summary = {
        "scanned_ok_codes": len(paths),
        "affected_codes": len(results),
        "affected_days": sum(int(item["day_count"]) for item in results),
        "affected_rows": sum(int(item["row_count"]) for item in results),
        "scan_errors": errors,
        "latest_non_ok_codes": sorted(code for code, value in status.items() if value != "ok"),
        "report": str(report_path),
    }
    summary_path = metadata_dir / "empty_nontrading_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
