# -*- coding: utf-8 -*-
"""用 NumPy 精确计算逐日滚动 10 日聪明钱 VWAP 比值。"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = (
    BASE_DIR / ".codex-artifact" / "minute_incremental_benchmark" / "smart_numpy.parquet"
)
_CODE_PATTERN = re.compile(r"[a-z]{2}[0-9]{6}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2026-05-29")
    return parser.parse_args(argv)


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": pd.Series(dtype="object"),
            "date": pd.Series(dtype="datetime64[ns]"),
            "smart_money_vwap_ratio_10d": pd.Series(dtype="float64"),
        }
    )


def compute_smart_money_numpy(
    source_path: str | Path,
    start: str,
    end: str,
) -> pd.DataFrame:
    """按原定义精确排序分钟记录，不使用抽样或近似计算。"""

    source = Path(source_path)
    code = source.stem.lower()
    if not _CODE_PATTERN.fullmatch(code):
        raise ValueError(f"无法从文件名识别股票代码：{source.name}")
    start_time = pd.Timestamp(start)
    end_time = pd.Timestamp(end)
    if start_time > end_time:
        raise ValueError("开始日期不能晚于结束日期。")
    frame = pd.read_parquet(
        source,
        columns=[
            "datetime",
            "open",
            "close",
            "volume",
            "amount",
            "qfq_ratio",
        ],
        filters=[
            ("datetime", ">=", start_time),
            ("datetime", "<", end_time + pd.Timedelta(days=1)),
        ],
    )
    if frame.empty:
        return _empty_result()
    frame = frame.sort_values("datetime", kind="stable").reset_index(drop=True)

    datetimes = frame["datetime"].to_numpy()
    dates = datetimes.astype("datetime64[D]")
    day_starts = np.r_[0, np.flatnonzero(dates[1:] != dates[:-1]) + 1]
    if len(day_starts) < 10:
        return _empty_result()
    day_ends = np.r_[day_starts[1:], len(frame)]

    opens = frame["open"].to_numpy(dtype=np.float64, copy=False)
    closes = frame["close"].to_numpy(dtype=np.float64, copy=False)
    volumes = frame["volume"].to_numpy(dtype=np.float64, copy=False)
    amounts = frame["amount"].to_numpy(dtype=np.float64, copy=False)
    qfq_ratios = frame["qfq_ratio"].to_numpy(dtype=np.float64, copy=False)

    previous_closes = np.empty_like(closes)
    previous_closes[1:] = closes[:-1]
    previous_closes[day_starts] = opens[day_starts]
    with np.errstate(divide="ignore", invalid="ignore"):
        minute_returns = np.log(closes / previous_closes)
        smart_scores = np.abs(minute_returns) / np.power(volumes, 0.25)
    adjusted_amounts = amounts * qfq_ratios

    values = np.full(len(day_starts), np.nan, dtype=np.float64)
    for day_index in range(9, len(day_starts)):
        window_start = int(day_starts[day_index - 9])
        window_end = int(day_ends[day_index])
        valid = (
            (volumes[window_start:window_end] > 0)
            & (amounts[window_start:window_end] >= 0)
        )
        row_ids = np.flatnonzero(valid) + window_start
        if row_ids.size == 0:
            continue

        # 原定义先按聪明钱分数降序；分数相同时按时间升序。
        # 原始记录已按时间稳定排序，因此稳定排序能精确保留第二排序条件。
        score_order = np.argsort(-smart_scores[row_ids], kind="stable")
        ranked_ids = row_ids[score_order]
        ranked_volumes = volumes[ranked_ids]
        # DuckDB 单进程按排序后的顺序逐个累加。这里也按同一顺序累加，
        # 避免 NumPy 分块求和带来的浮点末位差异。
        total_volume = sum(ranked_volumes)
        if total_volume == 0 or not np.isfinite(total_volume):
            continue
        prior_volume = (
            np.cumsum(ranked_volumes, dtype=np.float64) - ranked_volumes
        )
        selected = prior_volume < 0.20 * total_volume
        selected_volume = sum(ranked_volumes[selected])
        if selected_volume == 0:
            continue

        ranked_amounts = adjusted_amounts[ranked_ids]
        selected_amount = sum(ranked_amounts[selected])
        total_amount = sum(ranked_amounts)
        if total_amount == 0 or not np.isfinite(total_amount):
            continue
        values[day_index] = (
            selected_amount / selected_volume
        ) / (total_amount / total_volume)

    return pd.DataFrame(
        {
            "code": code,
            "date": pd.to_datetime(dates[day_starts[9:]]),
            "smart_money_vwap_ratio_10d": values[9:],
        }
    )


def _assert_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("输出必须位于当前工作区内。")
    if resolved.suffix.lower() != ".parquet":
        raise ValueError("输出必须使用 .parquet 后缀。")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output = _assert_output(Path(args.output))
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f"{output.stem}.tmp.parquet")
    temp.unlink(missing_ok=True)
    started = time.perf_counter()
    result = compute_smart_money_numpy(args.source, args.start, args.end)
    result.to_parquet(temp, index=False, compression="zstd")
    temp.replace(output)
    print(
        f"已生成 {len(result)} 行，耗时 {time.perf_counter() - started:.3f} 秒："
        f"{output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
