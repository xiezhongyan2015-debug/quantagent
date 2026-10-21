# -*- coding: utf-8 -*-
"""Level2 短样本回测只使用合成数据。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import pyarrow as pa
import pyarrow.parquet as pq

import run_level2_short_backtests as target


def _synthetic_inputs() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DatetimeIndex,
    np.ndarray,
    target.ShortBacktestSettings,
]:
    dates = pd.bdate_range("2025-10-01", "2026-05-29")
    codes = [f"sz{i:06d}" for i in range(1, 21)]
    base_rows = []
    for date_index, date in enumerate(dates):
        for stock_index, code in enumerate(codes):
            growth = 1.0 + (stock_index - 9.5) * 0.00035
            price = 10.0 * growth**date_index
            base_rows.append(
                {
                    "date": date,
                    "code": code,
                    "open": price,
                    "close": price,
                    "amount": 10_000_000.0,
                    "qfq_ratio": 1.0,
                }
            )
    base = pd.DataFrame(base_rows)

    factor_rows = []
    signal_dates = dates[dates >= pd.Timestamp("2026-01-05")]
    for factor_id, frequency, direction in [
        ("L2_DAILY", "日频", "负向（原文）"),
        ("L2_MONTHLY", "月度", "图片未给明确方向"),
    ]:
        for date in signal_dates:
            for stock_index, code in enumerate(codes):
                factor_rows.append(
                    {
                        "factor_id": factor_id,
                        "date": date,
                        "code": code.upper(),
                        "value": float(stock_index),
                        "frequency": frequency,
                        "expected_direction": direction,
                        "direction_source": "catalog:direction",
                        "implementation_quality": "exact",
                    }
                )
    factors = pd.DataFrame(factor_rows)
    membership = np.ones((len(dates), len(codes)), dtype=bool)
    settings = target.ShortBacktestSettings(
        start="2026-01-05",
        end="2026-05-29",
        min_stocks=10,
        groups=10,
    )
    return factors, base, dates, membership, settings


@pytest.fixture(scope="module")
def synthetic_outputs() -> dict[str, object]:
    factors, base, dates, membership, settings = _synthetic_inputs()
    return target.run_backtests(factors, base, dates, membership, settings)


def test_daily_ic_and_five_day_trades_keep_both_sides(
    synthetic_outputs: dict[str, object],
) -> None:
    rank_ic = synthetic_outputs["rank_ic"]
    rebalance = synthetic_outputs["rebalance_returns"]
    summary = synthetic_outputs["summary"]
    yearly = synthetic_outputs["yearly"]
    assert isinstance(rank_ic, pd.DataFrame)
    assert isinstance(rebalance, pd.DataFrame)
    assert isinstance(summary, pd.DataFrame)
    assert isinstance(yearly, pd.DataFrame)

    daily_ic = rank_ic[rank_ic["factor_id"] == "L2_DAILY"]
    daily_trades = rebalance[rebalance["factor_id"] == "L2_DAILY"]
    assert len(daily_ic) > daily_trades["signal_date"].nunique()
    assert set(daily_trades["side"]) == {"low", "high"}
    assert daily_trades.groupby("signal_date")["side"].nunique().eq(2).all()
    assert daily_trades["trade_frequency"].eq("every_5_trading_days").all()
    assert not set(pd.to_datetime(daily_ic["signal_date"]).dt.normalize()) & set(
        target.EXCLUDED_SIGNAL_DATES
    )
    assert not set(pd.to_datetime(daily_trades["signal_date"]).dt.normalize()) & set(
        target.EXCLUDED_SIGNAL_DATES
    )

    daily_summary = summary[summary["factor_id"] == "L2_DAILY"].set_index("side")
    assert set(daily_summary.index) == {"low", "high"}
    assert daily_summary["expected_direction"].eq("负向（原文）").all()
    assert daily_summary["direction_source"].eq("catalog:direction").all()
    assert not daily_summary["direction_used_for_selection"].any()
    assert daily_summary.loc["high", "cumulative_return"] > daily_summary.loc[
        "low", "cumulative_return"
    ]
    assert "selected_direction" not in summary.columns

    pairs = yearly.groupby(["factor_id", "year"])["side"].nunique()
    assert pairs.eq(2).all()
    assert set(yearly["year"]) == {2026}
    assert not yearly["direction_used_for_selection"].any()


def test_costs_and_monthly_dates_follow_existing_method(
    synthetic_outputs: dict[str, object],
) -> None:
    rebalance = synthetic_outputs["rebalance_returns"]
    assert isinstance(rebalance, pd.DataFrame)
    monthly = rebalance[rebalance["factor_id"] == "L2_MONTHLY"].copy()
    assert monthly["trade_frequency"].eq("monthly").all()
    assert monthly["transaction_cost"].eq(0.0003 + 0.0013).all()
    assert monthly["turnover"].eq(1.0).all()
    assert monthly.groupby("signal_date")["side"].nunique().eq(2).all()
    signal_month = pd.to_datetime(monthly["signal_date"]).dt.to_period("M")
    entry_month = pd.to_datetime(monthly["entry_date"]).dt.to_period("M")
    exit_month = pd.to_datetime(monthly["exit_date"]).dt.to_period("M")
    assert (signal_month + 1 == entry_month).all()
    assert (entry_month == exit_month).all()

    daily = rebalance[rebalance["factor_id"] == "L2_DAILY"]
    for _, side in daily.groupby("side"):
        # 因子排序固定，首期只买入，末期只卖出，中间不换股票。
        assert np.isclose(side["transaction_cost"].sum(), 0.0003 + 0.0013)


def test_one_day_holding_uses_daily_labels_and_dates() -> None:
    factors, base, dates, membership, settings = _synthetic_inputs()
    settings = replace(settings, hold_days=1)
    outputs = target.run_backtests(factors, base, dates, membership, settings)

    rank_ic = outputs["rank_ic"]
    rebalance = outputs["rebalance_returns"]
    daily_ic = rank_ic[rank_ic["factor_id"] == "L2_DAILY"]
    daily = rebalance[rebalance["factor_id"] == "L2_DAILY"]
    monthly = rebalance[rebalance["factor_id"] == "L2_MONTHLY"]

    assert daily_ic["return_horizon"].eq("next_1_trading_day").all()
    assert daily["trade_frequency"].eq("every_trading_day").all()
    assert daily["return_horizon"].eq("next_1_trading_day").all()
    assert monthly["trade_frequency"].eq("monthly").all()
    assert monthly["return_horizon"].eq("next_month").all()

    positions = {date: position for position, date in enumerate(dates)}
    for row in daily[["signal_date", "entry_date", "exit_date"]].drop_duplicates().itertuples():
        assert positions[pd.Timestamp(row.entry_date)] == positions[pd.Timestamp(row.signal_date)] + 1
        assert positions[pd.Timestamp(row.exit_date)] == positions[pd.Timestamp(row.entry_date)] + 1


def test_monthly_periods_do_not_treat_midmonth_end_as_month_end() -> None:
    dates = pd.bdate_range("2026-01-05", "2026-07-16")
    periods = target._monthly_periods(
        dates, pd.Timestamp("2026-01-05"), pd.Timestamp("2026-07-16")
    )
    assert periods
    assert dates[periods[-1][0]] == pd.Timestamp("2026-05-29")
    assert dates[periods[-1][2]] == pd.Timestamp("2026-06-30")
    assert pd.Timestamp("2026-07-16") not in {dates[item[2]] for item in periods}


def test_missing_scheduled_signal_charges_exit_and_new_entry() -> None:
    factors, base, dates, membership, settings = _synthetic_inputs()
    factors = factors[factors["factor_id"] == "L2_DAILY"].copy()
    factors.loc[factors["date"].eq(pd.Timestamp("2026-01-12")), "value"] = np.nan
    outputs = target.run_backtests(factors, base, dates, membership, settings)
    rebalance = outputs["rebalance_returns"]
    assert isinstance(rebalance, pd.DataFrame)
    assert pd.Timestamp("2026-01-12") not in set(rebalance["signal_date"])
    for _, side in rebalance.groupby("side"):
        # 缺一期后先退出，再重新进入，因此完整买卖发生两次。
        assert np.isclose(side["transaction_cost"].sum(), 2 * (0.0003 + 0.0013))


def test_reader_validation_and_output_files(
    tmp_path: Path, synthetic_outputs: dict[str, object]
) -> None:
    factors, _, _, _, _ = _synthetic_inputs()
    input_path = tmp_path / "factors.parquet"
    factors.head(20).to_parquet(input_path, index=False)
    loaded = target.load_factor_values(input_path)
    prepared, metadata = target.prepare_factor_values(loaded)
    assert len(prepared) == 20
    assert metadata.iloc[0]["frequency"] == "daily"
    assert prepared["code"].str.match(r"^(sh|sz|bj)\d{6}$").all()

    duplicate = pd.concat([loaded, loaded.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="必须唯一"):
        target.prepare_factor_values(duplicate)
    missing_frequency = loaded.copy()
    missing_frequency.loc[missing_frequency.index[0], "frequency"] = pd.NA
    with pytest.raises(ValueError, match="frequency 不能为空"):
        target.prepare_factor_values(missing_frequency)

    target.write_outputs(synthetic_outputs, tmp_path / "result")
    expected = {
        "rank_ic.csv",
        "rebalance_returns.csv",
        "summary.csv",
        "yearly.csv",
        "methodology.json",
    }
    assert {path.name for path in (tmp_path / "result").iterdir()} == expected
    methodology = json.loads(
        (tmp_path / "result" / "methodology.json").read_text(encoding="utf-8")
    )
    assert methodology["rules"]["groups"] == 10
    assert methodology["rules"]["buy_cost"] == 0.0003
    assert methodology["rules"]["sell_cost"] == 0.0013
    assert methodology["period"]["excluded_signal_dates"] == [
        "2026-03-12",
        "2026-04-14",
        "2026-05-12",
        "2026-07-06",
        "2026-07-09",
        "2026-07-10",
        "2026-07-13",
        "2026-07-15",
    ]
    assert methodology["counts"]["summary_rows"] == 4


def test_load_base_panel_reads_only_requested_codes(tmp_path: Path) -> None:
    cache = tmp_path / "base"
    cache.mkdir()
    dates = pd.bdate_range("2026-01-05", periods=3)
    for code in ["sh600000", "sz000001", "sz000002", "sz302132"]:
        pd.DataFrame(
            {
                "date": dates,
                "code": code,
                "open": 10.0,
                "close": 10.0,
                "amount": 8_000_000.0,
                "qfq_ratio": 1.0,
                "unused": 123.0,
            }
        ).to_parquet(cache / f"{code}.parquet", index=False)
    panel = target.load_base_panel(
        cache,
        ["600000.SH", "000001.SZ", "300001.SZ", "SZ300114"],
        dates[0],
        dates[-1],
    )
    assert set(panel["code"]) == {"sh600000", "sz000001", "sz300114"}
    assert "unused" not in panel.columns
    assert panel.attrs["missing_codes"] == ["sz300001"]


def test_file_entry_reads_synthetic_membership_and_parquet(tmp_path: Path) -> None:
    factors, base, dates, _, _ = _synthetic_inputs()
    qlib_dir = tmp_path / "qlib"
    cache_dir = tmp_path / "base"
    (qlib_dir / "calendars").mkdir(parents=True)
    (qlib_dir / "instruments").mkdir(parents=True)
    cache_dir.mkdir()
    pd.Series(dates.strftime("%Y-%m-%d")).to_csv(
        qlib_dir / "calendars" / "day.txt", index=False, header=False
    )
    codes = sorted(base["code"].unique())
    membership = pd.DataFrame(
        {
            "instrument": [code.upper() for code in codes],
            "start": "2025-10-01",
            "end": "2026-05-29",
        }
    )
    membership.to_csv(
        qlib_dir / "instruments" / "csi800.txt",
        sep="\t",
        index=False,
        header=False,
    )
    membership.iloc[:1].to_csv(
        qlib_dir / "instruments" / "csi1000.txt",
        sep="\t",
        index=False,
        header=False,
    )
    for code, group in base.groupby("code"):
        group.to_parquet(cache_dir / f"{code}.parquet", index=False)
    factor_path = tmp_path / "level2_values.parquet"
    writer = None
    try:
        for _, group in factors.groupby("factor_id", sort=False):
            table = pa.Table.from_pandas(group, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(factor_path, table.schema)
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()
    assert target._factor_row_groups(factor_path) is not None
    settings = target.ShortBacktestSettings(
        base_cache_dir=cache_dir,
        qlib_dir=qlib_dir,
        output_dir=tmp_path / "result",
        start="2026-01-05",
        end="2026-05-29",
        min_stocks=10,
    )
    outputs = target.run_from_files(factor_path, settings)
    summary = outputs["summary"]
    assert isinstance(summary, pd.DataFrame)
    assert len(summary) == 4
    assert set(summary["side"]) == {"low", "high"}
    methodology = outputs["methodology"]
    assert isinstance(methodology, dict)
    assert methodology["counts"]["matrix_stocks"] == 20
    assert methodology["sources"]["factor_reading"] == "按 factor_id 分批读取"
