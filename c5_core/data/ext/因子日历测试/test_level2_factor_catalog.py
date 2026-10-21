# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import level2_factor_catalog as target


@pytest.fixture(scope="module")
def source_rows() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    historical = target.read_historical_level2()
    current = target.select_2026_level2_targets(target.read_2026_factor_library())
    return historical, current


def test_source_counts_are_exact(
    source_rows: tuple[list[dict[str, str]], list[dict[str, str]]],
) -> None:
    historical, current = source_rows
    assert len(historical) == 109
    assert len({row["unit_id"] for row in historical}) == 77
    assert len(current) == 69
    assert len({row["因子代码"] for row in current}) == 69

    regular = [row for row in current if row["回测状态"] == "待补数据"]
    assert len(regular) == 68
    assert {row["因子代码"] for row in current} - {
        row["因子代码"] for row in regular
    } == {"J12_D31_PRICE_IMPACT_BIAS"}


def test_selection_keeps_real_level2_and_excludes_other_missing_data(
    source_rows: tuple[list[dict[str, str]], list[dict[str, str]]],
) -> None:
    _, current = source_rows
    codes = {row["因子代码"] for row in current}

    assert "J12_D03_PROFIT_ADJ_ACTIVE_FLEX_CHIP" in codes
    assert "J12_D29_OPEN_ADJ_ACTIVE_FLEX_CHIP" in codes
    assert "F02_D20_CHIP_CONCENTRATION" not in codes
    assert "J07_D31_CHIP_DISPOSITION" not in codes
    assert "J07_D12_TREND_FUNDS_PRICE" not in codes
    assert "J08_D29_TURNOVER_ATTENTION" not in codes
    assert all(target.requires_level2_data(row) for row in current)


def test_combined_catalog_maps_price_impact_to_hf0072() -> None:
    rows = target.build_combined_catalog()
    assert len(rows) == 178
    assert set(target.CATALOG_COLUMNS) == set(rows[0])

    price_impact = next(
        row
        for row in rows
        if row["source_scope"] == "current_2026"
        and row["factor_code"] == "J12_D31_PRICE_IMPACT_BIAS"
    )
    assert price_impact["historical_implementation_ref"] == "J12_D31_PRICE_IMPACT_BIAS"
    assert price_impact["historical_formula_keys"] == "HF0072"
    assert price_impact["historical_unit_ids"] == "HF0072__monthly"
    assert set(price_impact["historical_record_ids"].split(";")) == {
        "HC0084",
        "HC0219",
        "HC0291",
    }
    assert "公式印刷错误，暂不能严格计算" in price_impact["catalog_note"]


def test_cli_writes_requested_csv(tmp_path: Path) -> None:
    output = tmp_path / "combined_level2_factor_catalog.csv"
    assert target.main(["--output", str(output)]) == 0
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 178
    assert tuple(rows[0]) == target.CATALOG_COLUMNS
    assert sum(row["source_scope"] == "historical" for row in rows) == 109
    assert sum(row["source_scope"] == "current_2026" for row in rows) == 69
