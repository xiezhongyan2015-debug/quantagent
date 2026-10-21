from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


HORIZONS = (1, 3, 5, 10, 20)
VOLATILITY_CUTOFFS: tuple[float | None, ...] = (None, 0.025, 0.020, 0.015, 0.010)
BOX_DAYS = 30
VOLUME_MULTIPLE = 2.0
BAR_TO_BODY_MULTIPLE = 2.0
BUY_COST = 0.0003
SELL_COST = 0.0008
SLIPPAGE_EACH_SIDE = 0.00025
BENCHMARK_CODE = "SH000300"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检验盘整、连续放量和第二日长影线事件")
    parser.add_argument(
        "--qlib-root",
        default=os.getenv("QLIB_DATA_ROOT", "/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin"),
    )
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def read_calendar(root: Path) -> pd.DatetimeIndex:
    lines = (root / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines()
    calendar = pd.to_datetime(pd.Series(lines), errors="coerce").dropna()
    if calendar.empty:
        raise ValueError("交易日文件为空")
    return pd.DatetimeIndex(calendar).normalize()


def read_membership(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        sep="\t",
        header=None,
        names=["code", "start_date", "end_date"],
        dtype={"code": "string"},
    )
    frame["code"] = frame["code"].str.strip().str.upper()
    frame["start_date"] = pd.to_datetime(frame["start_date"], errors="coerce").dt.normalize()
    frame["end_date"] = pd.to_datetime(frame["end_date"], errors="coerce").dt.normalize()
    return frame.dropna().sort_values(["code", "start_date"]).reset_index(drop=True)


def is_a_share(code: str) -> bool:
    sh = re.fullmatch(r"SH(600|601|603|605|688|689)\d{3}", code)
    sz = re.fullmatch(r"SZ(000|001|002|003|300|301|302)\d{3}", code)
    return bool(sh or sz)


def read_field(
    features_root: Path,
    code: str,
    field: str,
    calendar_size: int,
) -> np.ndarray:
    path = features_root / code.lower() / f"{field}.day.bin"
    raw = np.fromfile(path, dtype="<f4")
    if raw.size < 2 or not np.isfinite(raw[0]):
        raise ValueError(f"无效资料: {path}")
    start = int(round(float(raw[0])))
    values = raw[1:].astype("float64", copy=False)
    result = np.full(calendar_size, np.nan, dtype="float64")
    source_start = max(0, -start)
    target_start = max(0, start)
    length = min(values.size - source_start, calendar_size - target_start)
    if length > 0:
        result[target_start : target_start + length] = values[
            source_start : source_start + length
        ]
    return result


def make_membership_lookup(
    frame: pd.DataFrame,
) -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    result: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = defaultdict(list)
    for row in frame.itertuples(index=False):
        result[str(row.code)].append((row.start_date, row.end_date))
    return result


def is_member(
    lookup: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]],
    code: str,
    date: pd.Timestamp,
) -> bool:
    return any(start <= date <= end for start, end in lookup.get(code, ()))


def close_position(high: float, low: float, close: float) -> str:
    bar_height = high - low
    if not np.isfinite(bar_height) or bar_height <= 0:
        return "无法计算"
    position = (close - low) / bar_height
    if position <= 1.0 / 3.0:
        return "底部三分之一"
    if position >= 2.0 / 3.0:
        return "顶部三分之一"
    return "中间三分之一"


def find_stock_events(
    *,
    code: str,
    calendar: pd.DatetimeIndex,
    fields: dict[str, np.ndarray],
    start: pd.Timestamp,
    end: pd.Timestamp,
    listing_start: pd.Timestamp,
    listing_end: pd.Timestamp,
    hs300_lookup: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]],
    benchmark_open: np.ndarray,
    benchmark_close: np.ndarray,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    open_price = fields["open"]
    high = fields["high"]
    low = fields["low"]
    close = fields["close"]
    volume = fields["volume"]
    valid = (
        np.isfinite(open_price)
        & np.isfinite(high)
        & np.isfinite(low)
        & np.isfinite(close)
        & np.isfinite(volume)
        & (open_price > 0)
        & (high > 0)
        & (low > 0)
        & (close > 0)
        & (volume > 0)
        & (high >= np.maximum(open_price, close))
        & (low <= np.minimum(open_price, close))
    )
    positions = np.flatnonzero(valid)
    if positions.size < BOX_DAYS + 2:
        return [], [], 0, 0

    frame = pd.DataFrame(
        {
            "calendar_index": positions,
            "trade_date": calendar[positions],
            "open": open_price[positions],
            "high": high[positions],
            "low": low[positions],
            "close": close[positions],
            "volume": volume[positions],
        }
    )
    frame["box_high"] = frame["high"].rolling(BOX_DAYS).max().shift(2)
    frame["box_low"] = frame["low"].rolling(BOX_DAYS).min().shift(2)
    frame["box_volume"] = frame["volume"].rolling(BOX_DAYS).mean().shift(2)
    # 30根K线内部有29个收盘到收盘收益。
    frame["daily_volatility"] = (
        frame["close"]
        .pct_change(fill_method=None)
        .rolling(BOX_DAYS - 1)
        .std(ddof=1)
        .shift(2)
    )
    frame["box_height"] = frame["box_high"] - frame["box_low"]
    frame["bar_height"] = frame["high"] - frame["low"]
    frame["body_height"] = (frame["close"] - frame["open"]).abs()
    frame["first_volume_multiple"] = frame["volume"].shift(1) / frame["box_volume"]
    frame["second_volume_multiple"] = frame["volume"] / frame["box_volume"]
    frame["bar_to_box_multiple"] = frame["bar_height"] / frame["box_height"]
    frame["bar_to_body_multiple"] = frame["bar_height"] / frame["body_height"]

    consecutive_days = frame["calendar_index"].diff() == 1
    signal = (
        consecutive_days
        & (frame["first_volume_multiple"] >= VOLUME_MULTIPLE)
        & (frame["second_volume_multiple"] >= VOLUME_MULTIPLE)
        & np.isfinite(frame["daily_volatility"])
        & (frame["trade_date"] >= max(start, listing_start))
        & (frame["trade_date"] <= min(end, listing_end))
    )
    selected = frame.loc[signal]
    if selected.empty:
        return [], [], 0, 0

    signals: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    base_count = int(len(selected))
    long_wick_count = 0
    for row in selected.itertuples(index=False):
        signal_date = pd.Timestamp(row.trade_date).normalize()
        signal_index = int(row.calendar_index)
        event_id = f"{code}_{signal_date:%Y%m%d}"
        entry_index = signal_index + 1
        entry_open = (
            float(open_price[entry_index])
            if entry_index < len(open_price) and np.isfinite(open_price[entry_index])
            else math.nan
        )
        entry_gap = (
            entry_open / float(row.close) - 1.0
            if np.isfinite(entry_open) and row.close > 0
            else math.nan
        )
        long_wick = bool(
            row.bar_height > 0
            and row.bar_height > BAR_TO_BODY_MULTIPLE * row.body_height
        )
        long_wick_count += int(long_wick)
        baseline_control = bool(row.daily_volatility <= 0.015)
        hs300_member = is_member(hs300_lookup, code, signal_date)
        # 全部沪深A股只保留预先指定的1.5%低波动档。
        # 沪深300内的长影线事件全部保留，便于查看其他波动率档。
        if not baseline_control and not (long_wick and hs300_member):
            continue
        signals.append(
            {
                "event_id": event_id,
                "code": code.lower(),
                "signal_date": signal_date,
                "calendar_index": signal_index,
                "hs300_member": hs300_member,
                "long_wick": long_wick,
                "baseline_control": baseline_control,
                "daily_volatility": float(row.daily_volatility),
                "annualized_volatility": float(row.daily_volatility * math.sqrt(252.0)),
                "box_high": float(row.box_high),
                "box_low": float(row.box_low),
                "box_height": float(row.box_height),
                "bar_height": float(row.bar_height),
                "body_height": float(row.body_height),
                "bar_to_box_multiple": float(row.bar_to_box_multiple),
                "bar_to_body_multiple": float(row.bar_to_body_multiple),
                "first_volume_multiple": float(row.first_volume_multiple),
                "second_volume_multiple": float(row.second_volume_multiple),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "candle_return": float(row.close / row.open - 1.0),
                "close_position": close_position(row.high, row.low, row.close),
                "entry_date": (
                    calendar[entry_index] if entry_index < len(calendar) else pd.NaT
                ),
                "entry_open": entry_open,
                "entry_gap": entry_gap,
                "entry_gap_ge_9_5pct": bool(
                    np.isfinite(entry_gap) and entry_gap >= 0.095
                ),
            }
        )
        for horizon in HORIZONS:
            exit_index = signal_index + horizon
            exit_close = (
                float(close[exit_index])
                if exit_index < len(close) and np.isfinite(close[exit_index])
                else math.nan
            )
            benchmark_entry = (
                float(benchmark_open[entry_index])
                if entry_index < len(benchmark_open)
                and np.isfinite(benchmark_open[entry_index])
                else math.nan
            )
            benchmark_exit = (
                float(benchmark_close[exit_index])
                if exit_index < len(benchmark_close)
                and np.isfinite(benchmark_close[exit_index])
                else math.nan
            )
            gross_return = (
                exit_close / entry_open - 1.0
                if np.isfinite(entry_open)
                and entry_open > 0
                and np.isfinite(exit_close)
                else math.nan
            )
            net_return = (
                exit_close
                * (1.0 - SELL_COST - SLIPPAGE_EACH_SIDE)
                / (entry_open * (1.0 + BUY_COST + SLIPPAGE_EACH_SIDE))
                - 1.0
                if np.isfinite(gross_return)
                else math.nan
            )
            benchmark_return = (
                benchmark_exit / benchmark_entry - 1.0
                if np.isfinite(benchmark_entry)
                and benchmark_entry > 0
                and np.isfinite(benchmark_exit)
                else math.nan
            )
            outcomes.append(
                {
                    "event_id": event_id,
                    "code": code.lower(),
                    "signal_date": signal_date,
                    "horizon_days": horizon,
                    "entry_date": (
                        calendar[entry_index] if entry_index < len(calendar) else pd.NaT
                    ),
                    "exit_date": (
                        calendar[exit_index] if exit_index < len(calendar) else pd.NaT
                    ),
                    "gross_return": gross_return,
                    "net_return": net_return,
                    "benchmark_return": benchmark_return,
                    "excess_return": (
                        gross_return - benchmark_return
                        if np.isfinite(gross_return)
                        and np.isfinite(benchmark_return)
                        else math.nan
                    ),
                }
            )
    return signals, outcomes, base_count, long_wick_count


def mark_nonoverlap(signals: pd.DataFrame, source_mask: pd.Series, column: str) -> None:
    signals[column] = False
    for _, group in signals.loc[source_mask].groupby("code", sort=False):
        last_signal = -10000
        for index, row in group.sort_values("calendar_index").iterrows():
            current = int(row["calendar_index"])
            if current - last_signal >= max(HORIZONS):
                signals.loc[index, column] = True
                last_signal = current


def clustered_interval(values: pd.Series, months: pd.Series) -> tuple[float, float]:
    valid = pd.DataFrame({"value": values, "month": months}).dropna()
    if valid.empty:
        return math.nan, math.nan
    mean = float(valid["value"].mean())
    clusters = valid.groupby("month")["value"].agg(["sum", "count"])
    group_count = len(clusters)
    if group_count < 2:
        return math.nan, math.nan
    scores = clusters["sum"] - clusters["count"] * mean
    variance = (
        group_count
        / (group_count - 1.0)
        * float((scores**2).sum())
        / float(len(valid) ** 2)
    )
    error = 1.96 * math.sqrt(max(variance, 0.0))
    return mean - error, mean + error


def summarize_subset(
    signals: pd.DataFrame,
    outcomes: pd.DataFrame,
    labels: dict[str, Any],
    cutoff: float | None,
    horizon: int,
) -> dict[str, Any]:
    chosen = signals if cutoff is None else signals.loc[signals["daily_volatility"] <= cutoff]
    result = outcomes.loc[
        (outcomes["horizon_days"] == horizon)
        & outcomes["event_id"].isin(chosen["event_id"])
    ].dropna(subset=["net_return"])
    months = pd.to_datetime(result["signal_date"]).dt.to_period("M").astype("string")
    low, high = clustered_interval(result["net_return"], months)
    row = dict(labels)
    row.update(
        {
            "daily_volatility_cutoff": cutoff,
            "horizon_days": horizon,
            "signal_count": int(len(chosen)),
            "tradeable_count": int(len(result)),
            "stock_count": int(chosen["code"].nunique()),
            "year_count": int(pd.to_datetime(chosen["signal_date"]).dt.year.nunique()),
            "mean_gross_return": float(result["gross_return"].mean()),
            "median_gross_return": float(result["gross_return"].median()),
            "gross_up_rate": float((result["gross_return"] > 0).mean()),
            "mean_net_return": float(result["net_return"].mean()),
            "median_net_return": float(result["net_return"].median()),
            "net_up_rate": float((result["net_return"] > 0).mean()),
            "mean_benchmark_return": float(result["benchmark_return"].mean()),
            "mean_excess_return": float(result["excess_return"].mean()),
            "median_excess_return": float(result["excess_return"].median()),
            "mean_net_return_ci95_low": low,
            "mean_net_return_ci95_high": high,
        }
    )
    return row


def build_summary(signals: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    event_options = (
        (
            "连续两日放量",
            signals["baseline_control"],
            "baseline_nonoverlap_20d",
        ),
        (
            "连续两日放量+第二日整根K线大于实体2倍",
            signals["long_wick"],
            "long_wick_nonoverlap_20d",
        ),
    )
    universe_options = (
        ("全部沪深A股", pd.Series(True, index=signals.index)),
        ("当日沪深300", signals["hs300_member"]),
    )
    for event_name, event_mask, spacing_column in event_options:
        for universe_name, universe_mask in universe_options:
            cutoffs = (
                VOLATILITY_CUTOFFS
                if event_name != "连续两日放量" and universe_name == "当日沪深300"
                else (0.015, 0.010)
            )
            base = signals.loc[event_mask & universe_mask]
            base_outcomes = outcomes.loc[outcomes["event_id"].isin(base["event_id"])]
            for sample_name, sample_mask in (
                ("全部信号", pd.Series(True, index=base.index)),
                ("同股至少间隔20日", base[spacing_column]),
            ):
                sample = base.loc[sample_mask]
                sample_outcomes = base_outcomes.loc[
                    base_outcomes["event_id"].isin(sample["event_id"])
                ]
                labels = {
                    "event": event_name,
                    "universe": universe_name,
                    "sample": sample_name,
                }
                for cutoff in cutoffs:
                    for horizon in HORIZONS:
                        rows.append(
                            summarize_subset(
                                sample,
                                sample_outcomes,
                                labels,
                                cutoff,
                                horizon,
                            )
                        )
    return pd.DataFrame(rows)


def grouped_long_wick(
    signals: pd.DataFrame,
    outcomes: pd.DataFrame,
    group_column: str,
) -> pd.DataFrame:
    signal_columns = ["event_id", "close_position"]
    rows = []
    samples = (
        (
            "全部沪深A股，日波动率不高于1.5%",
            signals["long_wick"] & signals["baseline_control"],
        ),
        ("当日沪深300，不限波动率", signals["long_wick"] & signals["hs300_member"]),
    )
    for sample_name, sample_mask in samples:
        selected = signals.loc[sample_mask, signal_columns]
        merged = outcomes.merge(
            selected,
            on="event_id",
            how="inner",
            validate="many_to_one",
        )
        merged["year"] = pd.to_datetime(merged["signal_date"]).dt.year
        for (group_value, horizon), group in merged.groupby(
            [group_column, "horizon_days"], dropna=False, sort=True
        ):
            valid = group.dropna(subset=["net_return"])
            rows.append(
                {
                    "sample": sample_name,
                    group_column: group_value,
                    "horizon_days": horizon,
                    "signal_count": int(group["event_id"].nunique()),
                    "tradeable_count": int(len(valid)),
                    "stock_count": int(group["code"].nunique()),
                    "mean_gross_return": float(valid["gross_return"].mean()),
                    "median_gross_return": float(valid["gross_return"].median()),
                    "gross_up_rate": float((valid["gross_return"] > 0).mean()),
                    "mean_net_return": float(valid["net_return"].mean()),
                    "mean_excess_return": float(valid["excess_return"].mean()),
                }
            )
    return pd.DataFrame(rows)


def json_value(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    raise TypeError(f"无法写入JSON: {type(value)!r}")


def main() -> None:
    args = parse_args()
    qlib_root = Path(args.qlib_root).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[1]
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else project_root / "factor_test_outputs" / "consolidation_double_volume_long_wick"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(args.start).normalize()
    end = pd.Timestamp(args.end).normalize()
    if end < start:
        raise ValueError("结束日期不能早于开始日期")

    calendar = read_calendar(qlib_root)
    features_root = qlib_root / "features"
    instruments = read_membership(qlib_root / "instruments" / "all.txt")
    instruments = instruments.loc[instruments["code"].map(is_a_share)].copy()
    hs300 = read_membership(qlib_root / "instruments" / "csi300.txt")
    hs300_lookup = make_membership_lookup(hs300)
    benchmark_open = read_field(features_root, BENCHMARK_CODE, "open", len(calendar))
    benchmark_close = read_field(features_root, BENCHMARK_CODE, "close", len(calendar))

    signal_rows: list[dict[str, Any]] = []
    outcome_rows: list[dict[str, Any]] = []
    base_signal_count = 0
    long_wick_signal_count = 0
    skipped: list[dict[str, str]] = []
    records = list(instruments.itertuples(index=False))
    print(f"开始读取{len(records):,}只历史沪深A股……", flush=True)
    for number, instrument in enumerate(records, start=1):
        code = str(instrument.code)
        try:
            fields = {
                field: read_field(features_root, code, field, len(calendar))
                for field in ("open", "high", "low", "close", "volume")
            }
            (
                stock_signals,
                stock_outcomes,
                stock_base_count,
                stock_long_wick_count,
            ) = find_stock_events(
                code=code,
                calendar=calendar,
                fields=fields,
                start=start,
                end=end,
                listing_start=instrument.start_date,
                listing_end=instrument.end_date,
                hs300_lookup=hs300_lookup,
                benchmark_open=benchmark_open,
                benchmark_close=benchmark_close,
            )
            signal_rows.extend(stock_signals)
            outcome_rows.extend(stock_outcomes)
            base_signal_count += stock_base_count
            long_wick_signal_count += stock_long_wick_count
        except (FileNotFoundError, ValueError) as exc:
            skipped.append({"code": code, "reason": str(exc)})
        if number % 500 == 0 or number == len(records):
            print(
                f"已处理{number:,}/{len(records):,}只，找到{base_signal_count:,}次连续放量",
                flush=True,
            )

    signals = pd.DataFrame(signal_rows).sort_values(
        ["signal_date", "code"]
    ).reset_index(drop=True)
    outcomes = pd.DataFrame(outcome_rows).sort_values(
        ["signal_date", "code", "horizon_days"]
    ).reset_index(drop=True)
    if signals.empty or outcomes.empty:
        raise RuntimeError("没有找到连续两日放量样本")
    mark_nonoverlap(
        signals,
        signals["baseline_control"],
        "baseline_nonoverlap_20d",
    )
    mark_nonoverlap(
        signals,
        signals["long_wick"],
        "long_wick_nonoverlap_20d",
    )

    summary = build_summary(signals, outcomes)
    by_year = grouped_long_wick(signals, outcomes, "year")
    by_close_position = grouped_long_wick(signals, outcomes, "close_position")
    signals.to_parquet(output_dir / "signals.parquet", index=False)
    outcomes.to_parquet(output_dir / "outcomes.parquet", index=False)
    summary.to_csv(output_dir / "summary.csv", index=False, encoding="utf-8-sig")
    by_year.to_csv(output_dir / "long_wick_by_year.csv", index=False, encoding="utf-8-sig")
    by_close_position.to_csv(
        output_dir / "long_wick_by_close_position.csv",
        index=False,
        encoding="utf-8-sig",
    )
    if skipped:
        pd.DataFrame(skipped).to_csv(
            output_dir / "skipped_instruments.csv",
            index=False,
            encoding="utf-8-sig",
        )

    run_info = {
        "qlib_root": str(qlib_root),
        "signal_start": start,
        "signal_end": end,
        "a_share_instrument_count": len(records),
        "skipped_instrument_count": len(skipped),
        "benchmark_code": BENCHMARK_CODE,
        "continuous_double_volume_signal_count": base_signal_count,
        "retained_low_volatility_baseline_count": int(
            signals["baseline_control"].sum()
        ),
        "long_wick_signal_count": long_wick_signal_count,
        "retained_long_wick_signal_count": int(signals["long_wick"].sum()),
        "retained_long_wick_hs300_signal_count": int(
            (signals["long_wick"] & signals["hs300_member"]).sum()
        ),
        "definition": {
            "box_days": BOX_DAYS,
            "box_excludes_two_volume_days": True,
            "daily_volatility_return_count": BOX_DAYS - 1,
            "volume_multiple_each_day": VOLUME_MULTIPLE,
            "long_wick_is_second_day_high_minus_low": True,
            "body_is_second_day_absolute_close_minus_open": True,
            "bar_to_body_multiple_strictly_greater_than": BAR_TO_BODY_MULTIPLE,
            "requires_positive_candle": False,
            "requires_shadow_direction": False,
            "entry": "信号日收盘确认，下一市场交易日开盘",
            "exit": "信号日后的第1、3、5、10、20个市场交易日收盘",
            "one_day_result": "只观察次日开盘到收盘；A股T+1下不可实际完成",
            "excess_return": "股票未扣费价格收益减去沪深300同期收益",
        },
        "costs": {
            "buy_cost": BUY_COST,
            "sell_cost": SELL_COST,
            "slippage_each_side": SLIPPAGE_EACH_SIDE,
        },
    }
    (output_dir / "run_info.json").write_text(
        json.dumps(run_info, ensure_ascii=False, indent=2, default=json_value),
        encoding="utf-8",
    )
    print(f"完成：{output_dir}", flush=True)


if __name__ == "__main__":
    main()
