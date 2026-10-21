# -*- coding: utf-8 -*-
"""独立回测十月至十二月十四个B类试算因子。"""

from __future__ import annotations

import argparse
import gc
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
from scipy.stats import norm

import build_october_december_trial_daily as trial_daily
import build_october_december_trial_network as trial_network
import duckdb_factor_store as factor_store
import run_january_minute_backtest as january
import run_september_december_backtest as sepdec


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "october_december_trial_results" / "final"

NETWORK_CODES = {
    "J10_D18_JUMP_DEGREE_CENTRALITY",
    "J11_D21_COJUMP_RELATIVE_MOMENTUM",
    "J11_D29_JUMP_NETWORK_DEGREE",
    "J12_D18_COJUMP_NONJUMP_MOM",
}

FACTOR_META = {
    "J10_D10_T_ACTIVE_SHARE": {
        "name": "T分布主动占比",
        "calendar_month": 10,
        "calendar_day": 10,
        "source_frequency": "1分钟",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "trial_formula",
        "note": "df=5；1分钟log收益除以日内样本标准差，按amount加权t分布累计概率。",
    },
    "J10_D18_JUMP_DEGREE_CENTRALITY": {
        "name": "基于跳跃频率关联的点度中心性",
        "calendar_month": 10,
        "calendar_day": 18,
        "source_frequency": "5分钟与全市场日事件",
        "signal_frequency": "monthly",
        "expected_direction": "positive",
        "strict_status": "trial_formula",
        "note": "月末252日同向跳跃网络保留最高50%有向关系，因子为保留边数。",
    },
    "J10_D20_CONSISTENT_TRADING": {
        "name": "一致交易",
        "calendar_month": 10,
        "calendar_day": 20,
        "source_frequency": "5分钟",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "trial_formula",
        "note": "alpha=0.10，实体一致5分钟成交量占比取最近20个有效日均值。",
    },
    "J10_D25_AVG_POS_JUMP_RET": {
        "name": "平均日内正跳跃收益",
        "calendar_month": 10,
        "calendar_day": 25,
        "source_frequency": "5分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "trial_formula",
        "note": "JS正跳收益取最近20个有效日、半衰期10日指数加权均值。",
    },
    "J10_D26_CUM_JUMP_ABS_RET": {
        "name": "累计跳跃绝对收益",
        "calendar_month": 10,
        "calendar_day": 26,
        "source_frequency": "日线与5分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "trial_formula",
        "note": "正负方向各按alpha=1%单侧检验；最近20个有效日计算exp(sum(abs(日log收益)*跳跃标记))-1。",
    },
    "J11_D04_ADJ_ABSRET_LAG_AMOUNT_CORR": {
        "name": "绝对收益与调整后滞后成交量相关性",
        "calendar_month": 11,
        "calendar_day": 4,
        "source_frequency": "1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "trial_formula",
        "note": "成交额按前20有效日同分钟总体标准化，corr(abs(ret_t),adjAmount_t-1)日值取最近10个有效日均值。",
    },
    "J11_D20_CONSISTENT_BUYING": {
        "name": "一致买入交易",
        "calendar_month": 11,
        "calendar_day": 20,
        "source_frequency": "5分钟",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "trial_formula",
        "note": "alpha=0.10且close>open，成交量占比取最近20个有效日均值。",
    },
    "J11_D21_COJUMP_RELATIVE_MOMENTUM": {
        "name": "跳跃关联相对动量",
        "calendar_month": 11,
        "calendar_day": 21,
        "source_frequency": "5分钟网络与日线",
        "signal_frequency": "monthly",
        "expected_direction": "positive",
        "strict_status": "trial_formula",
        "note": "月末用保留网络Corr加权关联股当日日收益，并对自身20日收益做截面含常数OLS残差。",
    },
    "J11_D25_POS_JUMP_RETURN_STD": {
        "name": "正跳跃收益标准差",
        "calendar_month": 11,
        "calendar_day": 25,
        "source_frequency": "5分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "trial_formula",
        "note": "JS正跳收益最近20个有效日、半衰期10日加权总体标准差。",
    },
    "J11_D29_JUMP_NETWORK_DEGREE": {
        "name": "跳跃网络关联度",
        "calendar_month": 11,
        "calendar_day": 29,
        "source_frequency": "5分钟与全市场日事件",
        "signal_frequency": "monthly",
        "expected_direction": "positive",
        "strict_status": "trial_formula",
        "note": "月末252日网络保留最高50%关系，因子为保留边Corr之和；节点文件另存边数。",
    },
    "J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR": {
        "name": "滞后绝对收益与调整后成交量相关性",
        "calendar_month": 12,
        "calendar_day": 4,
        "source_frequency": "1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "trial_formula",
        "note": "复用v3的corr(abs(ret_t-1),adjAmount_t)日值，取最近10个有效日均值。",
    },
    "J12_D11_CONSISTENT_SELL": {
        "name": "一致卖出交易",
        "calendar_month": 12,
        "calendar_day": 11,
        "source_frequency": "5分钟",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "trial_formula",
        "note": "alpha=0.10且close<open，成交量占比取最近20个有效日均值。",
    },
    "J12_D18_COJUMP_NONJUMP_MOM": {
        "name": "跳跃关联非正跳跃相对动量",
        "calendar_month": 12,
        "calendar_day": 18,
        "source_frequency": "5分钟网络与日线",
        "signal_frequency": "monthly",
        "expected_direction": "positive",
        "strict_status": "trial_formula",
        "note": "Corr分别加权关联股20日负跳与非跳收益，对自身20日收益做截面OLS残差后相加。",
    },
    "J12_D25_TURNOVER_POS_JUMP": {
        "name": "平均换手日内正跳跃收益",
        "calendar_month": 12,
        "calendar_day": 25,
        "source_frequency": "5分钟与日线",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "trial_formula",
        "note": "JS正跳收益乘volume/float_shares，再取20个有效日、半衰期10日加权均值。",
    },
}

BASE_COLUMNS = [
    "open",
    "close",
    "volume",
    "amount",
    "float_shares",
    "qfq_ratio",
    "vwap_outside_range_1pct",
]

TRIAL_COLUMNS = [
    "j10_d10_t_active_share_daily_trial",
    "consistent_trading_share_daily_trial",
    "consistent_buying_share_daily_trial",
    "consistent_selling_share_daily_trial",
    "j11_d04_adj_absret_lag_amount_corr_daily_trial",
    "jiang_oomen_js_daily_trial",
    "positive_jump_return_daily_trial",
    "negative_jump_return_daily_trial",
    "nonjump_return_daily_trial",
]

V3_COLUMNS = ["adj_lag_absret_amount_corr_daily"]


def _existing_paths(pattern: Path) -> list[Path]:
    if pattern.is_file():
        return [pattern]
    return sorted(pattern.parent.glob(pattern.name))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["base_daily"],
    )
    parser.add_argument(
        "--trial-cache-dir", type=Path, default=trial_daily.DEFAULT_OUTPUT_DIR
    )
    parser.add_argument(
        "--minute-v3-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["minute_extra_daily"],
    )
    parser.add_argument(
        "--network-root", type=Path, default=trial_network.DEFAULT_ROOT
    )
    parser.add_argument("--database", type=Path, default=factor_store.DEFAULT_DATABASE)
    parser.add_argument(
        "--duckdb-temp-dir", type=Path, default=factor_store.DEFAULT_TEMP_DIR
    )
    parser.add_argument("--duckdb-threads", type=int, default=8)
    parser.add_argument("--duckdb-memory-limit", default="8GB")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--qlib-dir", type=Path, default=Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin"))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--min-stocks", type=int, default=50)
    parser.add_argument("--save-factor-values", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true")
    parser.add_argument("--allow-missing-network", action="store_true")
    return parser.parse_args(argv)


def _compact_panel(panel: pd.DataFrame) -> pd.DataFrame:
    for column in panel.select_dtypes(include=["number"]).columns:
        panel[column] = panel[column].astype(np.float32)
    panel["date"] = pd.to_datetime(panel["date"])
    panel["code"] = panel["code"].astype("string")
    return panel


def load_inputs_with_duckdb(
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | list[Path], float]:
    base_glob = Path(args.base_cache_glob)
    trial_glob = Path(args.trial_cache_dir) / "*.parquet"
    v3_glob = Path(args.minute_v3_glob)
    for label, pattern in (
        ("基础日值", base_glob),
        ("B类试算日值", trial_glob),
        ("v3分钟日值", v3_glob),
    ):
        if not _existing_paths(pattern):
            raise FileNotFoundError(f"没有{label}文件：{pattern}")
    base_projection = ", ".join(f'b."{column}"' for column in BASE_COLUMNS)
    trial_projection = ", ".join(f't."{column}"' for column in TRIAL_COLUMNS)
    v3_projection = ", ".join(f'v."{column}"' for column in V3_COLUMNS)
    settings = factor_store.DuckDBSettings(
        database_path=Path(args.database),
        temp_dir=Path(args.duckdb_temp_dir),
        threads=args.duckdb_threads,
        memory_limit=args.duckdb_memory_limit,
    )
    started = time.perf_counter()
    with factor_store.connect(settings) as connection:
        panel = connection.execute(
            f"""
            SELECT
                b.date,
                lower(b.code) AS code,
                {base_projection},
                {trial_projection},
                {v3_projection}
            FROM read_parquet(
                '{trial_daily._sql_path(base_glob)}', union_by_name = true
            ) AS b
            INNER JOIN read_parquet(
                '{trial_daily._sql_path(trial_glob)}', union_by_name = true
            ) AS t
              ON b.date = t.date
             AND lower(b.code) = lower(t.code)
            INNER JOIN read_parquet(
                '{trial_daily._sql_path(v3_glob)}', union_by_name = true
            ) AS v
              ON b.date = v.date
             AND lower(b.code) = lower(v.code)
            WHERE b.date BETWEEN ? AND ?
            ORDER BY b.date, lower(b.code)
            """,
            [pd.Timestamp(args.start), pd.Timestamp(args.end)],
        ).fetchdf()
    if panel.empty:
        raise ValueError("三张日值表没有可合并记录。")
    if panel.duplicated(["date", "code"]).any():
        raise ValueError("合并后出现重复日期和股票代码。")

    node_files = sorted((Path(args.network_root) / "nodes").glob("*.parquet"))
    edge_files = sorted((Path(args.network_root) / "edges").glob("*.parquet"))
    compact_network = bool(
        node_files
        and set(trial_network.COMPACT_VALUE_COLUMNS).issubset(
            set(pd.read_parquet(node_files[0]).columns)
        )
    )
    if not node_files or (not compact_network and not edge_files):
        if not args.allow_missing_network:
            raise FileNotFoundError(
                f"缺少网络小表：{args.network_root}。请先运行build_october_december_trial_network.py。"
            )
        nodes = pd.DataFrame(columns=trial_network.NODE_COLUMNS)
        edges = pd.DataFrame(columns=trial_network.EDGE_COLUMNS)
    else:
        node_columns = [
            "snapshot_date",
            "code",
            "edge_count",
            "association_sum",
        ]
        if compact_network:
            node_columns.extend(trial_network.COMPACT_VALUE_COLUMNS)
        nodes = pd.read_parquet(
            [str(path) for path in node_files],
            columns=node_columns,
        )
        nodes["snapshot_date"] = pd.to_datetime(nodes["snapshot_date"])
        lower = pd.Timestamp(args.start)
        upper = pd.Timestamp(args.end)
        nodes = nodes[nodes["snapshot_date"].between(lower, upper)].copy()
        edges = (
            []
            if compact_network
            else [
                path
                for path in edge_files
                if lower
                <= pd.to_datetime(path.stem, format="%Y%m%d")
                <= upper
            ]
        )
    return _compact_panel(panel), nodes, edges, time.perf_counter() - started


def rolling_valid_sum_mean(
    values: np.ndarray, window: int
) -> tuple[np.ndarray, np.ndarray]:
    source = np.asarray(values, dtype=np.float64)
    sums = np.full(source.shape, np.nan, dtype=np.float32)
    means = np.full(source.shape, np.nan, dtype=np.float32)
    for column in range(source.shape[1]):
        positions = np.flatnonzero(np.isfinite(source[:, column]))
        if len(positions) < window:
            continue
        observations = source[positions, column]
        cumulative = np.r_[0.0, np.cumsum(observations, dtype=np.float64)]
        rolling_sum = cumulative[window:] - cumulative[:-window]
        targets = positions[window - 1 :]
        sums[targets, column] = rolling_sum.astype(np.float32)
        means[targets, column] = (rolling_sum / window).astype(np.float32)
    return sums, means


def rolling_valid_weighted_mean_std(
    values: np.ndarray, window: int, half_life: float
) -> tuple[np.ndarray, np.ndarray]:
    source = np.asarray(values, dtype=np.float64)
    means = np.full(source.shape, np.nan, dtype=np.float32)
    stds = np.full(source.shape, np.nan, dtype=np.float32)
    weights = 0.5 ** (
        (window - 1 - np.arange(window, dtype=np.float64)) / half_life
    )
    weight_sum = weights.sum()
    for column in range(source.shape[1]):
        positions = np.flatnonzero(np.isfinite(source[:, column]))
        if len(positions) < window:
            continue
        observations = source[positions, column]
        windows = np.lib.stride_tricks.sliding_window_view(observations, window)
        current_mean = windows @ weights / weight_sum
        current_square = (windows * windows) @ weights / weight_sum
        current_std = np.sqrt(np.maximum(current_square - current_mean**2, 0.0))
        targets = positions[window - 1 :]
        means[targets, column] = current_mean.astype(np.float32)
        stds[targets, column] = current_std.astype(np.float32)
    return means, stds


def _cross_sectional_ols_residual(
    dependent: np.ndarray,
    explanatory: np.ndarray,
    membership: np.ndarray,
    min_stocks: int,
) -> np.ndarray:
    result = np.full(dependent.shape, np.nan, dtype=np.float32)
    valid = membership & np.isfinite(dependent) & np.isfinite(explanatory)
    if int(valid.sum()) < min_stocks:
        return result
    x = explanatory[valid].astype(np.float64)
    y = dependent[valid].astype(np.float64)
    design = np.column_stack([np.ones(len(x)), x])
    coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
    result[valid] = (y - design @ coefficients).astype(np.float32)
    return result


def _weighted_peer_values(
    edge_rows: np.ndarray,
    edge_columns: np.ndarray,
    weights: np.ndarray,
    target_values: np.ndarray,
    stock_count: int,
) -> np.ndarray:
    result = np.full(stock_count, np.nan, dtype=np.float32)
    good = (
        np.isfinite(weights)
        & (weights > 0)
        & np.isfinite(target_values[edge_columns])
    )
    if not good.any():
        return result
    rows = edge_rows[good]
    columns = edge_columns[good]
    current_weights = weights[good]
    numerator = np.bincount(
        rows,
        weights=current_weights * target_values[columns],
        minlength=stock_count,
    )
    denominator = np.bincount(
        rows, weights=current_weights, minlength=stock_count
    )
    np.divide(numerator, denominator, out=result, where=denominator > 0)
    return result


def build_network_factor_matrices(
    nodes: pd.DataFrame,
    edges: pd.DataFrame | Sequence[Path],
    dates: pd.DatetimeIndex,
    stocks: list[str],
    membership: np.ndarray,
    daily_return: np.ndarray,
    own_return20: np.ndarray,
    negative_jump20: np.ndarray,
    nonjump20: np.ndarray,
    min_stocks: int,
) -> dict[str, np.ndarray]:
    shape = (len(dates), len(stocks))
    if nodes.empty:
        return {}
    factors = {
        code: np.full(shape, np.nan, dtype=np.float32) for code in NETWORK_CODES
    }
    date_map = {date: index for index, date in enumerate(dates)}
    stock_map = {code.lower(): index for index, code in enumerate(stocks)}
    edge_frame = edges if isinstance(edges, pd.DataFrame) else None
    edge_files = (
        {}
        if edge_frame is not None
        else {
            pd.to_datetime(Path(path).stem, format="%Y%m%d").normalize(): Path(path)
            for path in edges
        }
    )
    for snapshot_date, node_part in nodes.groupby("snapshot_date", sort=True):
        date = pd.Timestamp(snapshot_date).normalize()
        row_index = date_map.get(date)
        if row_index is None:
            continue
        node_columns = node_part["code"].astype(str).str.lower().map(stock_map)
        node_good = node_columns.notna().to_numpy()
        mapped = node_columns[node_good].to_numpy(dtype=np.int32)
        factors["J10_D18_JUMP_DEGREE_CENTRALITY"][row_index, mapped] = (
            node_part.loc[node_good, "edge_count"].to_numpy(dtype=np.float32)
        )
        factors["J11_D29_JUMP_NETWORK_DEGREE"][row_index, mapped] = (
            node_part.loc[node_good, "association_sum"].to_numpy(dtype=np.float32)
        )

        if set(trial_network.COMPACT_VALUE_COLUMNS).issubset(node_part.columns):
            peer_return = np.full(len(stocks), np.nan, dtype=np.float32)
            peer_negative = np.full(len(stocks), np.nan, dtype=np.float32)
            peer_nonjump = np.full(len(stocks), np.nan, dtype=np.float32)
            peer_return[mapped] = node_part.loc[
                node_good, "peer_daily_return"
            ].to_numpy(dtype=np.float32)
            peer_negative[mapped] = node_part.loc[
                node_good, "peer_negative_jump20"
            ].to_numpy(dtype=np.float32)
            peer_nonjump[mapped] = node_part.loc[
                node_good, "peer_nonjump20"
            ].to_numpy(dtype=np.float32)
            factors["J11_D21_COJUMP_RELATIVE_MOMENTUM"][row_index] = (
                _cross_sectional_ols_residual(
                    peer_return,
                    own_return20[row_index],
                    membership[row_index],
                    min_stocks,
                )
            )
            negative_residual = _cross_sectional_ols_residual(
                peer_negative,
                own_return20[row_index],
                membership[row_index],
                min_stocks,
            )
            nonjump_residual = _cross_sectional_ols_residual(
                peer_nonjump,
                own_return20[row_index],
                membership[row_index],
                min_stocks,
            )
            factors["J12_D18_COJUMP_NONJUMP_MOM"][row_index] = (
                negative_residual + nonjump_residual
            ).astype(np.float32)
            continue

        if edge_frame is not None:
            if edge_frame.empty:
                continue
            edge_part = edge_frame.loc[edge_frame["snapshot_date"].eq(date)]
        else:
            edge_path = edge_files.get(date)
            if edge_path is None:
                continue
            edge_part = pd.read_parquet(
                edge_path,
                columns=[
                    "snapshot_date",
                    "source_code",
                    "target_code",
                    "association",
                ],
            )
            edge_part["snapshot_date"] = pd.to_datetime(edge_part["snapshot_date"])
        source = edge_part["source_code"].astype(str).str.lower().map(stock_map)
        target = edge_part["target_code"].astype(str).str.lower().map(stock_map)
        edge_good = source.notna() & target.notna()
        if not edge_good.any():
            continue
        source_index = source[edge_good].to_numpy(dtype=np.int32)
        target_index = target[edge_good].to_numpy(dtype=np.int32)
        weights = edge_part.loc[edge_good, "association"].to_numpy(dtype=np.float64)

        peer_return = _weighted_peer_values(
            source_index,
            target_index,
            weights,
            daily_return[row_index],
            len(stocks),
        )
        factors["J11_D21_COJUMP_RELATIVE_MOMENTUM"][row_index] = (
            _cross_sectional_ols_residual(
                peer_return,
                own_return20[row_index],
                membership[row_index],
                min_stocks,
            )
        )
        peer_negative = _weighted_peer_values(
            source_index,
            target_index,
            weights,
            negative_jump20[row_index],
            len(stocks),
        )
        peer_nonjump = _weighted_peer_values(
            source_index,
            target_index,
            weights,
            nonjump20[row_index],
            len(stocks),
        )
        negative_residual = _cross_sectional_ols_residual(
            peer_negative,
            own_return20[row_index],
            membership[row_index],
            min_stocks,
        )
        nonjump_residual = _cross_sectional_ols_residual(
            peer_nonjump,
            own_return20[row_index],
            membership[row_index],
            min_stocks,
        )
        factors["J12_D18_COJUMP_NONJUMP_MOM"][row_index] = (
            negative_residual + nonjump_residual
        ).astype(np.float32)
    return factors


def build_factor_matrices(
    panel: pd.DataFrame,
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    membership: np.ndarray,
    settings: january.Settings,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    def matrix(column: str) -> np.ndarray:
        return january.panel_to_matrix(
            panel, column, dates, stocks, date_map, stock_map, dtype=np.float32
        )

    arrays = {
        "open": matrix("open"),
        "close": matrix("close"),
        "volume": matrix("volume"),
        "amount": matrix("amount"),
        "float_shares": matrix("float_shares"),
        "qfq_ratio": matrix("qfq_ratio"),
    }
    arrays["raw_close"] = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
    np.divide(
        arrays["close"],
        arrays["qfq_ratio"],
        out=arrays["raw_close"],
        where=np.isfinite(arrays["qfq_ratio"]) & (arrays["qfq_ratio"] > 0),
    )
    arrays["price_amount_bad"] = january.build_price_amount_bad_matrix(
        panel, dates, stocks, date_map, stock_map
    )
    arrays["open_exit_mark"], arrays["close_exit_mark"] = (
        january.build_exit_price_matrices(arrays["open"], arrays["close"])
    )

    factors: dict[str, np.ndarray] = {
        "J10_D10_T_ACTIVE_SHARE": matrix(
            "j10_d10_t_active_share_daily_trial"
        )
    }
    _, factors["J10_D20_CONSISTENT_TRADING"] = rolling_valid_sum_mean(
        matrix("consistent_trading_share_daily_trial"), 20
    )
    positive_jump = matrix("positive_jump_return_daily_trial")
    positive_mean, positive_std = rolling_valid_weighted_mean_std(
        positive_jump, 20, 10.0
    )
    factors["J10_D25_AVG_POS_JUMP_RET"] = positive_mean
    factors["J11_D25_POS_JUMP_RETURN_STD"] = positive_std

    daily_return = sepdec.returns_from_previous_valid(arrays["close"])
    daily_log_return = np.full(daily_return.shape, np.nan, dtype=np.float32)
    valid_return = np.isfinite(daily_return) & (daily_return > -1.0)
    daily_log_return[valid_return] = np.log1p(daily_return[valid_return])
    js = matrix("jiang_oomen_js_daily_trial")
    critical = norm.ppf(1.0 - trial_daily.JS_ALPHA)
    jump_component = np.full(js.shape, np.nan, dtype=np.float32)
    valid_jump = np.isfinite(js) & np.isfinite(daily_log_return)
    jump_component[valid_jump] = (
        np.abs(daily_log_return[valid_jump])
        * (np.abs(js[valid_jump]) > critical)
    )
    jump_sum, _ = rolling_valid_sum_mean(jump_component, 20)
    jar = np.full(jump_sum.shape, np.nan, dtype=np.float32)
    finite_jump_sum = np.isfinite(jump_sum)
    jar[finite_jump_sum] = np.expm1(jump_sum[finite_jump_sum])
    factors["J10_D26_CUM_JUMP_ABS_RET"] = jar

    _, factors["J11_D04_ADJ_ABSRET_LAG_AMOUNT_CORR"] = (
        rolling_valid_sum_mean(
            matrix("j11_d04_adj_absret_lag_amount_corr_daily_trial"), 10
        )
    )
    _, factors["J11_D20_CONSISTENT_BUYING"] = rolling_valid_sum_mean(
        matrix("consistent_buying_share_daily_trial"), 20
    )
    _, factors["J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR"] = rolling_valid_sum_mean(
        matrix("adj_lag_absret_amount_corr_daily"), 10
    )
    _, factors["J12_D11_CONSISTENT_SELL"] = rolling_valid_sum_mean(
        matrix("consistent_selling_share_daily_trial"), 20
    )
    turnover = sepdec.compute_daily_turnover(
        arrays["volume"], arrays["float_shares"]
    )
    turnover_jump = positive_jump * turnover
    turnover_jump_mean, _ = rolling_valid_weighted_mean_std(
        turnover_jump, 20, 10.0
    )
    factors["J12_D25_TURNOVER_POS_JUMP"] = turnover_jump_mean

    own_return20 = sepdec._rolling_valid_compound_return(daily_return, 20)
    negative_jump20, _ = rolling_valid_sum_mean(
        matrix("negative_jump_return_daily_trial"), 20
    )
    nonjump20, _ = rolling_valid_sum_mean(
        matrix("nonjump_return_daily_trial"), 20
    )
    factors.update(
        build_network_factor_matrices(
            nodes,
            edges,
            dates,
            stocks,
            membership,
            daily_return,
            own_return20,
            negative_jump20,
            nonjump20,
            settings.min_stocks,
        )
    )
    return {
        code: np.asarray(values, dtype=np.float32)
        for code, values in factors.items()
    }, arrays


def build_status_frame(factors: dict[str, np.ndarray]) -> pd.DataFrame:
    rows = []
    for code, meta in FACTOR_META.items():
        values = factors.get(code)
        rows.append(
            {
                "factor": code,
                **meta,
                "formula_mode": "trial",
                "formula_label": "试算",
                "calculated": values is not None,
                "finite_values": int(np.isfinite(values).sum()) if values is not None else 0,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["calendar_month", "calendar_day", "factor"]
    )


def _mark_trial(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["formula_mode"] = "trial"
    result["formula_label"] = "试算"
    return result


def save_trial_factor_values(
    factors: dict[str, np.ndarray],
    dates: pd.DatetimeIndex,
    stocks: list[str],
    output_dir: Path,
    directory_name: str,
) -> None:
    destination = output_dir / directory_name
    destination.mkdir(parents=True, exist_ok=True)
    stock_array = np.asarray(stocks, dtype=object)
    for name, values in factors.items():
        rows, columns = np.nonzero(np.isfinite(values))
        frame = pd.DataFrame(
            {
                "date": dates[rows],
                "code": stock_array[columns],
                "value": values[rows, columns],
                "formula_mode": "trial",
                "formula_label": "试算",
            }
        )
        path = destination / f"{name}.parquet"
        temporary = path.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, compression="zstd", index=False)
        temporary.replace(path)


def build_methodology(
    args: argparse.Namespace,
    settings: january.Settings,
    read_seconds: float,
    calculated_codes: Sequence[str],
) -> dict[str, object]:
    return {
        "formula_mode": "trial",
        "formula_label": "试算",
        "all_outputs_are_trial": True,
        "settings": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(settings).items()
        },
        "duckdb": {
            "database": str(args.database),
            "temp_dir": str(args.duckdb_temp_dir),
            "threads": args.duckdb_threads,
            "memory_limit": args.duckdb_memory_limit,
            "read_seconds": read_seconds,
            "copied_source_data": False,
        },
        "factor_meta": FACTOR_META,
        "calculated_codes": list(calculated_codes),
        "parameters": {
            "daily_cache": trial_daily.DAILY_PARAMETERS,
            "rolling": {
                "consistent_days": 20,
                "adjusted_corr_days": 10,
                "positive_jump_days": 20,
                "positive_jump_half_life": 10,
                "weighted_std": "归一化指数权重下的总体标准差",
                "jar_days": 20,
                "jar_jump": "abs(JS)>Phi^-1(0.99)，正负方向分别检验",
            },
            "network": {
                "history_days": 252,
                "cojump_tolerance": "t-1至t+1交易日，同方向",
                "association": "共同跳跃数/源股票跳跃数",
                "pruning": "全网络保留关联度最高50%的有向关系",
                "J10_D18": "保留边数",
                "J11_D29": "保留边关联度之和；节点小表另存边数",
                "regression": "月末历史成分范围截面含常数OLS",
            },
            "J12_D04": "直接复用v3反向滞后日值，不再次读取分钟数据",
        },
        "loaded_sources": {
            "base_daily": BASE_COLUMNS,
            "october_december_trial_daily": TRIAL_COLUMNS,
            "minute_extra_v3": V3_COLUMNS,
            "network_nodes": ["edge_count", "association_sum"],
            "network_edges": ["source_code", "target_code", "association"],
        },
        "jiang_oomen_reference": {
            "title": "Jiang and Oomen (2008), Testing for jumps when asset prices are observed with noise",
            "url": "https://doi.org/10.1016/j.jeconom.2008.04.009",
            "timing_note": "论文日检验不定位具体分钟；本次定位办法属于明确标记的试算参数。",
        },
        "backtest_conventions": {
            "universe": "历史中证800与中证1000成分并集",
            "daily_trade_frequency": "日度因子每5个交易日选一次并持有5日",
            "monthly_trade_frequency": "月末信号，下月首日开盘买入并在当月末收盘退出",
            "cost": "买入0.03%，卖出0.13%",
            "liquidity": "股价不低于3元，20日平均成交额不低于500万元",
        },
        "created_at": pd.Timestamp.now().isoformat(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = january.Settings(
        cache_dir=Path(args.trial_cache_dir),
        output_dir=Path(args.output_dir),
        qlib_dir=Path(args.qlib_dir),
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
        split_date=args.split_date,
        min_stocks=args.min_stocks,
    )
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    january.FACTOR_META = FACTOR_META
    started = time.time()

    print("[1/6] 读取基础日值、B类日值、v3日值和网络小表...", flush=True)
    panel, nodes, edges, read_seconds = load_inputs_with_duckdb(args)
    dates = january.read_calendar(settings.qlib_dir, settings.start, settings.end)
    stocks, date_map, stock_map = january.make_axes(panel, dates)
    membership_rows = january.read_membership_rows(
        settings.qlib_dir, settings.start, settings.end
    )
    membership, missing_members = january.build_membership(
        dates, stocks, membership_rows
    )
    january.atomic_write_csv(
        _mark_trial(missing_members),
        settings.output_dir / "qlib_members_without_cache.csv",
    )

    print("[2/6] 生成十四个B类试算因子...", flush=True)
    factors, arrays = build_factor_matrices(
        panel,
        nodes,
        edges,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        settings,
    )
    del panel, nodes, edges
    gc.collect()
    original_signals = january.build_original_signals(
        factors, dates, settings.backtest_start
    )

    january.atomic_write_csv(
        build_status_frame(factors), settings.output_dir / "factor_status.csv"
    )
    january.atomic_write_json(
        build_methodology(args, settings, read_seconds, sorted(factors)),
        settings.output_dir / "methodology.json",
    )
    if args.save_factor_values:
        save_trial_factor_values(
            factors, dates, stocks, settings.output_dir, "trial_factor_values"
        )
        save_trial_factor_values(
            original_signals,
            dates,
            stocks,
            settings.output_dir,
            "trial_original_signals",
        )
    if args.skip_backtest:
        print(
            f"已生成{len(factors)}个试算因子，耗时{time.time() - started:.1f}秒。",
            flush=True,
        )
        return 0

    print("[3/6] 生成历史股票范围和流动性筛选...", flush=True)
    eligible = january.build_eligible(
        arrays, membership, settings.min_price, settings.min_avg_amount
    )
    future_return = january.future_open_returns(
        arrays["open"], settings.hold_days, arrays["open_exit_mark"]
    )

    print("[4/6] 计算试算IC和分组收益...", flush=True)
    daily_frames = []
    original_frequency_frames = []
    rebalance_frames = []
    original_rebalance_frames = []
    for position, (factor_name, factor) in enumerate(factors.items(), start=1):
        daily = january.calculate_daily_ic(
            factor_name, factor, future_return, eligible, dates, settings
        )
        original_frequency = january.calculate_original_frequency_ic(
            factor_name,
            original_signals[factor_name],
            future_return,
            arrays,
            eligible,
            dates,
            settings,
        )
        daily_frames.append(daily)
        original_frequency_frames.append(original_frequency)
        rebalance_frames.append(
            january.run_five_day_backtest(
                factor_name, factor, future_return, eligible, arrays, dates, settings
            )
        )
        original_rebalance_frames.append(
            january.run_original_frequency_backtest(
                factor_name,
                original_signals[factor_name],
                future_return,
                eligible,
                arrays,
                dates,
                settings,
            )
        )
        mean_ic = daily["ic"].mean() if not daily.empty else np.nan
        print(
            f"  {position}/{len(factors)} {factor_name}：统一5日IC={mean_ic:+.4f}（试算）",
            flush=True,
        )

    daily_ic = pd.concat(daily_frames, ignore_index=True)
    original_frequency_ic = pd.concat(
        original_frequency_frames, ignore_index=True
    )
    monthly_ic = original_frequency_ic.loc[
        original_frequency_ic["signal_frequency"] == "monthly"
    ].copy()
    rebalance = pd.concat(rebalance_frames, ignore_index=True)
    original_rebalance = pd.concat(original_rebalance_frames, ignore_index=True)
    daily_summary = january.summarize_ic(
        daily_ic, "date", "unified_5d", settings.split_date
    )
    original_summary = january.summarize_original_frequency_ic(
        original_frequency_ic, settings.split_date
    )
    backtest_summary, yearly_backtest = january.summarize_backtest(
        rebalance, daily_summary, settings.split_date, settings.hold_days
    )
    original_backtest_summary, original_yearly_backtest = (
        january.summarize_original_frequency_backtest(
            original_rebalance, original_summary, settings.split_date
        )
    )

    print("[5/6] 保存全部带试算标记的结果...", flush=True)
    outputs = {
        "daily_ic.csv": daily_ic,
        "monthly_ic.csv": monthly_ic,
        "original_frequency_ic.csv": original_frequency_ic,
        "original_frequency_ic_summary.csv": original_summary,
        "ic_summary.csv": pd.concat(
            [daily_summary, original_summary], ignore_index=True
        ),
        "rebalance_returns.csv": rebalance,
        "backtest_summary.csv": backtest_summary,
        "yearly_backtest.csv": yearly_backtest,
        "original_frequency_rebalance_returns.csv": original_rebalance,
        "original_frequency_backtest_summary.csv": original_backtest_summary,
        "original_frequency_yearly_backtest.csv": original_yearly_backtest,
    }
    for name, frame in outputs.items():
        january.atomic_write_csv(_mark_trial(frame), settings.output_dir / name)

    print("[6/6] 十四项B类试算完成。", flush=True)
    print(f"总耗时{time.time() - started:.1f}秒。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
