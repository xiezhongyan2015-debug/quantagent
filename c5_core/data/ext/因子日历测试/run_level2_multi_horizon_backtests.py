# -*- coding: utf-8 -*-
"""一次读取 Level2 因子长表，完成多个日频持有期和一次月频回测。"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import run_level2_short_backtests as single


DEFAULT_DAILY_HOLD_DAYS = (1, 3, 5, 10, 20)
OUTPUT_KEYS = ("rank_ic", "rebalance_returns", "summary", "yearly")


def normalize_daily_hold_days(values: Sequence[int]) -> tuple[int, ...]:
    """检查日频持有期，并保留调用方给出的顺序。"""

    result = tuple(int(value) for value in values)
    if not result:
        raise ValueError("daily_hold_days 不能为空")
    if any(value <= 0 for value in result):
        raise ValueError("daily_hold_days 必须都是正整数")
    if len(set(result)) != len(result):
        raise ValueError("daily_hold_days 不能重复")
    return result


def _read_factor_piece(
    parquet: pq.ParquetFile,
    factor_id: str,
    row_groups: list[int],
    settings: single.ShortBacktestSettings,
) -> pd.DataFrame:
    """从已经打开的 Parquet 中读取一个因子的全部行组。"""

    piece = parquet.read_row_groups(
        row_groups,
        columns=list(single.REQUIRED_FACTOR_COLUMNS),
    ).to_pandas()
    piece["date"] = pd.to_datetime(piece["date"], errors="coerce")
    piece = piece[piece["factor_id"].astype(str).eq(factor_id)].copy()
    in_period = piece["date"].between(settings.start, settings.end)
    if in_period.any():
        return piece[in_period].copy()
    # 区间内没有值时保留一行说明，确保结果仍会列出这个因子。
    return piece.head(1).copy()


def _factor_row_groups_from_open_parquet(
    parquet: pq.ParquetFile,
) -> dict[str, list[int]] | None:
    """使用已经打开的 Parquet 检查每个行组是否只含一个因子。"""

    names = parquet.schema_arrow.names
    if any(column not in names for column in single.REQUIRED_FACTOR_COLUMNS):
        return None
    factor_position = names.index("factor_id")
    groups: dict[str, list[int]] = {}
    for row_group in range(parquet.num_row_groups):
        statistics = parquet.metadata.row_group(row_group).column(factor_position).statistics
        if statistics is None or not statistics.has_min_max:
            return None
        minimum = single._stat_text(statistics.min)
        maximum = single._stat_text(statistics.max)
        if not minimum or minimum != maximum:
            return None
        groups.setdefault(minimum, []).append(row_group)
    return groups or None


def _tag_horizon(
    outputs: Mapping[str, pd.DataFrame],
    *,
    hold_days: int | None,
    horizon: str,
) -> dict[str, pd.DataFrame]:
    """给四张结果表加上统一的持有期字段。"""

    tagged: dict[str, pd.DataFrame] = {}
    for key in OUTPUT_KEYS:
        frame = outputs[key].copy()
        frame["hold_days"] = pd.array(
            [hold_days if hold_days is not None else pd.NA] * len(frame),
            dtype="Int64",
        )
        frame["horizon"] = horizon
        first = [
            column
            for column in ("factor_id", "frequency", "hold_days", "horizon")
            if column in frame.columns
        ]
        frame = frame[first + [column for column in frame.columns if column not in first]]
        tagged[key] = frame
    return tagged


def _combine_tagged_outputs(
    tagged_outputs: Sequence[Mapping[str, pd.DataFrame]],
) -> dict[str, pd.DataFrame]:
    combined: dict[str, pd.DataFrame] = {}
    for key in OUTPUT_KEYS:
        frames = [item[key] for item in tagged_outputs]
        frame = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
        if not frame.empty:
            sort_columns = [
                column
                for column in ("factor_id", "frequency", "hold_days", "signal_date", "side")
                if column in frame.columns
            ]
            frame = frame.sort_values(sort_columns, na_position="last").reset_index(drop=True)
        combined[key] = frame
    return combined


def _assemble_one_bucket(
    metadata: pd.DataFrame,
    ic_frames: list[pd.DataFrame],
    trade_frames: list[pd.DataFrame],
    settings: single.ShortBacktestSettings,
    *,
    hold_days: int | None,
    horizon: str,
) -> dict[str, pd.DataFrame] | None:
    if metadata.empty:
        return None
    outputs = single._assemble_output_frames(
        metadata,
        ic_frames,
        trade_frames,
        settings,
    )
    return _tag_horizon(outputs, hold_days=hold_days, horizon=horizon)


def run_multi_horizon_from_parquet(
    factor_input: str | Path,
    settings: single.ShortBacktestSettings,
    daily_hold_days: Sequence[int] = DEFAULT_DAILY_HOLD_DAYS,
) -> dict[str, object]:
    """每个因子行组只读一次，并在同一因子矩阵上计算全部持有期。"""

    horizons = normalize_daily_hold_days(daily_hold_days)
    single._validate_settings(settings)
    candidates = [
        path
        for path in single._discover_factor_files(factor_input)
        if path.suffix.lower() in {".parquet", ".pq"}
    ]
    if len(candidates) != 1:
        raise ValueError(
            "该入口只接受按 factor_id 分行组保存的单个 Parquet，"
            "否则无法保证每个因子只读取一次"
        )
    factor_path = candidates[0]
    parquet = pq.ParquetFile(factor_path)
    factor_groups = _factor_row_groups_from_open_parquet(parquet)
    if factor_groups is None:
        raise ValueError(
            "该入口只接受按 factor_id 分行组保存的单个 Parquet，"
            "否则无法保证每个因子只读取一次"
        )

    history_start = pd.Timestamp(settings.start) - pd.Timedelta(
        days=settings.lookback_calendar_days
    )
    dates = single.january.read_calendar(
        Path(settings.qlib_dir), str(history_start.date()), settings.end
    )
    if dates.empty:
        raise ValueError("指定范围内没有交易日")
    all_base_codes = [
        path.stem for path in Path(settings.base_cache_dir).glob("*.parquet")
    ]
    base_panel = single.load_base_panel(
        Path(settings.base_cache_dir), all_base_codes, dates[0], dates[-1]
    )
    single._require_requested_end(dates, base_panel, settings)
    stocks, _, _ = single.january.make_axes(base_panel, dates)
    membership_rows = single.january.read_membership_rows(
        Path(settings.qlib_dir), str(dates[0].date()), str(dates[-1].date())
    )
    membership, _ = single.january.build_membership(dates, stocks, membership_rows)
    market = single._prepare_market_context(base_panel, dates, membership, settings)

    market_dates = market["dates"]
    market_stocks = market["stocks"]
    date_map = market["date_map"]
    stock_map = market["stock_map"]
    arrays = market["arrays"]
    eligible = market["eligible"]
    assert isinstance(market_dates, pd.DatetimeIndex)
    assert isinstance(market_stocks, list)
    assert isinstance(date_map, dict)
    assert isinstance(stock_map, dict)
    assert isinstance(arrays, dict)
    assert isinstance(eligible, np.ndarray)

    daily_metadata: list[pd.DataFrame] = []
    monthly_metadata: list[pd.DataFrame] = []
    daily_ic = {hold_days: [] for hold_days in horizons}
    daily_trades = {hold_days: [] for hold_days in horizons}
    monthly_ic: list[pd.DataFrame] = []
    monthly_trades: list[pd.DataFrame] = []
    usable_rows = 0
    excluded_rows = 0
    lower, upper = pd.Timestamp(settings.start), pd.Timestamp(settings.end)
    input_rows = sum(
        parquet.metadata.row_group(row_group).num_rows
        for row_groups in factor_groups.values()
        for row_group in row_groups
    )

    total_factors = len(factor_groups)
    for position, (factor_id, row_groups) in enumerate(factor_groups.items(), start=1):
        piece = _read_factor_piece(parquet, factor_id, row_groups, settings)
        values, metadata = single.prepare_factor_values(piece)
        if len(metadata) != 1 or str(metadata.iloc[0]["factor_id"]) != factor_id:
            raise ValueError(f"Parquet 分组与 factor_id 不一致：{factor_id}")
        in_period = values["date"].between(lower, upper)
        excluded = values["date"].isin(single.EXCLUDED_SIGNAL_DATES)
        excluded_rows += int((in_period & excluded).sum())
        values = values[in_period & ~excluded].copy()
        usable_rows += len(values)

        meta = metadata.iloc[0]
        factor = single.january.panel_to_matrix(
            values,
            "value",
            market_dates,
            market_stocks,
            date_map,
            stock_map,
            dtype=np.float32,
        )
        if meta["frequency"] == "monthly":
            monthly_metadata.append(metadata)
            current_ic = single.calculate_rank_ic(
                factor_id,
                factor,
                "monthly",
                arrays,
                eligible,
                market_dates,
                settings,
            )
            current_trades = single.backtest_both_sides(
                factor_id,
                factor,
                "monthly",
                arrays,
                eligible,
                market_dates,
                settings,
            )
            if not current_ic.empty:
                monthly_ic.append(current_ic)
            if not current_trades.empty:
                monthly_trades.append(current_trades)
        else:
            daily_metadata.append(metadata)
            for hold_days in horizons:
                current_settings = replace(settings, hold_days=hold_days)
                current_ic = single.calculate_rank_ic(
                    factor_id,
                    factor,
                    "daily",
                    arrays,
                    eligible,
                    market_dates,
                    current_settings,
                )
                current_trades = single.backtest_both_sides(
                    factor_id,
                    factor,
                    "daily",
                    arrays,
                    eligible,
                    market_dates,
                    current_settings,
                )
                if not current_ic.empty:
                    daily_ic[hold_days].append(current_ic)
                if not current_trades.empty:
                    daily_trades[hold_days].append(current_trades)

        del factor, values, piece
        if position % 10 == 0 or position == total_factors:
            print(f"已完成 {position}/{total_factors} 个因子")

    daily_meta = (
        pd.concat(daily_metadata, ignore_index=True).sort_values("factor_id")
        if daily_metadata
        else pd.DataFrame()
    )
    monthly_meta = (
        pd.concat(monthly_metadata, ignore_index=True).sort_values("factor_id")
        if monthly_metadata
        else pd.DataFrame()
    )
    all_metadata = pd.concat(
        [frame for frame in (daily_meta, monthly_meta) if not frame.empty],
        ignore_index=True,
    ).sort_values("factor_id")

    tagged_outputs: list[Mapping[str, pd.DataFrame]] = []
    for hold_days in horizons:
        current_settings = replace(settings, hold_days=hold_days)
        bucket = _assemble_one_bucket(
            daily_meta,
            daily_ic[hold_days],
            daily_trades[hold_days],
            current_settings,
            hold_days=hold_days,
            horizon=single._daily_return_horizon(hold_days),
        )
        if bucket is not None:
            tagged_outputs.append(bucket)
    monthly_bucket = _assemble_one_bucket(
        monthly_meta,
        monthly_ic,
        monthly_trades,
        settings,
        hold_days=None,
        horizon="next_month",
    )
    if monthly_bucket is not None:
        tagged_outputs.append(monthly_bucket)
    outputs: dict[str, object] = _combine_tagged_outputs(tagged_outputs)

    panel = market["panel"]
    assert isinstance(panel, pd.DataFrame)
    methodology = single._build_methodology(
        settings,
        all_metadata,
        usable_rows,
        panel,
        market_dates,
        membership,
        outputs,  # type: ignore[arg-type]
        input_rows=input_rows,
        excluded_rows=excluded_rows,
    )
    methodology["counts"]["daily_factor_count"] = int(len(daily_meta))
    methodology["counts"]["monthly_factor_count"] = int(len(monthly_meta))
    methodology["rules"]["daily_hold_days"] = list(horizons)
    methodology["rules"]["daily_rank_ic"] = (
        "每个交易日给出信号；次日开盘进入；分别持有"
        + "、".join(str(value) for value in horizons)
        + "个交易日后在开盘退出；股票截面 Spearman 秩相关"
    )
    methodology["rules"]["daily_rebalance"] = (
        "各持有期均从开始日锚定，按对应持有天数调仓；"
        "次日开盘进入，在对应退出日开盘退出"
    )
    methodology["sources"]["factor_input"] = [str(factor_path.resolve())]
    methodology["sources"]["factor_reading"] = (
        "单个 Parquet 只打开一次；每个 factor_id 的行组只读取一次"
    )
    methodology["limitations"].append(
        "五个日频持有期使用同一份因子值和市场矩阵；月频结果只计算一次。"
    )
    outputs["methodology"] = methodology
    return outputs


def _parse_hold_days(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("持有期必须是逗号分隔的正整数") from error
    try:
        return normalize_daily_hold_days(parsed)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factor-input", type=Path, required=True)
    parser.add_argument(
        "--base-cache-dir",
        type=Path,
        default=single.ShortBacktestSettings.base_cache_dir,
    )
    parser.add_argument(
        "--qlib-dir", type=Path, default=single.ShortBacktestSettings.qlib_dir
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", default=single.ShortBacktestSettings.start)
    parser.add_argument("--end", default=single.ShortBacktestSettings.end)
    parser.add_argument("--lookback-calendar-days", type=int, default=90)
    parser.add_argument(
        "--daily-hold-days",
        type=_parse_hold_days,
        default=DEFAULT_DAILY_HOLD_DAYS,
    )
    parser.add_argument("--groups", type=int, default=10)
    parser.add_argument("--min-stocks", type=int, default=50)
    parser.add_argument("--min-price", type=float, default=3.0)
    parser.add_argument("--min-avg-amount", type=float, default=5_000_000.0)
    parser.add_argument("--buy-cost", type=float, default=0.0003)
    parser.add_argument("--sell-cost", type=float, default=0.0013)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = single.ShortBacktestSettings(
        base_cache_dir=args.base_cache_dir,
        qlib_dir=args.qlib_dir,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        lookback_calendar_days=args.lookback_calendar_days,
        hold_days=args.daily_hold_days[0],
        groups=args.groups,
        min_stocks=args.min_stocks,
        min_price=args.min_price,
        min_avg_amount=args.min_avg_amount,
        buy_cost=args.buy_cost,
        sell_cost=args.sell_cost,
    )
    outputs = run_multi_horizon_from_parquet(
        args.factor_input,
        settings,
        daily_hold_days=args.daily_hold_days,
    )
    single.write_outputs(outputs, settings.output_dir)
    summary = outputs["summary"]
    assert isinstance(summary, pd.DataFrame)
    print(
        f"已完成 {summary['factor_id'].nunique()} 个因子，"
        f"共 {len(summary)} 行汇总结果；结果在 {settings.output_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
