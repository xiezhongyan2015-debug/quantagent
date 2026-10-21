# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

import assemble_minute_completion_results as assembler
import build_combined_minute_trial_features_by_stock as combined_trial_builder
import build_july_august_minute_features as july_builder
import build_minute_daily_extra_by_stock as v3_builder
import build_october_december_trial_daily as octdec_daily
import build_october_december_trial_network as octdec_network
import build_shared_market_minute_stats as shared_market
import build_trial_minute_features as early_trial
import run_early_four_minute_backtest as early_backtest
import run_july_august_backtest as julaug_backtest
import run_minute_completion_full_pipeline as target
import run_october_december_trial_backtest as octdec_backtest
import run_september_december_backtest as sepdec_backtest
import run_trial_minute_backtest as trial_backtest


def test_stage_commands_cover_dates_resources_and_outputs(tmp_path: Path) -> None:
    minute_dir = tmp_path / "minute"
    base_glob = tmp_path / "base" / "*.parquet"
    may_glob = tmp_path / "may" / "*.parquet"
    june_glob = tmp_path / "june" / "*.parquet"
    args = target.parse_args(
        [
            "--run-root",
            str(tmp_path / "runs"),
            "--final-output",
            str(tmp_path / "final"),
            "--recent-2026-dir",
            str(tmp_path / "recent_2026"),
            "--minute-data-dir",
            str(minute_dir),
            "--base-cache-glob",
            str(base_glob),
            "--may-cache-glob",
            str(may_glob),
            "--june-cache-glob",
            str(june_glob),
            "--cache-start",
            "2015-01-01",
            "--cache-end",
            "2026-05-29",
            "--backtest-start",
            "2016-01-04",
            "--backtest-end",
            "2025-12-31",
            "--workers",
            "4",
            "--threads",
            "6",
            "--memory-limit",
            "4GB",
            "--worker-memory-limit",
            "1GB",
            "--lone-goose-stock-batch-size",
            "17",
        ]
    )
    paths = target.make_paths(args)
    stages = target.build_stage_specs(args, paths)
    by_name = {stage.name: list(stage.command) for stage in stages}

    assert tuple(stage.name for stage in stages) == target.STAGE_NAMES
    for name in ["v3_cache", "julaug_stock_daily", "lone_goose_yearly"]:
        command = by_name[name]
        assert command[command.index("--minute-data-dir") + 1] == str(
            minute_dir.resolve()
        )
        assert command[command.index("--base-cache-glob") + 1] == str(
            base_glob.resolve()
        )
    assert "--yearly-dir" in by_name["shared_market_yearly"]
    assert str(paths.shared_yearly) in by_name["shared_market_yearly"]
    assert by_name["julaug_stock_daily"][
        by_name["julaug_stock_daily"].index("--universe") + 1
    ] == "base"
    assert by_name["lone_goose_yearly"][
        by_name["lone_goose_yearly"].index("--lone-goose-stock-batch-size") + 1
    ] == "17"
    assert "--stocks-only" in by_name["early9_trial_daily"]
    assert by_name["network_252"][
        by_name["network_252"].index("--window") + 1
    ] == "252"
    assert by_name["network_252"][
        by_name["network_252"].index("--threads") + 1
    ] == "6"
    assert by_name["network_252"][
        by_name["network_252"].index("--output-mode") + 1
    ] == "compact"
    assert by_name["network_252"][
        by_name["network_252"].index("--base-cache-glob") + 1
    ] == str(base_glob.resolve())
    for name in [
        "backtest_early4",
        "backtest_julaug",
        "backtest_sepdec",
        "backtest_early9_trial",
        "backtest_octdec_trial",
    ]:
        command = by_name[name]
        assert command[command.index("--start") + 1] == "2015-01-01"
        assert command[command.index("--backtest-start") + 1] == "2016-01-04"
        assert command[command.index("--end") + 1] == "2025-12-31"
        assert command[command.index("--duckdb-threads") + 1] == "6"
        assert command[command.index("--duckdb-memory-limit") + 1] == "4GB"
    julaug_command = by_name["backtest_julaug"]
    assert julaug_command[julaug_command.index("--base-cache-glob") + 1] == str(
        base_glob.resolve()
    )
    assert julaug_command[julaug_command.index("--may-cache-glob") + 1] == str(
        may_glob.resolve()
    )
    assert julaug_command[julaug_command.index("--june-cache-glob") + 1] == str(
        june_glob.resolve()
    )
    julaug_codes = julaug_command[
        julaug_command.index("--factor-codes") + 1 : julaug_command.index("--output-dir")
    ]
    assert julaug_codes == list(
        assembler.GROUP_SPECS[1].codes + assembler.GROUP_SPECS[4].codes
    )
    sepdec_command = by_name["backtest_sepdec"]
    sepdec_codes = sepdec_command[
        sepdec_command.index("--factor-codes") + 1 : sepdec_command.index("--output-dir")
    ]
    assert sepdec_codes == list(assembler.GROUP_SPECS[2].codes)

    assemble_command = by_name["assemble"]
    assert assemble_command[
        assemble_command.index("--julaug-strict-dir") + 1
    ] == str(paths.backtest_julaug)
    assert assemble_command[
        assemble_command.index("--julaug-trial-dir") + 1
    ] == str(paths.backtest_julaug)
    assert assemble_command[assemble_command.index("--output-dir") + 1] == str(
        paths.final_output
    )
    assert assemble_command[
        assemble_command.index("--recent-2026-dir") + 1
    ] == str((tmp_path / "recent_2026").resolve())

    parsers = {
        "v3_cache": v3_builder.parse_args,
        "shared_market_yearly": shared_market.parse_args,
        "julaug_stock_daily": combined_trial_builder.parse_args,
        "lone_goose_yearly": july_builder.parse_args,
        "early9_trial_daily": early_trial.parse_args,
        "octdec_trial_daily": octdec_daily.parse_args,
        "network_252": octdec_network.parse_args,
        "backtest_early4": early_backtest.parse_args,
        "backtest_julaug": julaug_backtest.parse_args,
        "backtest_sepdec": sepdec_backtest.parse_args,
        "backtest_early9_trial": trial_backtest.parse_args,
        "backtest_octdec_trial": octdec_backtest.parse_args,
        "assemble": assembler.parse_args,
    }
    for stage in stages:
        parsed = parsers[stage.name](list(stage.command[2:]))
        assert parsed is not None


def test_dry_run_writes_commands_without_starting_stages(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    result = target.main(
        [
            "--run-root",
            str(run_root),
            "--final-output",
            str(tmp_path / "final"),
            "--from-stage",
            "shared_market_yearly",
            "--to-stage",
            "early9_trial_daily",
            "--dry-run",
        ]
    )
    assert result == 0
    status_files = sorted((run_root / "status").glob("*.json"))
    assert len(status_files) == 4
    for path in status_files:
        status = json.loads(path.read_text(encoding="utf-8"))
        assert status["status"] == "dry_run"
        assert status["exit_code"] is None
        assert status["command"][0] == sys.executable
    assert not list(run_root.rglob("*.parquet"))
    overall = json.loads(
        (run_root / "pipeline_status.json").read_text(encoding="utf-8")
    )
    assert overall["status"] == "dry_run"


def test_failed_command_records_exit_code_and_stops_stage(tmp_path: Path) -> None:
    args = target.parse_args(
        ["--run-root", str(tmp_path / "runs"), "--dry-run"]
    )
    paths = target.make_paths(args)
    stage = target.StageSpec(
        name="failure_test",
        description="安全失败测试",
        command=(sys.executable, "-c", "raise SystemExit(7)"),
        check=lambda: target.CheckResult(False, "测试产物不存在", {}),
    )

    success, record = target._execute_stage(stage, 0, paths, dry_run=False)

    assert not success
    assert record["status"] == "failed"
    assert record["exit_code"] == 7
    status_path = paths.statuses / "01_failure_test.json"
    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["exit_code"] == 7
    assert saved["command"] == list(stage.command)


def test_backtest_status_check_requires_every_file_and_code(tmp_path: Path) -> None:
    directory = tmp_path / "backtest"
    directory.mkdir()
    codes = assembler.GROUP_SPECS[0].codes
    for filename in assembler.REQUIRED_CSV_FILES:
        frame = pd.DataFrame({"factor": codes, "value": range(len(codes))})
        if filename == "factor_status.csv":
            frame["calculated"] = True
        frame.to_csv(directory / filename, index=False)
    (directory / "methodology.json").write_text("{}", encoding="utf-8")

    complete = target.check_backtest_result(directory, codes)
    assert complete.complete

    broken = pd.read_csv(directory / "original_frequency_ic.csv").iloc[1:]
    broken.to_csv(directory / "original_frequency_ic.csv", index=False)
    incomplete = target.check_backtest_result(directory, codes)
    assert not incomplete.complete
    assert "缺代码" in "|".join(incomplete.details["errors"])

    pd.DataFrame({"factor": codes, "value": range(len(codes))}).to_csv(
        directory / "original_frequency_ic.csv", index=False
    )
    status = pd.read_csv(directory / "factor_status.csv")
    status.loc[status["factor"].eq(codes[0]), "calculated"] = False
    status.to_csv(directory / "factor_status.csv", index=False)
    not_calculated = target.check_backtest_result(directory, codes)
    assert not not_calculated.complete
    assert codes[0] in "|".join(not_calculated.details["errors"])


def test_primary_backtest_sources_are_re_registered_from_requested_paths(
    tmp_path: Path, monkeypatch
) -> None:
    base_glob = tmp_path / "base" / "*.parquet"
    v3_dir = tmp_path / "v3"
    database = tmp_path / "independent.duckdb"
    args = target.parse_args(
        [
            "--run-root",
            str(tmp_path / "runs"),
            "--base-cache-glob",
            str(base_glob),
            "--v3-cache-dir",
            str(v3_dir),
            "--database",
            str(database),
        ]
    )
    captured = {}

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(target.factor_store, "connect", lambda settings: FakeConnection())

    def fake_register(connection, sources):
        captured.update(sources)
        return pd.DataFrame({"source_name": list(sources)})

    monkeypatch.setattr(target.factor_store, "register_sources", fake_register)
    registered = target._register_primary_backtest_sources(args)

    assert registered["source_name"].tolist() == [
        "base_daily",
        "minute_extra_daily",
    ]
    assert captured["base_daily"] == base_glob.resolve()
    assert captured["minute_extra_daily"] == v3_dir.resolve() / "*.parquet"


def test_minute_builders_use_requested_raw_and_base_directories(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    minute_dir = tmp_path / "minute"
    base_dir.mkdir()
    minute_dir.mkdir()
    (base_dir / "sh600000.parquet").write_bytes(b"base")
    (base_dir / "sz000001.parquet").write_bytes(b"base")
    (minute_dir / "sh600000.parquet").write_bytes(b"minute")

    base_glob = base_dir / "*.parquet"
    v3_files, missing = v3_builder._source_files(base_glob, minute_dir)
    july_files = july_builder._source_files("base", minute_dir, base_glob)

    assert [path.stem for path in v3_files] == ["sh600000"]
    assert missing == ["sz000001"]
    assert [path.stem for path in july_files] == ["sh600000"]


def _write_one_row_parquet(
    path: Path,
    columns: list[str],
    marker: tuple[str, object] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {column: 0 for column in columns}
    if marker is not None:
        row[marker[0]] = marker[1]
    pd.DataFrame([row], columns=columns).to_parquet(path, index=False)


def test_combined_stage_completion_makes_later_stock_stages_skip(
    tmp_path: Path, monkeypatch
) -> None:
    base_dir = tmp_path / "base"
    minute_dir = tmp_path / "minute"
    base_dir.mkdir()
    minute_dir.mkdir()
    codes = ("sh600000", "sz000001")
    for code in codes:
        (base_dir / f"{code}.parquet").write_bytes(b"base")
        (minute_dir / f"{code}.parquet").write_bytes(b"minute")
    monkeypatch.setattr(target, "EXPECTED_BASE_STOCK_COUNT", len(codes))
    args = target.parse_args(
        [
            "--run-root",
            str(tmp_path / "runs"),
            "--base-cache-glob",
            str(base_dir / "*.parquet"),
            "--minute-data-dir",
            str(minute_dir),
        ]
    )
    paths = target.make_paths(args)
    for code in codes:
        _write_one_row_parquet(
            paths.julaug_stock / f"{code}.parquet",
            july_builder.STOCK_OUTPUT_COLUMNS,
        )
        _write_one_row_parquet(
            paths.early_trial_daily / f"{code}.parquet",
            early_trial.OUTPUT_COLUMNS,
            ("trial_cache_version", early_trial.CACHE_VERSION),
        )
        _write_one_row_parquet(
            paths.octdec_daily / f"{code}.parquet",
            octdec_daily.OUTPUT_COLUMNS,
            ("trial_cache_version", octdec_daily.CACHE_VERSION),
        )
    july_manifest = paths.run_root / "july_august" / "stock_manifest.json"
    july_manifest.parent.mkdir(parents=True, exist_ok=True)
    july_manifest.write_text(
        json.dumps(
            {
                "start": args.cache_start,
                "end": args.cache_end,
                "base_cache_glob": str(Path(args.base_cache_glob).resolve()),
                "minute_data_dir": str(minute_dir.resolve()),
                "shared_market_file": str(paths.shared_yearly.resolve()),
                "source_files": len(codes),
                "stock": {
                    "status": "completed",
                    "requested_files": len(codes),
                    "failures": [],
                },
            }
        ),
        encoding="utf-8",
    )
    early_manifest = paths.run_root / "early9_trial" / "daily_manifest.json"
    early_manifest.parent.mkdir(parents=True, exist_ok=True)
    early_manifest.write_text(
        json.dumps(
            {
                "start": args.cache_start,
                "end": args.cache_end,
                "stock_source_file_count": len(codes),
                "completed": len(codes),
                "skipped_current": 0,
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    octdec_manifest = (
        paths.run_root / "october_december_trial" / "daily_manifest.json"
    )
    octdec_manifest.parent.mkdir(parents=True, exist_ok=True)
    octdec_manifest.write_text(
        json.dumps(
            {
                "start": args.cache_start,
                "end": args.cache_end,
                "requested_files": len(codes),
                "completed_outputs": len(codes),
                "failures": [],
            }
        ),
        encoding="utf-8",
    )

    stages = target.build_stage_specs(args, paths)
    combined = stages[target.STAGE_NAMES.index("julaug_stock_daily")].check()
    assert combined.complete
    for stage_name in ("early9_trial_daily", "octdec_trial_daily"):
        index = target.STAGE_NAMES.index(stage_name)
        success, record = target._execute_stage(
            stages[index], index, paths, dry_run=False
        )
        assert success
        assert record["status"] == "skipped"

    (paths.early_trial_daily / f"{codes[0]}.parquet").unlink()
    assert not stages[target.STAGE_NAMES.index("julaug_stock_daily")].check().complete


def test_v3_process_detection_includes_incremental_builder() -> None:
    assert target._is_v3_build_command(
        "python build_minute_daily_extra_by_stock.py --workers 3"
    )
    assert target._is_v3_build_command(
        "python build_minute_daily_extra_incremental_by_stock.py --workers 3"
    )
    assert not target._is_v3_build_command("python unrelated_backtest.py")


def test_standard_v3_manifest_and_files_are_accepted(
    tmp_path: Path, monkeypatch
) -> None:
    base_dir = tmp_path / "base"
    minute_dir = tmp_path / "minute"
    v3_dir = tmp_path / "v3"
    for directory in (base_dir, minute_dir, v3_dir):
        directory.mkdir()
    codes = ("sh600000", "sz000001")
    columns = [
        "date",
        "code",
        "high_volume_realized_skew_daily",
        "dazzling_volatility_daily",
        "p_shaped_volume_distribution_daily",
        "top_return_time_1",
        "top_return_time_2",
        "top_return_time_3",
        "top_return_time_4",
        "top_return_time_5",
        "adj_lag_absret_amount_corr_daily",
    ]
    for code in codes:
        (base_dir / f"{code}.parquet").write_bytes(b"base")
        (minute_dir / f"{code}.parquet").write_bytes(b"minute")
        _write_one_row_parquet(v3_dir / f"{code}.parquet", columns)
    manifest = tmp_path / "v3_manifest.json"
    monkeypatch.setattr(target, "EXPECTED_BASE_STOCK_COUNT", len(codes))
    args = target.parse_args(
        [
            "--base-cache-glob",
            str(base_dir / "*.parquet"),
            "--minute-data-dir",
            str(minute_dir),
            "--v3-cache-dir",
            str(v3_dir),
            "--v3-manifest",
            str(manifest),
        ]
    )
    manifest.write_text(
        json.dumps(
            {
                "cache_version": 3,
                "start": args.cache_start,
                "end": args.cache_end,
                "cache_dir": str(v3_dir.resolve()),
                "base_cache_glob": str((base_dir / "*.parquet").resolve()),
                "minute_data_dir": str(minute_dir.resolve()),
                "requested_files": len(codes),
                "completed_outputs": len(codes),
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    assert target._check_v3(args).complete
