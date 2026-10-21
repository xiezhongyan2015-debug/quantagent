# -*- coding: utf-8 -*-
"""由B类试算日值生成252日共同跳跃网络小表。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Sequence

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import sparse

import build_october_december_trial_daily as trial_daily
import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = BASE_DIR / "october_december_trial_results" / "cache" / "network"
DEFAULT_EDGES_DIR = DEFAULT_ROOT / "edges"
DEFAULT_NODES_DIR = DEFAULT_ROOT / "nodes"
DEFAULT_MANIFEST = DEFAULT_ROOT / "network_manifest.json"
DEFAULT_METHODOLOGY = DEFAULT_ROOT / "network_methodology.json"

NETWORK_VERSION = 2
DEFAULT_WINDOW = 252
DEFAULT_PRUNE_FRACTION = 0.50

EDGE_COLUMNS = [
    "snapshot_date",
    "source_code",
    "target_code",
    "cojump_count",
    "source_jump_count",
    "association",
    "retained_rank",
    "formula_mode",
    "formula_label",
    "network_version",
    "network_window",
    "prune_fraction",
]

NODE_COLUMNS = [
    "snapshot_date",
    "code",
    "source_jump_count",
    "candidate_edge_count",
    "edge_count",
    "association_sum",
    "formula_mode",
    "formula_label",
    "network_version",
    "network_window",
    "prune_fraction",
]

COMPACT_VALUE_COLUMNS = [
    "peer_daily_return",
    "peer_negative_jump20",
    "peer_nonjump20",
]

COMPACT_NODE_COLUMNS = NODE_COLUMNS + COMPACT_VALUE_COLUMNS

COMPACT_RESUME_COLUMNS = [
    "daily_source_fingerprint",
    "base_source_fingerprint",
]


def _sql_path(value: str | Path) -> str:
    return str(Path(value).resolve()).replace("\\", "/").replace("'", "''")


def _assert_workspace_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("输出必须位于当前工作区内。")
    return resolved


def _atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.stem}.tmp.parquet")
    frame.to_parquet(temporary, compression="zstd", index=False)
    os.replace(temporary, path)


def _dilate_one_day(events: sparse.csr_matrix) -> sparse.csr_matrix:
    """在已有历史范围内把目标股票事件扩展到前后一个交易日。"""

    row_count, column_count = events.shape
    zero = sparse.csr_matrix((1, column_count), dtype=np.int32)
    previous = sparse.vstack([zero, events[:-1]], format="csr")
    following = sparse.vstack([events[1:], zero], format="csr")
    result = (events + previous + following).tocsr()
    result.data[:] = 1
    result.eliminate_zeros()
    if result.shape != (row_count, column_count):
        raise AssertionError("事件日期扩展后的大小不正确。")
    return result


def compute_network_snapshot(
    directions: np.ndarray | sparse.spmatrix,
    codes: Sequence[str],
    snapshot_date: str | pd.Timestamp,
    prune_fraction: float = DEFAULT_PRUNE_FRACTION,
    network_window: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """计算一个月末窗口的有向关联边与节点统计。"""

    if not 0.0 <= prune_fraction < 1.0:
        raise ValueError("prune_fraction必须在[0,1)内。")
    source = directions.toarray() if sparse.issparse(directions) else np.asarray(directions)
    if source.ndim != 2 or source.shape[1] != len(codes):
        raise ValueError("跳跃方向数据与股票代码数量不一致。")
    positive = sparse.csr_matrix((source == 1).astype(np.int32))
    negative = sparse.csr_matrix((source == -1).astype(np.int32))
    return compute_network_snapshot_from_events(
        positive,
        negative,
        codes,
        snapshot_date,
        prune_fraction,
        network_window or source.shape[0],
    )


def compute_network_snapshot_from_events(
    positive: sparse.csr_matrix,
    negative: sparse.csr_matrix,
    codes: Sequence[str],
    snapshot_date: str | pd.Timestamp,
    prune_fraction: float,
    network_window: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if positive.shape != negative.shape or positive.shape[1] != len(codes):
        raise ValueError("正负跳跃事件大小不一致。")
    positive = positive.astype(np.int32).tocsr()
    negative = negative.astype(np.int32).tocsr()
    target_positive = _dilate_one_day(positive)
    target_negative = _dilate_one_day(negative)
    counts = (
        positive.T @ target_positive + negative.T @ target_negative
    ).tocsr()
    counts.setdiag(0)
    counts.eliminate_zeros()
    source_jump_count = np.asarray(
        positive.sum(axis=0) + negative.sum(axis=0)
    ).ravel().astype(np.int32)
    coo = counts.tocoo()
    good = (
        (coo.row != coo.col)
        & (coo.data > 0)
        & (source_jump_count[coo.row] > 0)
    )
    rows = coo.row[good].astype(np.int32)
    columns = coo.col[good].astype(np.int32)
    cojump = coo.data[good].astype(np.int32)
    association = cojump.astype(np.float64) / source_jump_count[rows]

    candidate_edge_count = np.bincount(rows, minlength=len(codes)).astype(np.int32)
    retained_count = int(math.ceil(len(rows) * (1.0 - prune_fraction)))
    if retained_count:
        order = np.lexsort(
            (columns, rows, -cojump.astype(np.float64), -association)
        )
        selected = order[:retained_count]
        rows = rows[selected]
        columns = columns[selected]
        cojump = cojump[selected]
        association = association[selected]
    else:
        rows = rows[:0]
        columns = columns[:0]
        cojump = cojump[:0]
        association = association[:0]

    edge_count = np.bincount(rows, minlength=len(codes)).astype(np.int32)
    association_sum = np.bincount(
        rows, weights=association, minlength=len(codes)
    ).astype(np.float64)
    date = pd.Timestamp(snapshot_date).normalize()
    code_array = np.asarray([str(code).lower() for code in codes], dtype=object)
    edges = pd.DataFrame(
        {
            "snapshot_date": np.full(len(rows), date),
            "source_code": code_array[rows],
            "target_code": code_array[columns],
            "cojump_count": cojump,
            "source_jump_count": source_jump_count[rows],
            "association": association,
            "retained_rank": np.arange(1, len(rows) + 1, dtype=np.int32),
            "formula_mode": "trial",
            "formula_label": "试算",
            "network_version": np.full(len(rows), NETWORK_VERSION, dtype=np.int8),
            "network_window": np.full(len(rows), network_window, dtype=np.int16),
            "prune_fraction": np.full(len(rows), prune_fraction),
        },
        columns=EDGE_COLUMNS,
    )
    nodes = pd.DataFrame(
        {
            "snapshot_date": np.full(len(codes), date),
            "code": code_array,
            "source_jump_count": source_jump_count,
            "candidate_edge_count": candidate_edge_count,
            "edge_count": edge_count,
            "association_sum": association_sum,
            "formula_mode": "trial",
            "formula_label": "试算",
            "network_version": np.full(len(codes), NETWORK_VERSION, dtype=np.int8),
            "network_window": np.full(len(codes), network_window, dtype=np.int16),
            "prune_fraction": np.full(len(codes), prune_fraction),
        },
        columns=NODE_COLUMNS,
    )
    return edges, nodes


def _compute_compact_network_arrays(
    positive: sparse.csr_matrix,
    negative: sparse.csr_matrix,
    codes: Sequence[str],
    prune_fraction: float,
) -> dict[str, np.ndarray]:
    """按旧版排序和删减规则计算关系，只在内存中保留本月结果。"""

    if positive.shape != negative.shape or positive.shape[1] != len(codes):
        raise ValueError("正负跳跃事件大小不一致。")
    positive = positive.astype(np.int32).tocsr()
    negative = negative.astype(np.int32).tocsr()
    target_positive = _dilate_one_day(positive)
    target_negative = _dilate_one_day(negative)
    counts = (
        positive.T @ target_positive + negative.T @ target_negative
    ).tocsr()
    counts.setdiag(0)
    counts.eliminate_zeros()
    source_jump_count = np.asarray(
        positive.sum(axis=0) + negative.sum(axis=0)
    ).ravel().astype(np.int32)
    coo = counts.tocoo()
    good = (
        (coo.row != coo.col)
        & (coo.data > 0)
        & (source_jump_count[coo.row] > 0)
    )
    rows = coo.row[good].astype(np.int32)
    columns = coo.col[good].astype(np.int32)
    cojump = coo.data[good].astype(np.int32)
    association = cojump.astype(np.float64) / source_jump_count[rows]
    candidate_edge_count = np.bincount(
        rows, minlength=len(codes)
    ).astype(np.int32)
    retained_count = int(math.ceil(len(rows) * (1.0 - prune_fraction)))
    if retained_count:
        order = np.lexsort(
            (columns, rows, -cojump.astype(np.float64), -association)
        )
        selected = order[:retained_count]
        rows = rows[selected]
        columns = columns[selected]
        cojump = cojump[selected]
        association = association[selected]
    else:
        rows = rows[:0]
        columns = columns[:0]
        cojump = cojump[:0]
        association = association[:0]
    return {
        "rows": rows,
        "columns": columns,
        "cojump": cojump,
        "association": association,
        "source_jump_count": source_jump_count,
        "candidate_edge_count": candidate_edge_count,
        "edge_count": np.bincount(rows, minlength=len(codes)).astype(np.int32),
        "association_sum": np.bincount(
            rows, weights=association, minlength=len(codes)
        ).astype(np.float64),
    }


def _weighted_peer_values(
    rows: np.ndarray,
    columns: np.ndarray,
    weights: np.ndarray,
    target_values: np.ndarray,
    stock_count: int,
) -> np.ndarray:
    result = np.full(stock_count, np.nan, dtype=np.float32)
    good = (
        np.isfinite(weights)
        & (weights > 0)
        & np.isfinite(target_values[columns])
    )
    if not good.any():
        return result
    current_rows = rows[good]
    current_columns = columns[good]
    current_weights = weights[good]
    numerator = np.bincount(
        current_rows,
        weights=current_weights * target_values[current_columns],
        minlength=stock_count,
    )
    denominator = np.bincount(
        current_rows, weights=current_weights, minlength=stock_count
    )
    np.divide(numerator, denominator, out=result, where=denominator > 0)
    return result


def compute_compact_network_snapshot_from_events(
    positive: sparse.csr_matrix,
    negative: sparse.csr_matrix,
    codes: Sequence[str],
    snapshot_date: str | pd.Timestamp,
    prune_fraction: float,
    network_window: int,
    daily_return: np.ndarray,
    negative_jump20: np.ndarray,
    nonjump20: np.ndarray,
) -> pd.DataFrame:
    """直接生成回测需要的每股汇总值，不保存逐条股票关系。"""

    arrays = _compute_compact_network_arrays(
        positive, negative, codes, prune_fraction
    )
    stock_count = len(codes)
    for name, values in (
        ("daily_return", daily_return),
        ("negative_jump20", negative_jump20),
        ("nonjump20", nonjump20),
    ):
        if np.shape(values) != (stock_count,):
            raise ValueError(f"{name}长度与股票数量不一致。")
    rows = arrays["rows"]
    columns = arrays["columns"]
    association = arrays["association"]
    extra_values = {
        "peer_daily_return": _weighted_peer_values(
            rows, columns, association, daily_return, stock_count
        ),
        "peer_negative_jump20": _weighted_peer_values(
            rows, columns, association, negative_jump20, stock_count
        ),
        "peer_nonjump20": _weighted_peer_values(
            rows, columns, association, nonjump20, stock_count
        ),
    }
    date = pd.Timestamp(snapshot_date).normalize()
    values: dict[str, object] = {
        "snapshot_date": np.full(stock_count, date),
        "code": np.asarray(
            [str(code).lower() for code in codes], dtype=object
        ),
        "source_jump_count": arrays["source_jump_count"],
        "candidate_edge_count": arrays["candidate_edge_count"],
        "edge_count": arrays["edge_count"],
        "association_sum": arrays["association_sum"],
        "formula_mode": "trial",
        "formula_label": "试算",
        "network_version": np.full(
            stock_count, NETWORK_VERSION, dtype=np.int8
        ),
        "network_window": np.full(
            stock_count, network_window, dtype=np.int16
        ),
        "prune_fraction": np.full(stock_count, prune_fraction),
        **extra_values,
    }
    return pd.DataFrame(values, columns=COMPACT_NODE_COLUMNS)


def _source_fingerprint(files: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        stat = path.stat()
        digest.update(path.name.lower().encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def read_event_inputs(
    daily_glob: Path,
    start: str,
    end: str,
    temp_dir: Path,
    memory_limit: str,
    threads: int = 4,
) -> tuple[pd.DatetimeIndex, list[str], sparse.csr_matrix, sparse.csr_matrix]:
    connection = duckdb.connect()
    try:
        connection.execute(f"SET threads = {max(1, int(threads))}")
        connection.execute(f"SET memory_limit = '{memory_limit}'")
        connection.execute(f"SET temp_directory = '{_sql_path(temp_dir)}'")
        source = _sql_path(daily_glob)
        parameters = [pd.Timestamp(start), pd.Timestamp(end)]
        dates = connection.execute(
            f"""
            SELECT DISTINCT date
            FROM read_parquet('{source}', union_by_name = true)
            WHERE date BETWEEN ? AND ?
            ORDER BY date
            """,
            parameters,
        ).fetchdf()
        codes = connection.execute(
            f"""
            SELECT DISTINCT lower(code) AS code
            FROM read_parquet('{source}', union_by_name = true)
            WHERE date BETWEEN ? AND ?
            ORDER BY code
            """,
            parameters,
        ).fetchdf()
        events = connection.execute(
            f"""
            SELECT
                date,
                lower(code) AS code,
                CAST(jiang_oomen_jump_direction_daily_trial AS TINYINT)
                    AS direction
            FROM read_parquet('{source}', union_by_name = true)
            WHERE date BETWEEN ? AND ?
              AND abs(jiang_oomen_jump_direction_daily_trial) = 1
            ORDER BY date, code
            """,
            parameters,
        ).fetchdf()
    finally:
        connection.close()
    date_index = pd.DatetimeIndex(pd.to_datetime(dates["date"])).normalize()
    code_list = codes["code"].astype(str).str.lower().tolist()
    date_map = {date: index for index, date in enumerate(date_index)}
    code_map = {code: index for index, code in enumerate(code_list)}
    event_dates = pd.to_datetime(events["date"]).dt.normalize()
    rows = event_dates.map(date_map).to_numpy(dtype=np.int32)
    columns = events["code"].map(code_map).to_numpy(dtype=np.int32)
    directions = events["direction"].to_numpy(dtype=np.int8)
    shape = (len(date_index), len(code_list))
    positive_mask = directions == 1
    negative_mask = directions == -1
    positive = sparse.csr_matrix(
        (
            np.ones(int(positive_mask.sum()), dtype=np.int32),
            (rows[positive_mask], columns[positive_mask]),
        ),
        shape=shape,
    )
    negative = sparse.csr_matrix(
        (
            np.ones(int(negative_mask.sum()), dtype=np.int32),
            (rows[negative_mask], columns[negative_mask]),
        ),
        shape=shape,
    )
    return date_index, code_list, positive, negative


def _rolling_valid_sum(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    sums = np.full(source.shape, np.nan, dtype=np.float32)
    for column in range(source.shape[1]):
        positions = np.flatnonzero(np.isfinite(source[:, column]))
        if len(positions) < window:
            continue
        observations = source[positions, column]
        cumulative = np.r_[0.0, np.cumsum(observations, dtype=np.float64)]
        rolling_sum = cumulative[window:] - cumulative[:-window]
        sums[positions[window - 1 :], column] = rolling_sum.astype(np.float32)
    return sums


def _returns_from_previous_valid(close: np.ndarray) -> np.ndarray:
    source = np.asarray(close, dtype=np.float64)
    previous = np.full(source.shape, np.nan, dtype=np.float32)
    latest = np.full(source.shape[1], np.nan, dtype=np.float64)
    for row in range(source.shape[0]):
        previous[row] = latest
        current = source[row]
        good = np.isfinite(current)
        latest[good] = current[good]
    result = np.full(source.shape, np.nan, dtype=np.float32)
    valid = np.isfinite(source) & np.isfinite(previous) & (previous != 0)
    np.divide(source, previous, out=result, where=valid)
    result[valid] -= 1.0
    return result


def read_compact_inputs(
    daily_glob: Path,
    base_glob: Path,
    base_extra_glob: Path | None,
    start: str,
    end: str,
    temp_dir: Path,
    memory_limit: str,
    threads: int = 4,
) -> tuple[
    pd.DatetimeIndex,
    list[str],
    sparse.csr_matrix,
    sparse.csr_matrix,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    """读取稀疏跳跃事件，并生成三项关联股加权值所需的日值。"""

    dates, codes, positive, negative = read_event_inputs(
        daily_glob, start, end, temp_dir, memory_limit, threads
    )
    shape = (len(dates), len(codes))
    close = np.full(shape, np.nan, dtype=np.float32)
    negative_jump = np.full(shape, np.nan, dtype=np.float32)
    nonjump = np.full(shape, np.nan, dtype=np.float32)
    code_map = {code: index for index, code in enumerate(codes)}
    connection = duckdb.connect()
    try:
        connection.execute(f"SET threads = {max(1, int(threads))}")
        connection.execute(f"SET memory_limit = '{memory_limit}'")
        connection.execute(f"SET temp_directory = '{_sql_path(temp_dir)}'")
        base_sources = [base_glob]
        if base_extra_glob is not None:
            base_sources.append(base_extra_glob)
        for current_base in base_sources:
            cursor = connection.execute(
                f"""
                SELECT
                    t.date,
                    lower(t.code) AS code,
                    CAST(b.close AS FLOAT) AS close,
                    CAST(t.negative_jump_return_daily_trial AS FLOAT)
                        AS negative_jump,
                    CAST(t.nonjump_return_daily_trial AS FLOAT) AS nonjump
                FROM read_parquet(
                    '{_sql_path(daily_glob)}', union_by_name = true
                ) AS t
                INNER JOIN read_parquet(
                    '{_sql_path(current_base)}', union_by_name = true
                ) AS b
                  ON t.date = b.date
                 AND lower(t.code) = lower(b.code)
                WHERE t.date BETWEEN ? AND ?
                ORDER BY t.date, lower(t.code)
                """,
                [pd.Timestamp(start), pd.Timestamp(end)],
            )
            reader = cursor.fetch_record_batch(rows_per_batch=250_000)
            for batch in reader:
                frame = batch.to_pandas()
                frame_dates = pd.DatetimeIndex(
                    pd.to_datetime(frame["date"])
                ).normalize()
                rows = dates.get_indexer(frame_dates)
                columns = frame["code"].map(code_map).to_numpy(dtype=np.int32)
                good = (rows >= 0) & (columns >= 0)
                rows = rows[good]
                columns = columns[good]
                # 后读取的近期基础日值覆盖重叠日期，并补上2026年。
                close[rows, columns] = frame.loc[good, "close"].to_numpy(
                    dtype=np.float32
                )
                negative_jump[rows, columns] = frame.loc[
                    good, "negative_jump"
                ].to_numpy(dtype=np.float32)
                nonjump[rows, columns] = frame.loc[good, "nonjump"].to_numpy(
                    dtype=np.float32
                )
    finally:
        connection.close()
    daily_return = _returns_from_previous_valid(close)
    negative_jump20 = _rolling_valid_sum(negative_jump, 20)
    nonjump20 = _rolling_valid_sum(nonjump, 20)
    return (
        dates,
        codes,
        positive,
        negative,
        daily_return,
        negative_jump20,
        nonjump20,
    )


def month_end_positions(dates: pd.DatetimeIndex, window: int) -> np.ndarray:
    if len(dates) < window:
        return np.array([], dtype=np.int32)
    months = dates.to_period("M")
    is_month_end = np.r_[months[:-1] != months[1:], True]
    return np.flatnonzero(is_month_end & (np.arange(len(dates)) >= window - 1))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-dir", type=Path, default=trial_daily.DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--base-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["base_daily"],
    )
    parser.add_argument(
        "--base-cache-extra-glob",
        type=Path,
        help="可选的近期基础日值；在重叠日期覆盖主基础日值。",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--edges-dir", type=Path)
    parser.add_argument("--nodes-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--methodology", type=Path)
    parser.add_argument("--temp-dir", type=Path, default=factor_store.DEFAULT_TEMP_DIR)
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument(
        "--prune-fraction", type=float, default=DEFAULT_PRUNE_FRACTION
    )
    parser.add_argument(
        "--output-mode",
        choices=("compact", "legacy"),
        default="compact",
        help="compact只保存回测需要的每股汇总值；legacy保留逐条关系文件。",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.window < 2:
        raise ValueError("window必须至少为2。")
    if args.threads < 1:
        raise ValueError("threads必须至少为1。")
    if not 0.0 <= args.prune_fraction < 1.0:
        raise ValueError("prune-fraction必须在[0,1)内。")
    root = _assert_workspace_path(args.output_root)
    edges_dir = _assert_workspace_path(args.edges_dir or root / "edges")
    nodes_dir = _assert_workspace_path(args.nodes_dir or root / "nodes")
    manifest_path = _assert_workspace_path(
        args.manifest or root / "network_manifest.json"
    )
    methodology_path = _assert_workspace_path(
        args.methodology or root / "network_methodology.json"
    )
    temp_dir = _assert_workspace_path(args.temp_dir)
    daily_glob = args.daily_dir / "*.parquet"
    files = sorted(args.daily_dir.glob("*.parquet"))
    base_pattern = Path(args.base_cache_glob)
    base_files = sorted(base_pattern.parent.glob(base_pattern.name))
    base_extra_pattern = (
        Path(args.base_cache_extra_glob)
        if args.base_cache_extra_glob is not None
        else None
    )
    base_extra_files = (
        sorted(base_extra_pattern.parent.glob(base_extra_pattern.name))
        if base_extra_pattern is not None
        else []
    )
    if not files:
        raise FileNotFoundError(f"没有试算日值文件：{daily_glob}")
    if args.output_mode == "compact" and not base_files:
        raise FileNotFoundError(f"没有基础日值文件：{base_pattern}")
    if args.output_mode == "compact" and base_extra_pattern is not None and not base_extra_files:
        raise FileNotFoundError(f"没有近期基础日值文件：{base_extra_pattern}")
    daily_source_fingerprint = _source_fingerprint(files)
    all_base_files = [*base_files, *base_extra_files]
    base_source_fingerprint = (
        _source_fingerprint(all_base_files)
        if args.output_mode == "compact"
        else None
    )
    if args.output_mode == "legacy":
        edges_dir.mkdir(parents=True, exist_ok=True)
    nodes_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    if args.output_mode == "compact":
        (
            dates,
            codes,
            positive,
            negative,
            daily_return,
            negative_jump20,
            nonjump20,
        ) = read_compact_inputs(
            daily_glob,
            base_pattern,
            base_extra_pattern,
            args.start,
            args.end,
            temp_dir,
            args.memory_limit,
            args.threads,
        )
    else:
        dates, codes, positive, negative = read_event_inputs(
            daily_glob,
            args.start,
            args.end,
            temp_dir,
            args.memory_limit,
            args.threads,
        )
    positions = month_end_positions(dates, args.window)
    if not len(positions):
        raise ValueError(
            f"有效日期只有{len(dates)}天，不能生成{args.window}日网络。"
        )
    snapshots = []
    total_edges = 0
    total_nodes = 0
    for number, position in enumerate(positions, start=1):
        date = dates[position]
        label = date.strftime("%Y%m%d")
        edge_path = edges_dir / f"{label}.parquet"
        node_path = nodes_dir / f"{label}.parquet"
        if args.output_mode == "compact":
            if node_path.exists() and not args.force:
                current_columns = set(pq.read_schema(node_path).names)
                required_columns = set(COMPACT_NODE_COLUMNS + COMPACT_RESUME_COLUMNS)
                if required_columns.issubset(current_columns):
                    node_marker = pd.read_parquet(
                        node_path,
                        columns=[
                            "snapshot_date",
                            "network_version",
                            "network_window",
                            "prune_fraction",
                            "edge_count",
                            *COMPACT_RESUME_COLUMNS,
                        ],
                    )
                    current = bool(
                        not node_marker.empty
                        and pd.to_datetime(node_marker["snapshot_date"])
                        .dt.normalize()
                        .eq(date)
                        .all()
                        and node_marker["network_version"]
                        .eq(NETWORK_VERSION)
                        .all()
                        and node_marker["network_window"].eq(args.window).all()
                        and np.isclose(
                            node_marker["prune_fraction"].to_numpy(float),
                            args.prune_fraction,
                        ).all()
                        and node_marker["daily_source_fingerprint"]
                        .eq(daily_source_fingerprint)
                        .all()
                        and node_marker["base_source_fingerprint"]
                        .eq(base_source_fingerprint)
                        .all()
                    )
                    if current:
                        retained_edges = int(node_marker["edge_count"].sum())
                        total_edges += retained_edges
                        total_nodes += len(node_marker)
                        snapshots.append(
                            {
                                "snapshot_date": str(date.date()),
                                "status": "reused",
                                "retained_edges": retained_edges,
                                "nodes": len(node_marker),
                            }
                        )
                        continue
            left = position - args.window + 1
            nodes = compute_compact_network_snapshot_from_events(
                positive[left : position + 1],
                negative[left : position + 1],
                codes,
                date,
                args.prune_fraction,
                args.window,
                daily_return[position],
                negative_jump20[position],
                nonjump20[position],
            )
            nodes["daily_source_fingerprint"] = daily_source_fingerprint
            nodes["base_source_fingerprint"] = base_source_fingerprint
            _atomic_write_parquet(nodes, node_path)
            retained_edges = int(nodes["edge_count"].sum())
            total_edges += retained_edges
            total_nodes += len(nodes)
            snapshots.append(
                {
                    "snapshot_date": str(date.date()),
                    "status": "completed",
                    "retained_edges": retained_edges,
                    "nodes": len(nodes),
                }
            )
            print(
                f"网络月份 {number}/{len(positions)} {date.date()}："
                f"关系计算后立即汇总，节点{len(nodes):,}",
                flush=True,
            )
            continue
        if edge_path.exists() and node_path.exists() and not args.force:
            node_marker = pd.read_parquet(
                node_path,
                columns=["network_version", "network_window", "prune_fraction"],
            )
            current = bool(
                not node_marker.empty
                and node_marker["network_version"].eq(NETWORK_VERSION).all()
                and node_marker["network_window"].eq(args.window).all()
                and np.isclose(
                    node_marker["prune_fraction"].to_numpy(float),
                    args.prune_fraction,
                ).all()
            )
            if current:
                total_edges += int(pq.ParquetFile(edge_path).metadata.num_rows)
                total_nodes += len(node_marker)
                snapshots.append(
                    {"snapshot_date": str(date.date()), "status": "reused"}
                )
                continue
        left = position - args.window + 1
        edges, nodes = compute_network_snapshot_from_events(
            positive[left : position + 1],
            negative[left : position + 1],
            codes,
            date,
            args.prune_fraction,
            args.window,
        )
        _atomic_write_parquet(edges, edge_path)
        _atomic_write_parquet(nodes, node_path)
        total_edges += len(edges)
        total_nodes += len(nodes)
        snapshots.append(
            {
                "snapshot_date": str(date.date()),
                "status": "completed",
                "edges": len(edges),
                "nodes": len(nodes),
            }
        )
        print(
            f"网络快照 {number}/{len(positions)} {date.date()}："
            f"保留边 {len(edges):,}",
            flush=True,
        )

    elapsed = time.perf_counter() - started
    manifest = {
        "network_version": NETWORK_VERSION,
        "formula_mode": "trial",
        "formula_label": "试算",
        "start": args.start,
        "end": args.end,
        "window": args.window,
        "prune_fraction": args.prune_fraction,
        "output_mode": args.output_mode,
        "source_file_count": len(files),
        "source_fingerprint": daily_source_fingerprint,
        "base_source_file_count": (
            len(all_base_files) if args.output_mode == "compact" else 0
        ),
        "base_extra_source_file_count": (
            len(base_extra_files) if args.output_mode == "compact" else 0
        ),
        "base_source_fingerprint": (
            base_source_fingerprint
            if args.output_mode == "compact"
            else None
        ),
        "date_count": len(dates),
        "stock_count": len(codes),
        "positive_jump_events": int(positive.nnz),
        "negative_jump_events": int(negative.nnz),
        "snapshot_count": len(positions),
        "edge_rows": total_edges,
        "edge_files_written": len(positions) if args.output_mode == "legacy" else 0,
        "node_rows": total_nodes,
        "elapsed_seconds": elapsed,
        "threads": args.threads,
        "snapshots": snapshots,
    }
    methodology = {
        "formula_mode": "trial",
        "formula_label": "试算",
        "parameters": {
            "history_trading_days": args.window,
            "jump_test": "5分钟Jiang-Oomen JS",
            "jump_threshold": "正负方向各按alpha=1%的单侧正态阈值",
            "cojump": "股票i在t日跳跃，股票j在已有历史范围t-1至t+1日同向跳跃，记一次",
            "association": "cojump_count(i,j)/jump_count(i)，有向",
            "pruning": "对所有正关联按关联度、共同次数、代码固定顺序排序，只保留最高50%",
            "snapshot": "每个自然月最后一个有效交易日",
            "future_data": "不读取快照日之后的t+1事件",
        },
        "outputs": {
            "edges": EDGE_COLUMNS if args.output_mode == "legacy" else [],
            "nodes": (
                COMPACT_NODE_COLUMNS + COMPACT_RESUME_COLUMNS
                if args.output_mode == "compact"
                else NODE_COLUMNS
            ),
            "compact_values": (
                COMPACT_VALUE_COLUMNS if args.output_mode == "compact" else []
            ),
            "node_edge_count": "直接相连的保留有向边数",
            "node_association_sum": "保留有向边的关联度之和",
        },
        "raw_minute_data_read_again": False,
        "created_at": pd.Timestamp.now().isoformat(),
    }
    _atomic_write_json(manifest, manifest_path)
    _atomic_write_json(methodology, methodology_path)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
