# -*- coding: utf-8 -*-
"""按预先确定的因子方向汇总 Level2 多持有期回测。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import run_level2_short_backtests as short_backtest


IDENTITY_COLUMNS = ["factor_id", "frequency", "hold_days", "horizon"]
INTERNAL_IDENTITY_COLUMNS = [
    "factor_id",
    "frequency",
    "_hold_days_key",
    "horizon",
]
PERIOD_DATE_COLUMNS = ["signal_date", "entry_date", "exit_date"]
PERIOD_KEY_COLUMNS = INTERNAL_IDENTITY_COLUMNS + PERIOD_DATE_COLUMNS
SIDE_VALUE_COLUMNS = ["raw_return", "transaction_cost", "turnover"]
VALID_DIRECTIONS = frozenset({"high", "low"})


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_csv(path, dtype={"factor_id": "string"})


def _normalize_identity(frame: pd.DataFrame, source_name: str) -> pd.DataFrame:
    result = frame.copy()
    if "factor_id" not in result.columns:
        raise ValueError(f"{source_name} 缺少 factor_id")
    result["factor_id"] = result["factor_id"].astype("string").str.strip()
    if result["factor_id"].isna().any() or result["factor_id"].eq("").any():
        raise ValueError(f"{source_name} 的 factor_id 不能为空")
    if "frequency" not in result.columns:
        raise ValueError(f"{source_name} 缺少 frequency")
    try:
        result["frequency"] = result["frequency"].map(
            short_backtest.normalize_frequency
        )
    except ValueError as error:
        raise ValueError(f"{source_name} 的 frequency 无效") from error
    if "horizon" not in result.columns:
        if "return_horizon" not in result.columns:
            raise ValueError(f"{source_name} 缺少 horizon 或 return_horizon")
        result["horizon"] = result["return_horizon"]
    result["horizon"] = result["horizon"].astype("string").str.strip()
    if "hold_days" not in result.columns:
        result["hold_days"] = pd.NA
    result["hold_days"] = pd.to_numeric(result["hold_days"], errors="coerce").astype(
        "Int64"
    )
    result["_hold_days_key"] = result["hold_days"].fillna(-1).astype(int)
    return result


def load_direction_decisions(path: Path) -> pd.DataFrame:
    decisions = _read_csv(path)
    required = {"factor_id", "selected_direction"}
    missing = sorted(required - set(decisions.columns))
    if missing:
        raise ValueError(f"direction_decisions.csv 缺少列：{', '.join(missing)}")
    decisions["factor_id"] = decisions["factor_id"].astype("string").str.strip()
    decisions["selected_direction"] = (
        decisions["selected_direction"].astype("string").str.strip().str.lower()
    )
    duplicate = decisions["factor_id"].duplicated(keep=False)
    if duplicate.any():
        sample = decisions.loc[duplicate, "factor_id"].drop_duplicates().head(5).tolist()
        raise ValueError(f"direction_decisions.csv 的 factor_id 必须唯一，例如：{sample}")
    invalid = decisions.loc[
        ~decisions["selected_direction"].isin(VALID_DIRECTIONS),
        ["factor_id", "selected_direction"],
    ]
    if not invalid.empty:
        raise ValueError(
            "selected_direction 只能是 high 或 low，例如：\n"
            + invalid.head(5).to_string(index=False)
        )
    return decisions


def load_factor_status(path: Path) -> pd.DataFrame:
    status = _read_csv(path)
    status["factor_id"] = status["factor_id"].astype("string").str.strip()
    duplicate = status["factor_id"].duplicated(keep=False)
    if duplicate.any():
        sample = status.loc[duplicate, "factor_id"].drop_duplicates().head(5).tolist()
        raise ValueError(f"factor_status.csv 的 factor_id 必须唯一，例如：{sample}")
    return status


def _validate_factor_coverage(
    factor_ids: Iterable[str],
    table: pd.DataFrame,
    source_name: str,
) -> None:
    expected = set(factor_ids)
    available = set(table["factor_id"].dropna().astype(str))
    missing = sorted(expected - available)
    if missing:
        raise ValueError(
            f"{source_name} 缺少 {len(missing)} 个回测因子，例如：{missing[:5]}"
        )


def _validate_frequency_column(
    frame: pd.DataFrame,
    metadata: pd.DataFrame,
    source_name: str,
) -> None:
    if "frequency" not in metadata.columns:
        return
    expected = (
        frame[["factor_id", "frequency"]]
        .drop_duplicates()
        .groupby("factor_id")["frequency"]
        .agg(lambda values: set(values.dropna().map(short_backtest.normalize_frequency)))
    )
    for row in metadata[["factor_id", "frequency"]].itertuples(index=False):
        if pd.isna(row.frequency) or not str(row.frequency).strip():
            continue
        actual = short_backtest.normalize_frequency(row.frequency)
        allowed = expected.get(str(row.factor_id), set())
        if allowed and actual not in allowed:
            raise ValueError(
                f"{source_name} 中 {row.factor_id} 的 frequency={actual}，"
                f"与回测结果 {sorted(allowed)} 不一致"
            )


def pair_rebalance_sides(rebalance: pd.DataFrame) -> pd.DataFrame:
    values = _normalize_identity(rebalance, "rebalance_returns.csv")
    required = set(PERIOD_DATE_COLUMNS + ["side"] + SIDE_VALUE_COLUMNS)
    missing = sorted(required - set(values.columns))
    if missing:
        raise ValueError(f"rebalance_returns.csv 缺少列：{', '.join(missing)}")
    values["side"] = values["side"].astype("string").str.strip().str.lower()
    invalid_side = sorted(set(values["side"].dropna()) - VALID_DIRECTIONS)
    if invalid_side:
        raise ValueError(f"side 只能是 high 或 low，实际还有：{invalid_side}")
    for column in PERIOD_DATE_COLUMNS:
        values[column] = pd.to_datetime(values[column], errors="coerce").dt.normalize()
        if values[column].isna().any():
            raise ValueError(f"rebalance_returns.csv 的 {column} 有无效日期")
    for column in SIDE_VALUE_COLUMNS:
        values[column] = pd.to_numeric(values[column], errors="coerce")
        if values[column].isna().any():
            raise ValueError(f"rebalance_returns.csv 的 {column} 有空值或非数字")

    duplicate = values.duplicated(PERIOD_KEY_COLUMNS + ["side"], keep=False)
    if duplicate.any():
        sample = values.loc[duplicate, PERIOD_KEY_COLUMNS + ["side"]].head(5)
        raise ValueError("同一因子、持有期和日期的 side 必须唯一，例如：\n" + sample.to_string(index=False))
    side_counts = values.groupby(PERIOD_KEY_COLUMNS, dropna=False)["side"].nunique()
    missing_pair = side_counts[side_counts.ne(2)]
    if not missing_pair.empty:
        raise ValueError("每个回测期间都必须同时有 high 和 low 两组")

    common = PERIOD_KEY_COLUMNS + ["hold_days"]
    high = values.loc[values["side"].eq("high"), common + SIDE_VALUE_COLUMNS].rename(
        columns={column: f"high_{column}" for column in SIDE_VALUE_COLUMNS}
    )
    low = values.loc[values["side"].eq("low"), common + SIDE_VALUE_COLUMNS].rename(
        columns={column: f"low_{column}" for column in SIDE_VALUE_COLUMNS}
    )
    paired = high.merge(
        low.drop(columns="hold_days"),
        on=PERIOD_KEY_COLUMNS,
        how="inner",
        validate="one_to_one",
    )
    return paired.sort_values(PERIOD_KEY_COLUMNS).reset_index(drop=True)


def apply_selected_direction(
    paired: pd.DataFrame,
    decisions: pd.DataFrame,
    *,
    buy_cost: float,
    sell_cost: float,
) -> pd.DataFrame:
    if buy_cost < 0 or sell_cost < 0:
        raise ValueError("买入和卖出费率不能为负数")
    result = paired.merge(
        decisions[["factor_id", "selected_direction"]],
        on="factor_id",
        how="left",
        validate="many_to_one",
    )
    if result["selected_direction"].isna().any():
        missing = result.loc[result["selected_direction"].isna(), "factor_id"].unique()
        raise ValueError(f"这些因子没有方向决定：{missing[:5].tolist()}")

    # 原始 side 成本按做多计算；做空时买卖费率互换。
    total_rate = buy_cost + sell_cost
    result["high_short_transaction_cost"] = (
        2.0 * total_rate * result["high_turnover"]
        - result["high_transaction_cost"]
    )
    result["low_short_transaction_cost"] = (
        2.0 * total_rate * result["low_turnover"]
        - result["low_transaction_cost"]
    )
    minimum_short_cost = min(
        float(result["high_short_transaction_cost"].min()),
        float(result["low_short_transaction_cost"].min()),
    )
    if minimum_short_cost < -1e-10:
        raise ValueError("根据 turnover 反推的做空交易费为负，请检查 methodology.json 中的费率")
    result["high_short_transaction_cost"] = result[
        "high_short_transaction_cost"
    ].clip(lower=0.0)
    result["low_short_transaction_cost"] = result[
        "low_short_transaction_cost"
    ].clip(lower=0.0)

    choose_high = result["selected_direction"].eq("high")
    result["portfolio_rule"] = np.where(
        choose_high, "long_high_short_low", "long_low_short_high"
    )
    result["long_side"] = np.where(choose_high, "high", "low")
    result["short_side"] = np.where(choose_high, "low", "high")
    result["long_leg_raw_return"] = np.where(
        choose_high, result["high_raw_return"], result["low_raw_return"]
    )
    result["short_underlying_raw_return"] = np.where(
        choose_high, result["low_raw_return"], result["high_raw_return"]
    )
    result["short_leg_raw_return"] = -result["short_underlying_raw_return"]
    result["long_transaction_cost"] = np.where(
        choose_high,
        result["high_transaction_cost"],
        result["low_transaction_cost"],
    )
    result["short_transaction_cost"] = np.where(
        choose_high,
        result["low_short_transaction_cost"],
        result["high_short_transaction_cost"],
    )
    result["gross_return"] = (
        result["long_leg_raw_return"] + result["short_leg_raw_return"]
    )
    result["transaction_cost"] = (
        result["long_transaction_cost"] + result["short_transaction_cost"]
    )
    result["net_return"] = result["gross_return"] - result["transaction_cost"]
    result["combined_turnover"] = result["high_turnover"] + result["low_turnover"]

    result = result.sort_values(PERIOD_KEY_COLUMNS).reset_index(drop=True)
    grouped = result.groupby(INTERNAL_IDENTITY_COLUMNS, sort=False, dropna=False)
    result["gross_nav"] = grouped["gross_return"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    result["net_nav"] = grouped["net_return"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    result["gross_drawdown"] = grouped["gross_nav"].transform(
        lambda values: values / values.cummax() - 1.0
    )
    result["net_drawdown"] = grouped["net_nav"].transform(
        lambda values: values / values.cummax() - 1.0
    )
    return result


def _annual_return(final_nav: float, elapsed_years: float) -> float:
    return final_nav ** (1.0 / elapsed_years) - 1.0 if final_nav > 0 else -1.0


def summarize_period_returns(period_returns: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for keys, group in period_returns.groupby(
        INTERNAL_IDENTITY_COLUMNS, sort=True, dropna=False
    ):
        factor_id, frequency, hold_key, horizon = keys
        group = group.sort_values(PERIOD_DATE_COLUMNS)
        elapsed = max(
            (
                pd.Timestamp(group["exit_date"].max())
                - pd.Timestamp(group["entry_date"].min())
            ).days
            / 365.25,
            1.0 / 12.0,
        )
        periods_per_year = len(group) / elapsed
        gross = group["gross_return"].to_numpy(dtype=float)
        net = group["net_return"].to_numpy(dtype=float)
        gross_final = float(np.prod(1.0 + gross))
        net_final = float(np.prod(1.0 + net))
        gross_std = float(np.std(gross, ddof=1)) if len(gross) > 1 else np.nan
        net_std = float(np.std(net, ddof=1)) if len(net) > 1 else np.nan
        rows.append(
            {
                "factor_id": factor_id,
                "frequency": frequency,
                "hold_days": pd.NA if int(hold_key) < 0 else int(hold_key),
                "_hold_days_key": int(hold_key),
                "horizon": horizon,
                "periods": len(group),
                "first_entry_date": group["entry_date"].min(),
                "last_exit_date": group["exit_date"].max(),
                "periods_per_year": periods_per_year,
                "cumulative_gross_return": gross_final - 1.0,
                "cumulative_net_return": net_final - 1.0,
                "annual_gross_return": _annual_return(gross_final, elapsed),
                "annual_net_return": _annual_return(net_final, elapsed),
                "annual_gross_volatility": gross_std * math.sqrt(periods_per_year)
                if np.isfinite(gross_std)
                else np.nan,
                "annual_net_volatility": net_std * math.sqrt(periods_per_year)
                if np.isfinite(net_std)
                else np.nan,
                "sharpe": float(np.mean(net) / net_std * math.sqrt(periods_per_year))
                if np.isfinite(net_std) and net_std > 0
                else np.nan,
                "max_gross_drawdown": float(group["gross_drawdown"].min()),
                "max_drawdown": float(group["net_drawdown"].min()),
                "gross_win_rate": float(np.mean(gross > 0)),
                "win_rate": float(np.mean(net > 0)),
                "avg_transaction_cost": float(group["transaction_cost"].mean()),
                "total_transaction_cost": float(group["transaction_cost"].sum()),
                "avg_combined_turnover": float(group["combined_turnover"].mean()),
                "avg_gross_return": float(np.mean(gross)),
                "avg_net_return": float(np.mean(net)),
            }
        )
    return pd.DataFrame(rows)


def summarize_rank_ic(rank_ic: pd.DataFrame) -> pd.DataFrame:
    values = _normalize_identity(rank_ic, "rank_ic.csv")
    if "rank_ic" not in values.columns:
        raise ValueError("rank_ic.csv 缺少 rank_ic")
    values["rank_ic"] = pd.to_numeric(values["rank_ic"], errors="coerce")
    rows: list[dict[str, object]] = []
    for keys, group in values.groupby(
        INTERNAL_IDENTITY_COLUMNS, sort=True, dropna=False
    ):
        factor_id, frequency, hold_key, horizon = keys
        observations = group["rank_ic"].dropna()
        standard = float(observations.std(ddof=1)) if len(observations) > 1 else np.nan
        mean = float(observations.mean()) if len(observations) else np.nan
        rows.append(
            {
                "factor_id": factor_id,
                "frequency": frequency,
                "_hold_days_key": int(hold_key),
                "horizon": horizon,
                "rank_ic_observations": int(len(observations)),
                "raw_mean_rank_ic": mean,
                "raw_rank_ic_std": standard,
                "raw_rank_ic_ir": mean / standard
                if np.isfinite(standard) and standard > 0
                else np.nan,
            }
        )
    return pd.DataFrame(rows)


def _result_grid(
    raw_result_dir: Path,
    rebalance: pd.DataFrame,
    rank_ic: pd.DataFrame,
) -> pd.DataFrame:
    summary_path = raw_result_dir / "summary.csv"
    candidates: list[pd.DataFrame] = []
    if summary_path.is_file():
        candidates.append(_normalize_identity(_read_csv(summary_path), "summary.csv"))
    candidates.extend(
        [
            _normalize_identity(rebalance, "rebalance_returns.csv"),
            _normalize_identity(rank_ic, "rank_ic.csv"),
        ]
    )
    grid = pd.concat(
        [frame[IDENTITY_COLUMNS + ["_hold_days_key"]] for frame in candidates],
        ignore_index=True,
    ).drop_duplicates(INTERNAL_IDENTITY_COLUMNS)
    return grid.sort_values(INTERNAL_IDENTITY_COLUMNS).reset_index(drop=True)


def _resolve_factor_values_path(
    raw_result_dir: Path,
    factor_values: Path | None,
) -> tuple[Path, dict[str, object]]:
    methodology_path = raw_result_dir / "methodology.json"
    if not methodology_path.is_file():
        raise FileNotFoundError(methodology_path)
    methodology = json.loads(methodology_path.read_text(encoding="utf-8"))
    if factor_values is not None:
        path = Path(factor_values)
    else:
        source = methodology.get("sources", {}).get("factor_input", [])
        candidates = [source] if isinstance(source, str) else list(source)
        paths = [Path(item) for item in candidates if str(item).lower().endswith((".parquet", ".pq"))]
        if len(paths) != 1:
            raise ValueError(
                "methodology.json 没有唯一的因子 Parquet，请用 --factor-values 指定"
            )
        path = paths[0]
    if not path.is_absolute():
        path = (raw_result_dir / path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path, methodology


def _factor_row_groups(parquet: pq.ParquetFile) -> dict[str, list[int]] | None:
    names = parquet.schema_arrow.names
    required = {"factor_id", "date", "code", "value"}
    if not required.issubset(names):
        return None
    position = names.index("factor_id")
    groups: dict[str, list[int]] = {}
    for row_group in range(parquet.num_row_groups):
        statistics = parquet.metadata.row_group(row_group).column(position).statistics
        if statistics is None or not statistics.has_min_max:
            return None
        minimum = short_backtest._stat_text(statistics.min)
        maximum = short_backtest._stat_text(statistics.max)
        if not minimum or minimum != maximum:
            return None
        groups.setdefault(minimum, []).append(row_group)
    return groups or None


def _factor_value_fingerprint(
    values: pd.DataFrame,
    signal_dates: set[pd.Timestamp],
) -> tuple[str, int]:
    frame = values[["date", "code", "value"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame[frame["date"].isin(signal_dates)].copy()
    if frame.empty:
        return "", 0
    if frame[["date", "code"]].duplicated().any():
        raise ValueError("因子值中 date、code 必须唯一")
    frame["code"] = frame["code"].astype("string").fillna("")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce").astype(float)
    frame.loc[frame["value"].eq(0.0), "value"] = 0.0
    frame = frame.sort_values(["date", "code"]).reset_index(drop=True)
    canonical = pd.DataFrame(
        {
            "date_ns": frame["date"].astype("int64"),
            "code": frame["code"],
            "value": frame["value"],
        }
    )
    row_hashes = pd.util.hash_pandas_object(canonical, index=False).to_numpy(
        dtype=np.uint64
    )
    digest = hashlib.sha256()
    digest.update(b"level2-factor-values-v1\0")
    digest.update(np.asarray([len(frame)], dtype="<i8").tobytes())
    digest.update(row_hashes.astype("<u8", copy=False).tobytes())
    return digest.hexdigest(), len(frame)


def build_factor_aliases(
    factor_values_path: Path,
    factor_ids: Iterable[str],
    signal_dates_by_factor: Mapping[str, set[pd.Timestamp]],
) -> pd.DataFrame:
    wanted = sorted(set(factor_ids))
    parquet = pq.ParquetFile(factor_values_path)
    groups = _factor_row_groups(parquet)
    if groups is None:
        raise ValueError("因子 Parquet 必须按 factor_id 分行组保存，才能逐因子计算 fingerprint")
    missing = sorted(set(wanted) - set(groups))
    if missing:
        raise ValueError(f"因子 Parquet 缺少 {len(missing)} 个因子，例如：{missing[:5]}")

    rows: list[dict[str, object]] = []
    for position, factor_id in enumerate(wanted, start=1):
        values = parquet.read_row_groups(
            groups[factor_id], columns=["factor_id", "date", "code", "value"]
        ).to_pandas()
        values = values[values["factor_id"].astype(str).eq(factor_id)].copy()
        fingerprint, fingerprint_rows = _factor_value_fingerprint(
            values, signal_dates_by_factor.get(factor_id, set())
        )
        rows.append(
            {
                "factor_id": factor_id,
                "fingerprint": fingerprint,
                "fingerprint_rows": fingerprint_rows,
                "fingerprint_status": "ok" if fingerprint else "no_evaluated_values",
            }
        )
        if position % 20 == 0 or position == len(wanted):
            print(f"已计算 {position}/{len(wanted)} 个因子 fingerprint")

    aliases = pd.DataFrame(rows)
    aliases["alias_group"] = aliases.apply(
        lambda row: f"AG_{row['fingerprint'][:12]}"
        if row["fingerprint"]
        else f"AG_EMPTY_{row['factor_id']}",
        axis=1,
    )
    aliases["alias_count"] = aliases.groupby("alias_group")["factor_id"].transform(
        "size"
    )
    representatives = aliases.groupby("alias_group")["factor_id"].transform("min")
    aliases["alias_representative"] = representatives
    aliases["is_alias_representative"] = aliases["factor_id"].eq(representatives)
    aliases["alias_evidence_weight"] = 1.0 / aliases["alias_count"]
    return aliases.sort_values(["alias_group", "factor_id"]).reset_index(drop=True)


def validate_alias_directions(
    aliases: pd.DataFrame, decisions: pd.DataFrame
) -> None:
    checked = aliases.merge(
        decisions[["factor_id", "selected_direction"]],
        on="factor_id",
        how="left",
        validate="one_to_one",
    )
    conflicts = checked.groupby("alias_group")["selected_direction"].nunique()
    conflicts = conflicts[conflicts.gt(1)]
    if conflicts.empty:
        return
    rows = checked[checked["alias_group"].isin(conflicts.index)][
        ["alias_group", "factor_id", "selected_direction"]
    ]
    raise ValueError("同一 alias_group 的方向不一致：\n" + rows.to_string(index=False))


def _signal_dates_by_factor(
    rank_ic: pd.DataFrame, rebalance: pd.DataFrame
) -> dict[str, set[pd.Timestamp]]:
    result: dict[str, set[pd.Timestamp]] = {}
    for frame in (rank_ic, rebalance):
        if "signal_date" not in frame.columns:
            continue
        dates = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize()
        for factor_id, factor_dates in dates.groupby(frame["factor_id"]):
            result.setdefault(str(factor_id), set()).update(factor_dates.dropna())
    return result


def _merge_descriptions(
    frame: pd.DataFrame,
    decisions: pd.DataFrame,
    factor_status: pd.DataFrame,
    aliases: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    result = frame.copy()
    _validate_frequency_column(result, decisions, "direction_decisions.csv")
    _validate_frequency_column(result, factor_status, "factor_status.csv")

    direction_extra = [
        column
        for column in decisions.columns
        if column not in {"factor_id", "selected_direction", "frequency"}
    ]
    conflicts = sorted(set(direction_extra) & set(result.columns))
    if conflicts:
        raise ValueError(
            "方向说明列与结果字段重名，请先改说明列名称：" + ", ".join(conflicts)
        )
    result = result.merge(
        decisions[["factor_id", "selected_direction"] + direction_extra],
        on="factor_id",
        how="left",
        validate="many_to_one",
    )

    status = factor_status.drop(columns=["frequency"], errors="ignore").copy()
    status_rename: dict[str, str] = {}
    for column in status.columns:
        if column == "factor_id":
            continue
        if column in result.columns:
            candidate = f"factor_status_{column}"
            if candidate in result.columns:
                raise ValueError(f"factor_status.csv 的列名冲突：{column}")
            status_rename[column] = candidate
    status = status.rename(columns=status_rename)
    result = result.merge(status, on="factor_id", how="left", validate="many_to_one")
    result = result.merge(aliases, on="factor_id", how="left", validate="many_to_one")
    return result, direction_extra


def _reorder_columns(
    frame: pd.DataFrame,
    direction_extra: list[str],
    *,
    period: bool,
) -> pd.DataFrame:
    first = IDENTITY_COLUMNS + [
        "selected_direction",
        "portfolio_rule",
        "fingerprint",
        "alias_group",
        "alias_count",
        "alias_representative",
        "is_alias_representative",
    ]
    if period:
        first += PERIOD_DATE_COLUMNS
    first += direction_extra
    first = [column for column in first if column in frame.columns]
    hidden = {"_hold_days_key"}
    rest = [
        column
        for column in frame.columns
        if column not in first and column not in hidden
    ]
    result = frame[first + rest].copy()
    if "hold_days" in result.columns:
        result["hold_days"] = pd.to_numeric(
            result["hold_days"], errors="coerce"
        ).astype("Int64")
    return result


def summarize_from_files(
    raw_result_dir: Path,
    factor_status_path: Path,
    direction_decisions_path: Path,
    *,
    factor_values_path: Path | None = None,
) -> dict[str, object]:
    raw_result_dir = Path(raw_result_dir)
    rebalance = _read_csv(raw_result_dir / "rebalance_returns.csv")
    rank_ic = _read_csv(raw_result_dir / "rank_ic.csv")
    decisions = load_direction_decisions(Path(direction_decisions_path))
    factor_status = load_factor_status(Path(factor_status_path))
    grid = _result_grid(raw_result_dir, rebalance, rank_ic)
    factor_ids = sorted(grid["factor_id"].astype(str).unique())
    _validate_factor_coverage(factor_ids, decisions, "direction_decisions.csv")
    _validate_factor_coverage(factor_ids, factor_status, "factor_status.csv")

    resolved_factor_values, raw_methodology = _resolve_factor_values_path(
        raw_result_dir, factor_values_path
    )
    rules = raw_methodology.get("rules", {})
    try:
        buy_cost = float(rules["buy_cost"])
        sell_cost = float(rules["sell_cost"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("methodology.json 缺少有效的 buy_cost 或 sell_cost") from error

    paired = pair_rebalance_sides(rebalance)
    period_returns = apply_selected_direction(
        paired, decisions, buy_cost=buy_cost, sell_cost=sell_cost
    )
    performance = summarize_period_returns(period_returns)
    ic_summary = summarize_rank_ic(rank_ic)

    summary = grid.merge(
        performance,
        on=INTERNAL_IDENTITY_COLUMNS + ["hold_days"],
        how="left",
        validate="one_to_one",
    ).merge(
        ic_summary,
        on=INTERNAL_IDENTITY_COLUMNS,
        how="left",
        validate="one_to_one",
    )
    summary = summary.merge(
        decisions[["factor_id", "selected_direction"]],
        on="factor_id",
        how="left",
        validate="many_to_one",
    )
    direction_sign = np.where(summary["selected_direction"].eq("high"), 1.0, -1.0)
    summary["directed_mean_rank_ic"] = summary["raw_mean_rank_ic"] * direction_sign
    summary["directed_rank_ic_ir"] = summary["raw_rank_ic_ir"] * direction_sign
    summary["portfolio_rule"] = np.where(
        summary["selected_direction"].eq("high"),
        "long_high_short_low",
        "long_low_short_high",
    )
    summary["periods"] = summary["periods"].fillna(0).astype(int)
    summary["rank_ic_observations"] = (
        summary["rank_ic_observations"].fillna(0).astype(int)
    )
    summary["status"] = np.where(summary["periods"].gt(0), "ok", "no_valid_period")

    signal_dates = _signal_dates_by_factor(rank_ic, rebalance)
    aliases = build_factor_aliases(
        resolved_factor_values, factor_ids, signal_dates
    )
    validate_alias_directions(aliases, decisions)
    period_returns, period_direction_extra = _merge_descriptions(
        period_returns.drop(columns="selected_direction"),
        decisions,
        factor_status,
        aliases,
    )
    summary, summary_direction_extra = _merge_descriptions(
        summary.drop(columns="selected_direction"),
        decisions,
        factor_status,
        aliases,
    )
    period_returns = _reorder_columns(
        period_returns, period_direction_extra, period=True
    )
    summary = _reorder_columns(summary, summary_direction_extra, period=False)
    summary["cumulative_net_return_rank"] = summary[
        "cumulative_net_return"
    ].rank(method="min", ascending=False, na_option="bottom")
    summary["cumulative_net_return_rank_within_horizon"] = summary.groupby(
        ["frequency", "hold_days", "horizon"], dropna=False
    )["cumulative_net_return"].rank(
        method="min", ascending=False, na_option="bottom"
    )
    summary = summary.sort_values(
        ["cumulative_net_return", "factor_id", "hold_days"],
        ascending=[False, True, True],
        na_position="last",
    ).reset_index(drop=True)

    methodology = {
        "inputs": {
            "raw_result_dir": str(raw_result_dir.resolve()),
            "factor_status": str(Path(factor_status_path).resolve()),
            "direction_decisions": str(Path(direction_decisions_path).resolve()),
            "factor_values": str(resolved_factor_values.resolve()),
        },
        "counts": {
            "factor_count": len(factor_ids),
            "factor_horizon_rows": len(summary),
            "period_rows": len(period_returns),
            "alias_groups": int(aliases["alias_group"].nunique()),
            "repeated_alias_groups": int(
                aliases.loc[aliases["alias_count"].gt(1), "alias_group"].nunique()
            ),
        },
        "rules": {
            "direction": (
                "selected_direction=high 时做多最高10%、做空最低10%；"
                "selected_direction=low 时做多最低10%、做空最高10%"
            ),
            "gross_exposure": "多头100%加空头100%，合计200%名义仓位",
            "buy_cost": buy_cost,
            "sell_cost": sell_cost,
            "short_cost": (
                "原始 side 成本按做多计算；做空成本用 "
                "2*(buy_cost+sell_cost)*turnover-long_side_cost 反推"
            ),
            "rank_ic": (
                "负向因子的 directed_mean_rank_ic 和 directed_rank_ic_ir 乘以 -1；"
                "raw_mean_rank_ic 和 raw_rank_ic_ir 保持原值；方向不由样本 IC 决定"
            ),
            "annualization": "按首个进入日至最后退出日的实际跨度折算，夏普按净收益计算",
            "drawdown": "逐期复利净值的最大回撤；不含日内波动",
            "win_rate": "逐期净收益大于0的比例",
            "fingerprint": (
                "对实际参与评价信号日的 date、code、value 排序后计算 "
                "pandas 64位行哈希，再计算 SHA-256；完全相同的值序列共用 alias_group"
            ),
        },
    }
    return {
        "period_returns": period_returns,
        "summary": summary,
        "aliases": aliases,
        "methodology": methodology,
    }


def write_outputs(outputs: Mapping[str, object], output_dir: Path) -> None:
    output_dir = Path(output_dir)
    for key, filename in {
        "period_returns": "directional_period_returns.csv",
        "summary": "directional_summary.csv",
        "aliases": "factor_aliases.csv",
    }.items():
        frame = outputs[key]
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"{key} 不是表格")
        short_backtest._atomic_write_csv(frame, output_dir / filename)
    short_backtest._atomic_write_json(outputs["methodology"], output_dir / "methodology.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-result-dir", type=Path, required=True)
    parser.add_argument("--factor-status", type=Path, required=True)
    parser.add_argument("--direction-decisions", type=Path, required=True)
    parser.add_argument("--factor-values", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    outputs = summarize_from_files(
        args.raw_result_dir,
        args.factor_status,
        args.direction_decisions,
        factor_values_path=args.factor_values,
    )
    write_outputs(outputs, args.output_dir)
    summary = outputs["summary"]
    aliases = outputs["aliases"]
    assert isinstance(summary, pd.DataFrame)
    assert isinstance(aliases, pd.DataFrame)
    repeated = aliases.loc[aliases["alias_count"].gt(1), "alias_group"].nunique()
    print(
        f"已汇总 {summary['factor_id'].nunique()} 个因子、{len(summary)} 个持有期结果；"
        f"发现 {repeated} 组重复值序列；结果在 {args.output_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
