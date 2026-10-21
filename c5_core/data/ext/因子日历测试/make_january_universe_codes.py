# -*- coding: utf-8 -*-
"""生成一月分钟回测需要读取的股票文件清单。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd


ALIASES = {"SZ300114": "sz302132"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data",
    )
    parser.add_argument("--qlib-dir", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "january_original_minute_results"),
    )
    return parser.parse_args()


def atomic_write_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    qlib_dir = Path(args.qlib_dir)
    output_dir = Path(args.output_dir)
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    parts = []
    for name in ("csi800", "csi1000"):
        path = qlib_dir / "instruments" / f"{name}.txt"
        frame = pd.read_csv(
            path,
            sep="\t",
            header=None,
            names=["instrument", "start", "end"],
        )
        frame["start"] = pd.to_datetime(frame["start"], errors="coerce")
        frame["end"] = pd.to_datetime(frame["end"], errors="coerce")
        frame = frame[(frame["end"] >= start) & (frame["start"] <= end)]
        parts.append(frame[["instrument"]])

    instruments = sorted(
        pd.concat(parts, ignore_index=True)["instrument"].astype(str).str.upper().unique()
    )
    available = {path.stem.lower() for path in data_dir.glob("*.parquet")}
    selected = []
    missing = []
    mappings = []
    for instrument in instruments:
        ordinary = instrument.lower()
        file_code = ordinary if ordinary in available else ALIASES.get(instrument)
        if file_code in available:
            selected.append(file_code)
            mappings.append((instrument, file_code))
        else:
            missing.append(instrument)

    selected = sorted(set(selected))
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text("\n".join(selected) + "\n", output_dir / "january_universe_codes.txt")
    pd.DataFrame(mappings, columns=["qlib_instrument", "minute_file_code"]).to_csv(
        output_dir / "january_universe_code_mapping.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame({"missing_instrument": missing}).to_csv(
        output_dir / "january_universe_missing_files.csv", index=False, encoding="utf-8-sig"
    )
    print(
        f"历史成分股票{len(instruments)}只；找到分钟文件{len(selected)}只；"
        f"缺少{len(missing)}只。"
    )


if __name__ == "__main__":
    main()
