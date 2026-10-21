# -*- coding: utf-8 -*-

import csv
from pathlib import Path

import pandas as pd
import pytest

import historical_existing_duckdb_factors as target


def test_registry_contains_exactly_96_actual_result_codes() -> None:
    assert len(target.CODE_TO_GROUP) == 96
    assert sum(len(group.result_codes) for group in target.GROUPS.values()) == 96
    assert {
        name: len(group.result_codes) for name, group in target.GROUPS.items()
    } == {
        "january": 11,
        "february_march": 12,
        "april": 6,
        "may": 7,
        "june": 8,
        "early_four": 3,
        "early_trial": 5,
        "july_august": 16,
        "september_december": 24,
        "october_december_trial": 4,
    }


def test_aliases_resolve_to_runner_codes_and_groups() -> None:
    assert target.resolve_result_code("ILLIQ_SHORTCUT") == "ILLIQ_SHORTCUT_5M_5D"
    assert target.CODE_TO_GROUP[target.resolve_result_code("RVJN")] == "january"
    assert (
        target.CODE_TO_GROUP[target.resolve_result_code("M06_UPSIDE_VOL_SHARE")]
        == "february_march"
    )
    assert target.resolve_result_code("J06_D19_INTRADAY_CVAR_5PCT") == (
        "J06_D19_INTRADAY_CVAR_5PCT"
    )


def test_mapping_reader_keeps_actual_codes_and_reports_unresolved(tmp_path: Path) -> None:
    path = tmp_path / "mapping.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["record_id", "implementation_ref"]
        )
        writer.writeheader()
        writer.writerows(
            [
                {"record_id": "A", "implementation_ref": "ILLIQ_SHORTCUT"},
                {"record_id": "B", "implementation_ref": "ILLIQ_SHORTCUT"},
                {"record_id": "C", "implementation_ref": "J06_D10_VOLATILITY_PANIC_20D"},
                {"record_id": "D", "implementation_ref": "J12_D26_APM"},
            ]
        )
    mapped = target.read_mapped_implementations(path)
    assert [(item.result_code, item.group_name) for item in mapped] == [
        ("ILLIQ_SHORTCUT_5M_5D", "january"),
        ("J06_D10_VOLATILITY_PANIC_20D", "june"),
    ]
    assert mapped[0].record_ids == ("A", "B")
    assert target.unresolved_mapping_refs(path) == ["J12_D26_APM"]


def test_required_fields_and_existing_duckdb_entry_markers() -> None:
    february = target.required_columns_by_source("february_march")
    assert "rvjn_5m" in february["base_daily"]
    assert "vwap_outside_range_1pct" in february["base_daily"]
    april = target.required_columns_by_source("april")
    assert "tail_volume_share_daily" in april["april_daily"]
    june = target.required_columns_by_source("june")
    assert "atv_5m_47" in june["june_daily"]
    with pytest.raises(ValueError, match="已有DuckDB入口"):
        target.required_columns_by_source("july_august")


def test_generic_duckdb_panel_loader_joins_parquet(tmp_path: Path) -> None:
    base = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "code": ["SH600000", "SH600000"],
            "open": [10.0, 11.0],
        }
    )
    feature = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "code": ["sh600000", "sh600000"],
            "factor_x": [1.5, 2.5],
        }
    )
    base_path = tmp_path / "base.parquet"
    feature_path = tmp_path / "feature.parquet"
    base.to_parquet(base_path)
    feature.to_parquet(feature_path)
    panel = target.load_duckdb_panel(
        {
            "base": ("date", "code", "open"),
            "feature": ("date", "code", "factor_x"),
        },
        "2024-01-01",
        "2024-01-31",
        source_globs={"base": base_path, "feature": feature_path},
    )
    assert panel.columns.tolist() == ["date", "code", "open", "factor_x"]
    assert panel["code"].tolist() == ["sh600000", "sh600000"]
    assert panel["open"].dtype.name == "float32"
    assert panel["factor_x"].tolist() == [1.5, 2.5]


def test_duckdb_panel_loader_rejects_incomplete_coverage(tmp_path: Path) -> None:
    base = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "code": ["sh600000", "sh600000"],
            "open": [10.0, 11.0],
        }
    )
    feature = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02"]),
            "code": ["sh600000"],
            "factor_x": [1.5],
        }
    )
    base_path = tmp_path / "base.parquet"
    feature_path = tmp_path / "feature.parquet"
    base.to_parquet(base_path)
    feature.to_parquet(feature_path)
    with pytest.raises(ValueError, match="覆盖行数不一致"):
        target.load_duckdb_panel(
            {"base": ("date", "code", "open"), "feature": ("date", "code", "factor_x")},
            "2024-01-01",
            "2024-01-31",
            source_globs={"base": base_path, "feature": feature_path},
        )

