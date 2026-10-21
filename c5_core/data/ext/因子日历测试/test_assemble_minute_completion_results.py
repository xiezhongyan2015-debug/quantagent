# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import assemble_minute_completion_results as target


ARGUMENT_FLAGS = {
    "early_strict_dir": "--early-strict-dir",
    "julaug_strict_dir": "--julaug-strict-dir",
    "sepdec_strict_dir": "--sepdec-strict-dir",
    "early_sep_trial_dir": "--early-sep-trial-dir",
    "julaug_trial_dir": "--julaug-trial-dir",
    "octdec_trial_dir": "--octdec-trial-dir",
}


def _write_group(
    directory: Path,
    codes: tuple[str, ...],
    *,
    extra_code: str | None = None,
) -> None:
    directory.mkdir(parents=True)
    rows = list(codes)
    if extra_code is not None:
        rows.append(extra_code)
    for filename in target.REQUIRED_CSV_FILES:
        frame = pd.DataFrame(
            {
                "factor": rows,
                "sample_value": range(len(rows)),
                "source_filename": filename,
            }
        )
        frame.to_csv(directory / filename, index=False, encoding="utf-8-sig")


def _make_inputs(
    root: Path,
    *,
    extra_group: str | None = None,
) -> dict[str, Path]:
    directories = {}
    for group in target.GROUP_SPECS:
        directory = root / group.argument_name
        _write_group(
            directory,
            group.codes,
            extra_code="OLD_FACTOR_NOT_IN_COMPLETION"
            if group.argument_name == extra_group
            else None,
        )
        directories[group.argument_name] = directory
    return directories


def _argv(directories: dict[str, Path], output_dir: Path) -> list[str]:
    argv: list[str] = []
    for argument_name, flag in ARGUMENT_FLAGS.items():
        argv.extend([flag, str(directories[argument_name])])
    argv.extend(["--output-dir", str(output_dir)])
    return argv


def _write_recent_2026(directory: Path) -> None:
    directory.mkdir(parents=True)
    rows = [
        (code, group.formula_mode)
        for group in target.GROUP_SPECS
        for code in group.codes
    ]
    months = [f"2026-{month:02d}" for month in range(1, 6)]
    raw_rows = []
    monthly_rows = []
    for code_index, (code, mode) in enumerate(rows):
        for month_index, month in enumerate(months, start=1):
            exit_date = pd.Timestamp(2026, month_index, 28)
            raw_rows.append(
                {
                    "factor": code,
                    "factor_name": code,
                    "formula_mode": mode,
                    "source_group": "测试",
                    "signal_frequency": "monthly",
                    "return_horizon": "next_month",
                    "signal_date": exit_date - pd.Timedelta(days=28),
                    "entry_date": exit_date - pd.Timedelta(days=25),
                    "exit_date": exit_date,
                    "return_month": month,
                    "ic": 0.001 * (code_index + month_index),
                    "stock_count": 100,
                }
            )
            monthly_rows.append(
                {
                    "factor": code,
                    "factor_name": code,
                    "formula_mode": mode,
                    "source_group": "测试",
                    "signal_frequency": "monthly",
                    "return_month": month,
                    "monthly_ic": 0.001 * (code_index + month_index),
                    "raw_observations": 1,
                    "min_stock_count": 100,
                    "avg_stock_count": 100.0,
                }
            )
    summary_rows = [
        {
            "factor": code,
            "factor_name": code,
            "formula_mode": mode,
            "source_group": "测试",
            "signal_frequency": "monthly",
            "recent_months": 5,
            "recent_ic": 0.01,
            "recent_ic_std": 0.001,
            "recent_icir": 10.0,
            "raw_observations": 5,
            "min_stock_count": 100,
            "avg_stock_count": 100.0,
            "first_exit_date": "2026-01-28",
            "last_exit_date": "2026-05-28",
            "recent_abs_ic": 0.01,
            "recent_abs_ic_rank": index + 1,
        }
        for index, (code, mode) in enumerate(rows)
    ]
    pd.DataFrame(raw_rows).to_csv(
        directory / target.RECENT_2026_FILES[0], index=False
    )
    pd.DataFrame(monthly_rows).to_csv(
        directory / target.RECENT_2026_FILES[1], index=False
    )
    pd.DataFrame(summary_rows).to_csv(
        directory / target.RECENT_2026_FILES[2], index=False
    )
    pd.DataFrame(
        [{"check_item": "目标因子数", "expected": 51, "actual": 51, "status": "通过"}]
    ).to_csv(directory / target.RECENT_2026_FILES[3], index=False)


def test_assemble_six_groups_and_validate_counts(tmp_path: Path) -> None:
    directories = _make_inputs(tmp_path, extra_group="julaug_strict_dir")
    output_dir = tmp_path / "merged"

    assert target.main(_argv(directories, output_dir)) == 0

    expected_codes = {
        code for group in target.GROUP_SPECS for code in group.codes
    }
    assert len(expected_codes) == 51
    for filename in target.REQUIRED_CSV_FILES:
        frame = pd.read_csv(output_dir / filename)
        assert set(frame["factor"]) == expected_codes
        assert frame["factor"].nunique() == 51
        assert "OLD_FACTOR_NOT_IN_COMPLETION" not in set(frame["factor"])

    status = pd.read_csv(output_dir / "factor_status.csv")
    assert len(status) == 51
    assert status["formula_mode"].value_counts().to_dict() == {
        "trial": 27,
        "strict": 24,
    }
    j12 = status[
        status["factor"] == "J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR"
    ]
    assert len(j12) == 1
    assert j12.iloc[0]["formula_mode"] == "trial"

    unavailable = pd.read_csv(
        output_dir / "currently_unavailable_factors.csv"
    )
    assert len(unavailable) == 27
    assert unavailable["factor"].nunique() == 27
    assert unavailable["reason"].str.strip().ne("").all()
    assert not expected_codes.intersection(unavailable["factor"])

    audit = pd.read_csv(output_dir / "source_group_audit.csv")
    assert len(audit) == 6
    extra_row = audit[audit["source_group"] == "七八月10个严格"].iloc[0]
    assert extra_row["ignored_extra_code_count"] == 1
    assert extra_row["ignored_extra_codes"] == "OLD_FACTOR_NOT_IN_COMPLETION"

    methodology = json.loads(
        (output_dir / "methodology.json").read_text(encoding="utf-8")
    )
    assert methodology["validation"] == {
        "unique_new_factor_codes": 51,
        "strict_count": 24,
        "trial_count": 27,
        "currently_unavailable_count": 27,
        "j12_d04_group_count": 1,
        "j12_d04_factor_status_rows": 1,
        "required_csv_files": list(target.REQUIRED_CSV_FILES),
    }
    assert methodology["recent_2026"] == {"included": False}


def test_optional_recent_2026_files_are_validated_copied_and_recorded(
    tmp_path: Path,
) -> None:
    directories = _make_inputs(tmp_path / "groups")
    recent_dir = tmp_path / "recent_2026"
    _write_recent_2026(recent_dir)
    output_dir = tmp_path / "merged"
    argv = _argv(directories, output_dir)
    argv.extend(["--recent-2026-dir", str(recent_dir)])

    assert target.main(argv) == 0

    for filename in target.RECENT_2026_FILES:
        assert (output_dir / filename).read_bytes() == (
            recent_dir / filename
        ).read_bytes()
    methodology = json.loads(
        (output_dir / "methodology.json").read_text(encoding="utf-8")
    )
    recent = methodology["recent_2026"]
    assert recent["included"] is True
    assert recent["factor_count"] == 51
    assert recent["strict_count"] == 24
    assert recent["trial_count"] == 27
    assert recent["months"] == [f"2026-{month:02d}" for month in range(1, 6)]
    assert set(recent["files"]) == set(target.RECENT_2026_FILES)
    for filename in target.RECENT_2026_FILES:
        assert recent["files"][filename]["copied_path"] == str(
            (output_dir / filename).resolve()
        )


def test_invalid_recent_2026_files_fail_before_output_is_written(
    tmp_path: Path,
) -> None:
    directories = _make_inputs(tmp_path / "groups")
    recent_dir = tmp_path / "recent_2026"
    _write_recent_2026(recent_dir)
    (recent_dir / target.RECENT_2026_FILES[2]).unlink()
    output_dir = tmp_path / "merged"
    argv = _argv(directories, output_dir)
    argv.extend(["--recent-2026-dir", str(recent_dir)])

    with pytest.raises(target.CompletionInputError, match="2026近期结果缺文件"):
        target.main(argv)

    assert not output_dir.exists()


def test_missing_file_is_reported_before_writing(tmp_path: Path) -> None:
    directories = _make_inputs(tmp_path)
    missing = directories["early_strict_dir"] / "factor_status.csv"
    missing.unlink()
    output_dir = tmp_path / "merged"

    with pytest.raises(target.CompletionInputError) as exc_info:
        target.main(_argv(directories, output_dir))

    message = str(exc_info.value)
    assert "缺文件" in message
    assert str(missing) in message
    assert not output_dir.exists()


def test_missing_code_is_reported_before_writing(tmp_path: Path) -> None:
    directories = _make_inputs(tmp_path)
    group = target.GROUP_SPECS[2]
    missing_code = group.codes[0]
    source = directories[group.argument_name] / "original_frequency_ic.csv"
    frame = pd.read_csv(source)
    frame = frame[frame["factor"] != missing_code]
    frame.to_csv(source, index=False, encoding="utf-8-sig")
    output_dir = tmp_path / "merged"

    with pytest.raises(target.CompletionInputError) as exc_info:
        target.main(_argv(directories, output_dir))

    message = str(exc_info.value)
    assert "缺代码" in message
    assert missing_code in message
    assert not output_dir.exists()
