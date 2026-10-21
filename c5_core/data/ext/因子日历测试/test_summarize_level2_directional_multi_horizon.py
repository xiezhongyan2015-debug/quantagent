# -*- coding: utf-8 -*-
"""方向汇总只使用合成数据。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import summarize_level2_directional_multi_horizon as target


def _write_factor_values(path: Path) -> None:
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    writer = None
    try:
        for factor_id, offset in [("FA", 0.0), ("FB", 0.0), ("FC", 10.0)]:
            rows = []
            for date_index, date in enumerate(dates):
                for code_index, code in enumerate(["sz000001", "sz000002"]):
                    rows.append(
                        {
                            "factor_id": factor_id,
                            "date": date,
                            "code": code,
                            "value": offset + date_index * 2 + code_index,
                        }
                    )
            table = pa.Table.from_pandas(pd.DataFrame(rows), preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(path, table.schema)
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    factor_values = tmp_path / "factor_values.parquet"
    _write_factor_values(factor_values)

    rebalance_rows = []
    for factor_id, frequency, hold_days, horizon in [
        ("FA", "daily", 1, "next_1_trading_day"),
        ("FB", "daily", 1, "next_1_trading_day"),
        ("FC", "monthly", pd.NA, "next_month"),
    ]:
        for period, signal_date in enumerate(
            pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        ):
            high_return = [0.10, -0.04, 0.06][period]
            low_return = [0.02, 0.01, -0.01][period]
            if factor_id == "FC":
                high_return, low_return = 0.03, 0.01
            for side, raw_return in [("high", high_return), ("low", low_return)]:
                rebalance_rows.append(
                    {
                        "factor_id": factor_id,
                        "frequency": frequency,
                        "hold_days": hold_days,
                        "horizon": horizon,
                        "signal_date": signal_date,
                        "entry_date": signal_date + pd.Timedelta(days=1),
                        "exit_date": signal_date + pd.Timedelta(days=2),
                        "side": side,
                        "raw_return": raw_return,
                        "transaction_cost": 0.001,
                        "turnover": 0.5,
                    }
                )
    pd.DataFrame(rebalance_rows).to_csv(raw_dir / "rebalance_returns.csv", index=False)

    rank_rows = []
    for factor_id, frequency, hold_days, horizon, values in [
        ("FA", "daily", 1, "next_1_trading_day", [0.1, 0.2, 0.3]),
        ("FB", "daily", 1, "next_1_trading_day", [0.1, 0.2, 0.3]),
        ("FC", "monthly", pd.NA, "next_month", [-0.1, -0.2, -0.3]),
    ]:
        for signal_date, rank_ic in zip(
            pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]), values
        ):
            rank_rows.append(
                {
                    "factor_id": factor_id,
                    "frequency": frequency,
                    "hold_days": hold_days,
                    "horizon": horizon,
                    "signal_date": signal_date,
                    "rank_ic": rank_ic,
                }
            )
    pd.DataFrame(rank_rows).to_csv(raw_dir / "rank_ic.csv", index=False)

    summary_rows = []
    for factor_id, frequency, hold_days, horizon in [
        ("FA", "daily", 1, "next_1_trading_day"),
        ("FB", "daily", 1, "next_1_trading_day"),
        ("FC", "monthly", pd.NA, "next_month"),
    ]:
        for side in ("high", "low"):
            summary_rows.append(
                {
                    "factor_id": factor_id,
                    "frequency": frequency,
                    "hold_days": hold_days,
                    "horizon": horizon,
                    "side": side,
                }
            )
    pd.DataFrame(summary_rows).to_csv(raw_dir / "summary.csv", index=False)
    (raw_dir / "methodology.json").write_text(
        json.dumps(
            {
                "sources": {"factor_input": [str(factor_values.resolve())]},
                "rules": {"buy_cost": 0.001, "sell_cost": 0.002},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    status_path = tmp_path / "factor_status.csv"
    pd.DataFrame(
        {
            "factor_id": ["FA", "FB", "FC"],
            "name": ["因子甲", "因子乙", "因子丙"],
            "frequency": ["daily", "daily", "monthly"],
            "implementation_quality": ["strict", "strict", "trial"],
        }
    ).to_csv(status_path, index=False)
    decisions_path = tmp_path / "direction_decisions.csv"
    pd.DataFrame(
        {
            "factor_id": ["FA", "FB", "FC"],
            "selected_direction": ["high", "high", "low"],
            "direction_reason": ["原文正向", "原文正向", "按经济含义判断"],
            "direction_confidence": ["明确", "明确", "推断"],
        }
    ).to_csv(decisions_path, index=False)
    return raw_dir, status_path, decisions_path


def test_directional_summary_costs_metrics_and_aliases(tmp_path: Path) -> None:
    raw_dir, status_path, decisions_path = _write_inputs(tmp_path)
    outputs = target.summarize_from_files(raw_dir, status_path, decisions_path)
    period = outputs["period_returns"]
    summary = outputs["summary"]
    aliases = outputs["aliases"]
    assert isinstance(period, pd.DataFrame)
    assert isinstance(summary, pd.DataFrame)
    assert isinstance(aliases, pd.DataFrame)

    fa_first = period[
        period["factor_id"].eq("FA")
        & period["signal_date"].eq(pd.Timestamp("2026-01-05"))
    ].iloc[0]
    assert fa_first["portfolio_rule"] == "long_high_short_low"
    assert np.isclose(fa_first["gross_return"], 0.08)
    # 做多成本0.001，做空成本=2*(0.001+0.002)*0.5-0.001=0.002。
    assert np.isclose(fa_first["transaction_cost"], 0.003)
    assert np.isclose(fa_first["net_return"], 0.077)

    fb_first = period[
        period["factor_id"].eq("FB")
        & period["signal_date"].eq(pd.Timestamp("2026-01-05"))
    ].iloc[0]
    assert fb_first["portfolio_rule"] == "long_high_short_low"
    assert np.isclose(fb_first["gross_return"], 0.08)
    assert np.isclose(fb_first["net_return"], 0.077)

    fa_summary = summary[summary["factor_id"].eq("FA")].iloc[0]
    expected_net = np.prod([1.077, 0.947, 1.067]) - 1.0
    assert np.isclose(fa_summary["cumulative_net_return"], expected_net)
    assert np.isclose(fa_summary["avg_transaction_cost"], 0.003)
    assert np.isclose(fa_summary["win_rate"], 2 / 3)
    assert np.isclose(fa_summary["directed_mean_rank_ic"], 0.2)
    assert np.isclose(fa_summary["directed_rank_ic_ir"], 2.0)
    assert fa_summary["direction_reason"] == "原文正向"
    assert fa_summary["name"] == "因子甲"

    fb_summary = summary[summary["factor_id"].eq("FB")].iloc[0]
    assert np.isclose(fb_summary["raw_mean_rank_ic"], 0.2)
    assert np.isclose(fb_summary["directed_mean_rank_ic"], 0.2)
    assert np.isclose(fb_summary["directed_rank_ic_ir"], 2.0)

    alias = aliases.set_index("factor_id")
    assert alias.loc["FA", "fingerprint"] == alias.loc["FB", "fingerprint"]
    assert alias.loc["FA", "alias_group"] == alias.loc["FB", "alias_group"]
    assert alias.loc["FA", "alias_count"] == 2
    assert alias.loc["FC", "alias_count"] == 1

    fc = summary[summary["factor_id"].eq("FC")].iloc[0]
    assert pd.isna(fc["hold_days"])
    assert fc["horizon"] == "next_month"
    assert np.isclose(fc["directed_mean_rank_ic"], 0.2)
    fc_first = period[
        period["factor_id"].eq("FC")
        & period["signal_date"].eq(pd.Timestamp("2026-01-05"))
    ].iloc[0]
    assert fc_first["portfolio_rule"] == "long_low_short_high"
    assert np.isclose(fc_first["gross_return"], -0.02)


def test_output_files_and_direction_columns_are_kept(tmp_path: Path) -> None:
    raw_dir, status_path, decisions_path = _write_inputs(tmp_path)
    outputs = target.summarize_from_files(raw_dir, status_path, decisions_path)
    output_dir = tmp_path / "output"
    target.write_outputs(outputs, output_dir)
    assert {path.name for path in output_dir.iterdir()} == {
        "directional_period_returns.csv",
        "directional_summary.csv",
        "factor_aliases.csv",
        "methodology.json",
    }
    written = pd.read_csv(output_dir / "directional_summary.csv")
    assert "direction_reason" in written.columns
    assert "direction_confidence" in written.columns
    assert set(written["frequency"]) == {"daily", "monthly"}
    assert set(written["horizon"]) == {"next_1_trading_day", "next_month"}


def test_missing_direction_is_rejected(tmp_path: Path) -> None:
    raw_dir, status_path, decisions_path = _write_inputs(tmp_path)
    decisions = pd.read_csv(decisions_path)
    decisions = decisions[~decisions["factor_id"].eq("FB")]
    decisions.to_csv(decisions_path, index=False)
    with pytest.raises(ValueError, match="缺少 1 个回测因子"):
        target.summarize_from_files(raw_dir, status_path, decisions_path)


def test_missing_side_pair_is_rejected(tmp_path: Path) -> None:
    raw_dir, _, _ = _write_inputs(tmp_path)
    rebalance = pd.read_csv(raw_dir / "rebalance_returns.csv")
    rebalance = rebalance.drop(rebalance.index[0])
    with pytest.raises(ValueError, match="必须同时有 high 和 low"):
        target.pair_rebalance_sides(rebalance)


def test_alias_direction_conflict_is_rejected() -> None:
    aliases = pd.DataFrame(
        {
            "factor_id": ["FA", "FB"],
            "alias_group": ["AG_same", "AG_same"],
        }
    )
    decisions = pd.DataFrame(
        {
            "factor_id": ["FA", "FB"],
            "selected_direction": ["high", "low"],
        }
    )
    with pytest.raises(ValueError, match="方向不一致"):
        target.validate_alias_directions(aliases, decisions)
