# -*- coding: utf-8 -*-
"""用可重复的小样本比较市场分钟统计和网络月份计算的新旧做法。"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Sequence

import duckdb
import numpy as np
import pandas as pd
from scipy import sparse

import build_october_december_trial_network as network
import build_shared_market_minute_stats as shared


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = (
    BASE_DIR
    / ".codex-artifact"
    / "minute_completion"
    / "shared_network_benchmark.json"
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--shared-stocks", type=int, default=24)
    parser.add_argument("--network-stocks", type=int, default=600)
    parser.add_argument("--seed", type=int, default=20260723)
    return parser.parse_args(argv)


def _write_shared_sample(
    directory: Path, stock_count: int, seed: int
) -> list[Path]:
    rng = np.random.default_rng(seed)
    dates = pd.DatetimeIndex(
        [
            date
            for year in range(2020, 2026)
            for date in pd.bdate_range(f"{year}-03-02", periods=20)
        ]
    )
    minutes = np.r_[np.arange(571, 581), np.arange(781, 791)]
    paths = []
    directory.mkdir(parents=True, exist_ok=True)
    for stock in range(stock_count):
        rows = []
        for date in dates:
            price = 10.0 + stock * 0.01
            for minute in minutes:
                current_return = float(rng.normal(0.0, 0.0015))
                current_open = price
                price *= 1.0 + current_return
                rows.append(
                    {
                        "datetime": date
                        + pd.Timedelta(minutes=int(minute)),
                        "open": current_open,
                        "close": price,
                        "amount": float(rng.lognormal(12.0, 0.5)),
                    }
                )
        path = directory / f"sh{600000 + stock:06d}.parquet"
        pd.DataFrame(rows).to_parquet(path, compression="zstd", index=False)
        paths.append(path)
    return paths


def _compare_frames(left: pd.DataFrame, right: pd.DataFrame) -> float:
    left = left.sort_values("datetime").reset_index(drop=True)
    right = right.sort_values("datetime").reset_index(drop=True)
    if left["datetime"].tolist() != right["datetime"].tolist():
        raise AssertionError("新旧市场分钟表的时间不一致。")
    maximum = 0.0
    for column in shared.OUTPUT_COLUMNS:
        if pd.api.types.is_numeric_dtype(left[column]):
            first = left[column].to_numpy(dtype=float)
            second = right[column].to_numpy(dtype=float)
            if not np.allclose(
                first, second, rtol=1e-10, atol=1e-13, equal_nan=True
            ):
                raise AssertionError(f"市场分钟字段不一致：{column}")
            finite = np.isfinite(first) & np.isfinite(second)
            if finite.any():
                maximum = max(
                    maximum, float(np.max(np.abs(first[finite] - second[finite])))
                )
        elif pd.api.types.is_datetime64_any_dtype(left[column]):
            if not np.array_equal(
                pd.to_datetime(left[column]).to_numpy(dtype="datetime64[ns]"),
                pd.to_datetime(right[column]).to_numpy(dtype="datetime64[ns]"),
            ):
                raise AssertionError(f"市场分钟字段不一致：{column}")
        elif not left[column].astype(object).equals(
            right[column].astype(object)
        ):
            raise AssertionError(f"市场分钟字段不一致：{column}")
    return maximum


def benchmark_shared(root: Path, stock_count: int, seed: int) -> dict[str, object]:
    minute_dir = root / "shared_source"
    paths = _write_shared_sample(minute_dir, stock_count, seed)
    started = time.perf_counter()
    repeated_parts = []
    day_offset = 0
    with duckdb.connect() as connection:
        for year in range(2020, 2026):
            start, end = f"{year}-01-01", f"{year}-12-31"
            part = connection.execute(
                shared.build_query(paths, start, end, day_offset)
            ).fetchdf()
            day_offset += int(part["date"].nunique())
            repeated_parts.append(part)
    repeated_seconds = time.perf_counter() - started
    repeated = pd.concat(repeated_parts, ignore_index=True)

    partial = root / "shared_partial.parquet"
    started = time.perf_counter()
    worker_result = shared._streaming_partial_worker(
        ([str(path) for path in paths], "2020-01-01", "2025-12-31", str(partial))
    )
    streaming = shared._merge_streaming_partials(
        [partial], "2020-01-01", "2025-12-31"
    )
    streaming_seconds = time.perf_counter() - started
    maximum_difference = _compare_frames(repeated, streaming)
    return {
        "stock_files": len(paths),
        "source_rows": int(worker_result["rows_read"]),
        "years": 6,
        "old_source_file_reads": len(paths) * 6,
        "new_source_file_reads": len(paths),
        "old_seconds": repeated_seconds,
        "new_seconds": streaming_seconds,
        "speed_ratio_old_over_new": repeated_seconds / streaming_seconds,
        "maximum_absolute_difference": maximum_difference,
    }


def benchmark_network(root: Path, stock_count: int, seed: int) -> dict[str, object]:
    rng = np.random.default_rng(seed + 1)
    days = 252
    random_values = rng.random((days, stock_count))
    directions = np.zeros((days, stock_count), dtype=np.int8)
    directions[random_values < 0.018] = -1
    directions[random_values > 0.982] = 1
    codes = [f"s{number:06d}" for number in range(stock_count)]
    daily_return = rng.normal(0.0, 0.02, stock_count).astype(np.float32)
    negative_jump20 = -rng.random(stock_count).astype(np.float32) * 0.08
    nonjump20 = rng.normal(0.0, 0.04, stock_count).astype(np.float32)

    started = time.perf_counter()
    edges, legacy_nodes = network.compute_network_snapshot(
        directions,
        codes,
        "2025-12-31",
        prune_fraction=0.5,
    )
    legacy_seconds = time.perf_counter() - started
    positive = sparse.csr_matrix((directions == 1).astype(np.int32))
    negative = sparse.csr_matrix((directions == -1).astype(np.int32))
    started = time.perf_counter()
    compact = network.compute_compact_network_snapshot_from_events(
        positive,
        negative,
        codes,
        "2025-12-31",
        prune_fraction=0.5,
        network_window=days,
        daily_return=daily_return,
        negative_jump20=negative_jump20,
        nonjump20=nonjump20,
    )
    compact_seconds = time.perf_counter() - started
    np.testing.assert_array_equal(
        compact["edge_count"].to_numpy(),
        legacy_nodes["edge_count"].to_numpy(),
    )
    np.testing.assert_allclose(
        compact["association_sum"].to_numpy(),
        legacy_nodes["association_sum"].to_numpy(),
        rtol=0,
        atol=0,
    )
    code_map = {code: index for index, code in enumerate(codes)}
    rows = edges["source_code"].map(code_map).to_numpy(dtype=np.int32)
    columns = edges["target_code"].map(code_map).to_numpy(dtype=np.int32)
    weights = edges["association"].to_numpy(dtype=np.float64)
    for column, values in (
        ("peer_daily_return", daily_return),
        ("peer_negative_jump20", negative_jump20),
        ("peer_nonjump20", nonjump20),
    ):
        expected = network._weighted_peer_values(
            rows, columns, weights, values, stock_count
        )
        np.testing.assert_array_equal(
            compact[column].to_numpy(dtype=np.float32), expected
        )

    edge_path = root / "legacy_edges.parquet"
    legacy_node_path = root / "legacy_nodes.parquet"
    compact_path = root / "compact_nodes.parquet"
    edges.to_parquet(edge_path, compression="zstd", index=False)
    legacy_nodes.to_parquet(legacy_node_path, compression="zstd", index=False)
    compact.to_parquet(compact_path, compression="zstd", index=False)
    old_bytes = edge_path.stat().st_size + legacy_node_path.stat().st_size
    new_bytes = compact_path.stat().st_size
    bytes_per_edge = edge_path.stat().st_size / max(len(edges), 1)
    bytes_per_node = new_bytes / max(len(compact), 1)
    return {
        "days": days,
        "stocks": stock_count,
        "retained_edges": len(edges),
        "old_seconds": legacy_seconds,
        "new_seconds": compact_seconds,
        "old_snapshot_bytes": old_bytes,
        "new_snapshot_bytes": new_bytes,
        "disk_reduction_ratio": old_bytes / new_bytes,
        "measured_bytes_per_edge": bytes_per_edge,
        "measured_bytes_per_compact_node": bytes_per_node,
        "estimated_old_full_gib_at_656m_edges": 656_000_000
        * bytes_per_edge
        / 2**30,
        "estimated_new_full_gib_at_5530x120_nodes": 5530
        * 120
        * bytes_per_node
        / 2**30,
        "value_comparison": "exact",
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BASE_DIR / "tmp") as raw_root:
        root = Path(raw_root)
        result = {
            "shared_market": benchmark_shared(
                root, args.shared_stocks, args.seed
            ),
            "network": benchmark_network(root, args.network_stocks, args.seed),
            "note": "只使用合成小样本，没有读取或写入正式缓存。",
            "created_at": pd.Timestamp.now().isoformat(),
        }
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
