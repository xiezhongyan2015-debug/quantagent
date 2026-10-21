from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import historical_existing_duckdb_factors as existing
import run_historical_direct_duckdb_backtests as target


def _args(tmp_path: Path):
    return target.parse_args(
        [
            "--groups",
            "early_four",
            "july_august",
            "--raw-output-root",
            str(tmp_path / "raw"),
            "--output-dir",
            str(tmp_path / "out"),
            "--database",
            str(tmp_path / "factor.duckdb"),
            "--duckdb-temp-dir",
            str(tmp_path / "temp"),
        ]
    )


def test_all_direct_group_codes_are_present_in_current_modules() -> None:
    rows = target.inspect_coverage()
    assert [row.group_name for row in rows] == list(target.DIRECT_GROUPS)
    assert all(not row.missing_codes for row in rows)
    assert sum(len(row.expected_codes) for row in rows) == 52
    assert {
        row.group_name for row in rows if row.accepts_factor_codes
    } == {"july_august", "september_december"}


def test_build_commands_only_passes_factor_filter_to_supported_groups(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    units = pd.DataFrame(
        [
            {"group_name": "early_four", "result_code": "A06_DAZZLING_VOLATILITY_20D"},
            {"group_name": "july_august", "result_code": "J07_D14_UPSIDE_RV"},
        ]
    )
    commands = target.build_commands(units, args)
    assert "--factor-codes" not in commands["early_four"]
    july = commands["july_august"]
    assert july[july.index("--factor-codes") + 1 :] == ["J07_D14_UPSIDE_RV"]
    assert july[july.index("--duckdb-threads") + 1] == "2"
    assert july[july.index("--end") + 1] == "2024-12-31"


def test_normalize_results_matches_new_runner_shapes(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "september_december"
    raw.mkdir(parents=True)
    pd.DataFrame(
        [{"factor": "J09_D04_EMOTION_SPILLOVER", "finite_values": 1234}]
    ).to_csv(raw / "factor_status.csv", index=False, encoding="utf-8-sig")
    returns = pd.DataFrame(
        {
            "factor": ["J09_D04_EMOTION_SPILLOVER"] * 3,
            "signal_frequency": ["daily"] * 3,
            "signal_date": ["2021-12-31", "2022-12-30", "2023-12-29"],
            "entry_date": ["2022-01-04", "2023-01-03", "2024-01-02"],
            "exit_date": ["2022-01-11", "2023-01-10", "2024-01-09"],
            "selected_direction": ["high"] * 3,
            "selected_net_return": [0.10, -0.05, 0.20],
            "selected_turnover": [0.5, 0.4, 0.6],
            "benchmark_return": [0.02, -0.01, 0.03],
            "group_stocks": [100, 110, 120],
        }
    )
    returns.to_csv(
        raw / "original_frequency_rebalance_returns.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(
        {
            "factor": ["J09_D04_EMOTION_SPILLOVER"] * 3,
            "signal_date": returns["signal_date"],
            "ic": [0.01, 0.02, 0.03],
        }
    ).to_csv(
        raw / "original_frequency_ic.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(
        [
            {
                "factor": "J09_D04_EMOTION_SPILLOVER",
                "period": "full",
                "in_sample_ic": 0.015,
            }
        ]
    ).to_csv(
        raw / "original_frequency_backtest_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    units = pd.DataFrame(
        [
            {
                "unit_id": "HFTEST__daily",
                "formula_key": "HFTEST",
                "representative_name": "测试因子",
                "normalized_frequency": "daily",
                "result_code": "J09_D04_EMOTION_SPILLOVER",
                "group_name": "september_december",
            }
        ]
    )
    output = tmp_path / "out"
    counts = target.normalize_results(
        units,
        tmp_path / "raw",
        output,
        coverage=[],
    )
    assert counts == {"selected": 1, "completed": 1, "failed": 0}
    status = pd.read_csv(output / "unit_status.csv", encoding="utf-8-sig")
    summary = pd.read_csv(output / "unit_backtest_summary.csv", encoding="utf-8-sig")
    yearly = pd.read_csv(output / "unit_yearly_returns.csv", encoding="utf-8-sig")
    assert status.columns.tolist() == target.STATUS_COLUMNS
    assert status.loc[0, "finite_values"] == 1234
    assert status.loc[0, "periods_2022_2024"] == 3
    assert summary.columns.tolist() == target.SUMMARY_COLUMNS
    assert np.isclose(summary.loc[0, "cumulative_return"], 1.10 * 0.95 * 1.20 - 1)
    assert yearly.columns.tolist() == target.YEARLY_COLUMNS
    assert yearly["year"].tolist() == [2022, 2023, 2024]


def test_missing_raw_result_becomes_failed_status(tmp_path: Path) -> None:
    units = pd.DataFrame(
        [
            {
                "unit_id": "HFTEST__daily",
                "formula_key": "HFTEST",
                "representative_name": "测试因子",
                "normalized_frequency": "daily",
                "result_code": "A06_DAZZLING_VOLATILITY_20D",
                "group_name": "early_four",
            }
        ]
    )
    output = tmp_path / "out"
    counts = target.normalize_results(units, tmp_path / "raw", output, coverage=[])
    status = pd.read_csv(output / "unit_status.csv", encoding="utf-8-sig")
    assert counts["failed"] == 1
    assert status.loc[0, "status"] == "failed"
    assert "缺少" in status.loc[0, "error"]


def test_unresolved_mapping_refs_remain_reported() -> None:
    assert set(existing.UNRESOLVED_IMPLEMENTATION_REFS).issubset(
        existing.unresolved_mapping_refs()
    )
