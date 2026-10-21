#!/usr/bin/env python3
"""抽样估算分钟 CSV 转为 Parquet 后的占用空间。"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


PRICE_COLUMNS = ["开盘", "最高", "最低", "收盘", "涨跌(元)"]
FLOAT_COLUMNS = ["涨跌幅(%)", "换手率(%)"]
INT_COLUMNS = ["成交量(股)", "成交额(元)", "流通股本(股)", "总股本(股)"]
CORE_COLUMNS = [
    "日期",
    "开盘",
    "最高",
    "最低",
    "收盘",
    "成交量(股)",
    "成交额(元)",
    "流通股本(股)",
    "总股本(股)",
]


def sample_members(members: list[zipfile.ZipInfo], count: int) -> list[zipfile.ZipInfo]:
    ordered = sorted(members, key=lambda item: item.file_size)
    if count >= len(ordered):
        return ordered
    positions = {round(index * (len(ordered) - 1) / (count - 1)) for index in range(count)}
    return [ordered[index] for index in sorted(positions)]


def convert_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    output_path: Path,
    row_group_size: int,
    profile: str,
    compression_level: int,
) -> dict[str, object]:
    writer: pq.ParquetWriter | None = None
    rows = 0
    started = time.monotonic()
    try:
        with archive.open(member) as source:
            for frame in pd.read_csv(source, chunksize=row_group_size):
                if profile.startswith("core"):
                    frame = frame[CORE_COLUMNS]
                frame["日期"] = pd.to_datetime(frame["日期"], errors="raise")
                float_type = "float64" if profile == "core_float64" else "float32"
                selected_prices = [column for column in PRICE_COLUMNS if column in frame]
                selected_floats = [column for column in FLOAT_COLUMNS if column in frame]
                for column in selected_prices + selected_floats:
                    frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float_type)
                for column in [item for item in INT_COLUMNS if item in frame]:
                    frame[column] = pd.to_numeric(frame[column], errors="raise").round().astype("Int64")
                table = pa.Table.from_pandas(frame, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(
                        output_path,
                        table.schema,
                        compression="zstd",
                        compression_level=compression_level,
                        use_dictionary=True,
                        write_statistics=True,
                    )
                writer.write_table(table, row_group_size=row_group_size)
                rows += len(frame)
    finally:
        if writer is not None:
            writer.close()
    return {
        "member": member.filename,
        "csv_bytes": member.file_size,
        "zip_bytes": member.compress_size,
        "parquet_bytes": output_path.stat().st_size,
        "rows": rows,
        "seconds": round(time.monotonic() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--row-group-size", type=int, default=250_000)
    parser.add_argument(
        "--profile",
        choices=("full_float32", "core_float32", "core_float64"),
        default="full_float32",
    )
    parser.add_argument("--compression-level", type=int, default=9)
    parser.add_argument("--keep-dir", type=Path)
    args = parser.parse_args()

    if args.keep_dir:
        output_dir = args.keep_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        remove_after = False
    else:
        output_dir = Path(tempfile.mkdtemp(prefix="minute_parquet_test_"))
        remove_after = True

    results: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(args.archive) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            chosen = sample_members(members, args.samples)
            for index, member in enumerate(chosen, start=1):
                output_path = output_dir / f"sample_{index:02d}.parquet"
                result = convert_member(
                    archive,
                    member,
                    output_path,
                    args.row_group_size,
                    args.profile,
                    args.compression_level,
                )
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)

        source_total = sum(item.file_size for item in members)
        zip_total = sum(item.compress_size for item in members)
        sample_csv = sum(int(item["csv_bytes"]) for item in results)
        sample_parquet = sum(int(item["parquet_bytes"]) for item in results)
        estimate = round(source_total * sample_parquet / sample_csv)
        summary = {
            "sample_count": len(results),
            "profile": args.profile,
            "compression_level": args.compression_level,
            "sample_csv_bytes": sample_csv,
            "sample_parquet_bytes": sample_parquet,
            "parquet_to_csv": sample_parquet / sample_csv,
            "parquet_to_zip": sample_parquet / sum(int(item["zip_bytes"]) for item in results),
            "archive_csv_bytes": source_total,
            "archive_zip_bytes": zip_total,
            "estimated_parquet_bytes": estimate,
        }
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        if remove_after:
            shutil.rmtree(output_dir)


if __name__ == "__main__":
    main()
