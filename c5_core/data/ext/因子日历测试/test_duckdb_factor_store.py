# -*- coding: utf-8 -*-

from pathlib import Path

import pandas as pd
import pytest

import duckdb_factor_store as store


def _write_source(path: Path, values: list[tuple[str, str, float]], column: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(values, columns=["date", "code", column])
    frame["date"] = pd.to_datetime(frame["date"])
    frame.to_parquet(path / "part.parquet", index=False)


def test_register_and_read_joined_panel(tmp_path: Path) -> None:
    base = tmp_path / "base"
    feature = tmp_path / "feature"
    _write_source(
        base,
        [("2020-01-02", "SH600000", 10.0), ("2020-01-03", "SH600000", 11.0)],
        "close",
    )
    _write_source(
        feature,
        [("2020-01-02", "sh600000", 1.5), ("2020-01-03", "sh600000", 2.5)],
        "factor_a",
    )
    settings = store.DuckDBSettings(
        database_path=tmp_path / "test.duckdb",
        temp_dir=tmp_path / "tmp",
        threads=1,
        memory_limit="1GB",
    )
    with store.connect(settings) as connection:
        result = store.register_sources(
            connection,
            {"base": base / "*.parquet", "feature": feature / "*.parquet"},
        )
        assert result.set_index("source_name").loc["base", "file_count"] == 1
        panel = store.read_joined_panel(
            connection,
            {"base": ["close"], "feature": ["factor_a"]},
            "2020-01-02",
            "2020-01-03",
        )
    assert panel["code"].tolist() == ["sh600000", "sh600000"]
    assert panel["close"].tolist() == [10.0, 11.0]
    assert panel["factor_a"].tolist() == [1.5, 2.5]


def test_rejects_duplicate_output_columns(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    values = [("2020-01-02", "sh600000", 1.0)]
    _write_source(first, values, "same")
    _write_source(second, values, "same")
    settings = store.DuckDBSettings(
        database_path=tmp_path / "test.duckdb",
        temp_dir=tmp_path / "tmp",
        threads=1,
        memory_limit="1GB",
    )
    with store.connect(settings) as connection:
        store.register_sources(
            connection,
            {"first": first / "*.parquet", "second": second / "*.parquet"},
        )
        with pytest.raises(ValueError, match="同名字段"):
            store.read_joined_panel(
                connection,
                {"first": ["same"], "second": ["same"]},
                "2020-01-02",
                "2020-01-03",
            )
