from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import assemble_minute_completion_results as assembler
from assemble_minute_completion_results import GROUP_SPECS
import run_2026_minute_completion_check as checker


def _result_mapping(root: Path) -> dict[str, Path]:
    return {
        "early_strict_dir": root / "early_four",
        "julaug_strict_dir": root / "july_august",
        "sepdec_strict_dir": root / "september_december",
        "early_sep_trial_dir": root / "early_trial",
        "julaug_trial_dir": root / "july_august",
        "octdec_trial_dir": root / "octdec_trial",
    }


def _write_synthetic_results(root: Path) -> dict[str, Path]:
    mapping = _result_mapping(root)
    codes_by_directory: dict[Path, list[str]] = {}
    for group in GROUP_SPECS:
        codes_by_directory.setdefault(mapping[group.argument_name], []).extend(group.codes)
    exits = pd.to_datetime(
        ["2026-01-30", "2026-02-27", "2026-03-31", "2026-04-30", "2026-05-29"]
    )
    for directory, codes in codes_by_directory.items():
        directory.mkdir(parents=True, exist_ok=True)
        raw_rows = []
        status_rows = []
        for code_index, code in enumerate(codes):
            status_rows.append({"factor": code, "name": f"名称{code}", "calculated": True})
            for month_index, exit_date in enumerate(exits):
                raw_rows.append(
                    {
                        "factor": code,
                        "signal_date": exit_date - pd.Timedelta(days=30),
                        "entry_date": exit_date - pd.Timedelta(days=25),
                        "exit_date": exit_date,
                        "ic": (code_index + 1) * 0.001 + month_index * 0.01,
                        "stock_count": 100 + code_index,
                        "signal_frequency": "monthly",
                        "return_horizon": "next_month",
                    }
                )
        pd.DataFrame(raw_rows).to_csv(
            directory / "original_frequency_ic.csv", index=False
        )
        pd.DataFrame(status_rows).to_csv(directory / "factor_status.csv", index=False)
    return mapping


def _main_arguments(
    mapping: dict[str, Path], output_dir: Path, database: Path
) -> list[str]:
    return [
        "--skip-runs",
        "--early-four-result-dir",
        str(mapping["early_strict_dir"]),
        "--july-august-result-dir",
        str(mapping["julaug_strict_dir"]),
        "--september-december-result-dir",
        str(mapping["sepdec_strict_dir"]),
        "--early-trial-result-dir",
        str(mapping["early_sep_trial_dir"]),
        "--octdec-trial-result-dir",
        str(mapping["octdec_trial_dir"]),
        "--output-dir",
        str(output_dir),
        "--database",
        str(database),
    ]


def test_group_specs_are_single_source_of_51_codes() -> None:
    expected = checker.expected_factor_frame()
    assert len(expected) == 51
    assert expected["factor"].nunique() == 51
    assert expected["formula_mode"].value_counts().to_dict() == {
        "trial": 27,
        "strict": 24,
    }


def test_recent_output_names_match_assemble_and_workbook_reader() -> None:
    names = (
        checker.RAW_OUTPUT,
        checker.MONTHLY_OUTPUT,
        checker.SUMMARY_OUTPUT,
        checker.QUALITY_OUTPUT,
    )
    assert names == assembler.RECENT_2026_FILES
    workbook_common = (
        Path(__file__).resolve().parent
        / ".codex-artifact"
        / "minute_completion"
        / "minute_completion_common.mjs"
    ).read_text(encoding="utf-8")
    for name in names:
        assert workbook_common.count(f'"{name}"') == 1


def test_skip_runs_summarizes_synthetic_csv(tmp_path: Path) -> None:
    mapping = _write_synthetic_results(tmp_path / "runs")
    output_dir = tmp_path / "summary"
    result = checker.main(
        _main_arguments(mapping, output_dir, tmp_path / "independent.duckdb")
    )
    assert result == 0
    raw = pd.read_csv(output_dir / checker.RAW_OUTPUT)
    monthly = pd.read_csv(output_dir / checker.MONTHLY_OUTPUT)
    summary = pd.read_csv(output_dir / checker.SUMMARY_OUTPUT)
    quality = pd.read_csv(output_dir / checker.QUALITY_OUTPUT)
    assert len(raw) == 255
    assert len(monthly) == 255
    assert len(summary) == 51
    assert summary["factor"].nunique() == 51
    assert summary["formula_mode"].value_counts().to_dict() == {
        "trial": 27,
        "strict": 24,
    }
    assert raw["exit_date"].min() == "2026-01-30"
    assert raw["exit_date"].max() == "2026-05-29"
    assert quality["status"].eq("通过").all()


def test_missing_factor_code_has_clear_error(tmp_path: Path) -> None:
    mapping = _write_synthetic_results(tmp_path / "runs")
    raw_path = mapping["early_strict_dir"] / "original_frequency_ic.csv"
    raw = pd.read_csv(raw_path)
    missing_code = GROUP_SPECS[0].codes[0]
    raw[raw["factor"].ne(missing_code)].to_csv(raw_path, index=False)
    args = checker.parse_args(
        _main_arguments(
            mapping, tmp_path / "summary", tmp_path / "independent.duckdb"
        )
    )
    with pytest.raises(checker.CompletionCheckError, match=missing_code):
        checker.load_recent_raw_ic(args)


def test_entrypoint_calls_use_fixed_dates_and_all_five_modules(tmp_path: Path) -> None:
    args = checker.parse_args(
        [
            "--database",
            str(tmp_path / "independent.duckdb"),
            "--output-dir",
            str(tmp_path / "summary"),
        ]
    )
    calls = checker.build_entrypoint_calls(args)
    assert [call.module for call in calls] == [
        "run_early_four_minute_backtest",
        "run_july_august_backtest",
        "run_september_december_backtest",
        "run_trial_minute_backtest",
        "run_october_december_trial_backtest",
    ]
    for call in calls:
        arguments = list(call.arguments)
        assert arguments[arguments.index("--start") + 1] == "2025-06-02"
        assert arguments[arguments.index("--backtest-start") + 1] == "2025-12-01"
        assert arguments[arguments.index("--end") + 1] == "2026-05-29"
        assert arguments[arguments.index("--database") + 1] == str(args.database)
    julaug_arguments = list(calls[1].arguments)
    assert julaug_arguments[julaug_arguments.index("--factor-codes") + 1 :] == list(
        GROUP_SPECS[1].codes + GROUP_SPECS[4].codes
    )
    sepdec_arguments = list(calls[2].arguments)
    assert sepdec_arguments[sepdec_arguments.index("--factor-codes") + 1 :] == list(
        GROUP_SPECS[2].codes
    )


def test_defaults_use_full_pipeline_cache_directories() -> None:
    args = checker.parse_args([])
    assert Path(args.market_stats) == checker.FULL_PIPELINE_ROOT / "shared_market" / "yearly"
    assert Path(args.july_august_cache_glob).parent == (
        checker.FULL_PIPELINE_ROOT / "july_august" / "daily_by_stock"
    )
    assert Path(args.network_root) == (
        checker.FULL_PIPELINE_ROOT / "october_december_trial" / "network"
    )


def _write_input_layout(tmp_path: Path, codes: tuple[str, ...]) -> list[str]:
    arguments = [
        "--database",
        str(tmp_path / "independent.duckdb"),
        "--base-cache-glob",
        str(tmp_path / "base" / "*.parquet"),
        "--may-cache-glob",
        str(tmp_path / "may" / "*.parquet"),
        "--june-cache-glob",
        str(tmp_path / "june" / "*.parquet"),
        "--v3-cache-glob",
        str(tmp_path / "v3" / "*.parquet"),
        "--july-august-cache-glob",
        str(tmp_path / "julaug" / "*.parquet"),
        "--lone-goose-cache-glob",
        str(tmp_path / "lone" / "*.parquet"),
        "--early-trial-cache-dir",
        str(tmp_path / "early"),
        "--octdec-trial-cache-dir",
        str(tmp_path / "octdec"),
        "--network-root",
        str(tmp_path / "network"),
        "--market-stats",
        str(tmp_path / "market"),
        "--qlib-dir",
        str(tmp_path / "qlib"),
    ]
    stock_dirs = ["base", "may", "june", "v3", "julaug", "early", "octdec"]
    for name in stock_dirs:
        directory = tmp_path / name
        directory.mkdir(parents=True)
        for code in codes:
            (directory / f"{code}.parquet").write_bytes(b"test")
    (tmp_path / "lone").mkdir()
    (tmp_path / "lone" / "lone_goose_2026.parquet").write_bytes(b"test")
    (tmp_path / "market").mkdir()
    (tmp_path / "market" / "shared_market_2026.parquet").write_bytes(b"test")
    for part in ("nodes", "edges"):
        directory = tmp_path / "network" / part
        directory.mkdir(parents=True)
        (directory / "20260529.parquet").write_bytes(b"test")
    for relative in (
        Path("calendars/day.txt"),
        Path("instruments/csi800.txt"),
        Path("instruments/csi1000.txt"),
    ):
        path = tmp_path / "qlib" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("2026-05-29\n", encoding="utf-8")
    feature = tmp_path / "qlib" / "features" / "sh000985" / "close.day.bin"
    feature.parent.mkdir(parents=True, exist_ok=True)
    feature.write_bytes(b"test")
    return arguments


def test_input_check_accepts_yearly_market_directory_and_rejects_partial_stock_cache(
    tmp_path: Path,
) -> None:
    arguments = _write_input_layout(tmp_path, ("sh600000", "sz000001"))
    args = checker.parse_args(arguments)
    checker.validate_run_inputs(args)

    (tmp_path / "v3" / "sz000001.parquet").unlink()
    with pytest.raises(checker.CompletionCheckError, match="v3分钟日值缓存缺少1只"):
        checker.validate_run_inputs(args)


def test_input_check_accepts_compact_network_without_edges(tmp_path: Path) -> None:
    arguments = _write_input_layout(tmp_path, ("sh600000",))
    node_path = tmp_path / "network" / "nodes" / "20260529.parquet"
    pd.DataFrame(
        {
            "snapshot_date": [pd.Timestamp("2026-05-29")],
            "code": ["sh600000"],
            "peer_daily_return": [0.01],
            "peer_negative_jump20": [-0.02],
            "peer_nonjump20": [0.03],
        }
    ).to_parquet(node_path, index=False)
    (tmp_path / "network" / "edges" / "20260529.parquet").unlink()
    (tmp_path / "network" / "network_manifest.json").write_text(
        '{"output_mode":"compact","edge_files_written":0}', encoding="utf-8"
    )

    args = checker.parse_args(arguments)
    checker.validate_run_inputs(args)

    frame = pd.read_parquet(node_path).drop(columns=["peer_nonjump20"])
    frame.to_parquet(node_path, index=False)
    with pytest.raises(checker.CompletionCheckError, match="peer_nonjump20"):
        checker.validate_run_inputs(args)


def test_source_coverage_uses_first_trading_day_after_natural_start(
    tmp_path: Path,
) -> None:
    arguments = _write_input_layout(tmp_path, ("sh600000",))
    dates = pd.to_datetime(["2025-06-03", "2026-05-29"])
    stock_frame = pd.DataFrame({"date": dates, "code": "sh600000"})
    for name in ("base", "may", "june", "v3", "julaug", "early", "octdec"):
        stock_frame.to_parquet(tmp_path / name / "sh600000.parquet", index=False)
    stock_frame.to_parquet(
        tmp_path / "lone" / "lone_goose_2026.parquet", index=False
    )
    pd.DataFrame({"date": dates}).to_parquet(
        tmp_path / "market" / "shared_market_2026.parquet", index=False
    )
    pd.DataFrame({"snapshot_date": dates, "code": "sh600000"}).to_parquet(
        tmp_path / "network" / "nodes" / "20260529.parquet", index=False
    )
    calendar = tmp_path / "qlib" / "calendars" / "day.txt"
    calendar.write_text(
        "2025-05-30\n2025-06-03\n2026-05-29\n", encoding="utf-8"
    )

    args = checker.parse_args(arguments)
    coverage = checker.register_and_check_sources(args)

    assert set(coverage["source_name"]) == {
        "base_daily",
        "may_daily",
        "june_daily",
        "minute_extra_daily",
        "july_august_daily",
        "lone_goose_daily",
        "early_trial_daily",
        "octdec_trial_daily",
        "shared_market_minute_stats",
        "octdec_network_nodes",
    }
    assert pd.to_datetime(coverage["minimum_date"]).min() == pd.Timestamp(
        "2025-06-03"
    )


def test_dry_run_does_not_require_or_create_cache_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "summary"
    result = checker.main(
        [
            "--dry-run",
            "--database",
            str(tmp_path / "independent.duckdb"),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert result == 0
    assert not output_dir.exists()
