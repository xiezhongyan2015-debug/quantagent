# -*- coding: utf-8 -*-
"""多持有期 Level2 回测只使用合成数据。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import run_level2_multi_horizon_backtests as target
import run_level2_short_backtests as single
from test_run_level2_short_backtests import _synthetic_inputs


def _write_inputs(tmp_path: Path) -> tuple[Path, single.ShortBacktestSettings]:
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
    settings = single.ShortBacktestSettings(
        base_cache_dir=cache_dir,
        qlib_dir=qlib_dir,
        output_dir=tmp_path / "result",
        start="2026-01-05",
        end="2026-05-29",
        min_stocks=10,
    )
    return factor_path, settings


def test_multi_horizon_reads_each_factor_matrix_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factor_path, settings = _write_inputs(tmp_path)
    calls = 0
    piece_calls = 0
    original = single.january.panel_to_matrix
    original_read = target._read_factor_piece

    def counted(*args: object, **kwargs: object):
        nonlocal calls
        if len(args) > 1 and args[1] == "value":
            calls += 1
        return original(*args, **kwargs)

    def counted_read(*args: object, **kwargs: object):
        nonlocal piece_calls
        piece_calls += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(single.january, "panel_to_matrix", counted)
    monkeypatch.setattr(target, "_read_factor_piece", counted_read)
    outputs = target.run_multi_horizon_from_parquet(
        factor_path,
        settings,
        daily_hold_days=(1, 3, 5, 10, 20),
    )

    summary = outputs["summary"]
    rank_ic = outputs["rank_ic"]
    rebalance = outputs["rebalance_returns"]
    assert isinstance(summary, pd.DataFrame)
    assert isinstance(rank_ic, pd.DataFrame)
    assert isinstance(rebalance, pd.DataFrame)
    assert calls == 2
    assert piece_calls == 2

    daily = summary[summary["factor_id"].eq("L2_DAILY")]
    monthly = summary[summary["factor_id"].eq("L2_MONTHLY")]
    assert len(daily) == 5 * 2
    assert set(daily["hold_days"].dropna().astype(int)) == {1, 3, 5, 10, 20}
    assert set(daily["horizon"]) == {
        "next_1_trading_day",
        "next_3_trading_days",
        "next_5_trading_days",
        "next_10_trading_days",
        "next_20_trading_days",
    }
    assert len(monthly) == 2
    assert monthly["hold_days"].isna().all()
    assert monthly["horizon"].eq("next_month").all()

    daily_ic = rank_ic[rank_ic["factor_id"].eq("L2_DAILY")]
    assert daily_ic["return_horizon"].eq(daily_ic["horizon"]).all()
    monthly_trades = rebalance[rebalance["factor_id"].eq("L2_MONTHLY")]
    assert monthly_trades["trade_frequency"].eq("monthly").all()
    assert monthly_trades["horizon"].eq("next_month").all()

    methodology = outputs["methodology"]
    assert isinstance(methodology, dict)
    assert methodology["rules"]["daily_hold_days"] == [1, 3, 5, 10, 20]
    assert methodology["counts"]["daily_factor_count"] == 1
    assert methodology["counts"]["monthly_factor_count"] == 1
    assert methodology["sources"]["factor_reading"] == (
        "单个 Parquet 只打开一次；每个 factor_id 的行组只读取一次"
    )


def test_each_horizon_matches_existing_single_run(tmp_path: Path) -> None:
    factor_path, settings = _write_inputs(tmp_path)
    multi = target.run_multi_horizon_from_parquet(
        factor_path,
        settings,
        daily_hold_days=(3,),
    )
    single_run = single.run_from_files(
        factor_path,
        replace_settings(settings, hold_days=3),
    )
    multi_summary = multi["summary"]
    single_summary = single_run["summary"]
    assert isinstance(multi_summary, pd.DataFrame)
    assert isinstance(single_summary, pd.DataFrame)
    compare_columns = [
        "factor_id",
        "frequency",
        "side",
        "cumulative_return",
        "sharpe",
        "max_drawdown",
        "mean_rank_ic",
        "rank_ic_ir",
    ]
    pd.testing.assert_frame_equal(
        multi_summary[compare_columns].reset_index(drop=True),
        single_summary[compare_columns].reset_index(drop=True),
        check_dtype=False,
    )


def replace_settings(
    settings: single.ShortBacktestSettings, **changes: object
) -> single.ShortBacktestSettings:
    values = {
        field: getattr(settings, field)
        for field in settings.__dataclass_fields__
    }
    values.update(changes)
    return single.ShortBacktestSettings(**values)


@pytest.mark.parametrize("values", [(), (0,), (1, 1)])
def test_hold_days_validation(values: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        target.normalize_daily_hold_days(values)
