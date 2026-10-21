# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import historical_level2_factors as level2


def _catalog_row(
    factor_id: str,
    name: str,
    formula_key: str = "",
    scope: str = "current_2026",
    reference: str = "",
    frequency: str = "日度",
) -> dict[str, str]:
    return {
        "source_scope": scope,
        "name": name,
        "code_or_unit": factor_id,
        "factor_code": factor_id if scope == "current_2026" else reference,
        "formula_key": formula_key,
        "unit_id": factor_id if scope == "historical" else "",
        "formula": name,
        "signal_frequency": frequency,
        "direction": "正向",
        "historical_implementation_ref": reference,
    }


def _days(count: int = 25, codes: tuple[str, ...] = ("A",)) -> pd.DataFrame:
    rows = []
    for code_index, code in enumerate(codes):
        for day_index, date in enumerate(pd.date_range("2024-01-02", periods=count, freq="B")):
            rows.append(
                {
                    "date": date,
                    "code": code,
                    "daily_return": (day_index + 1) / 1000.0 + code_index / 100.0,
                }
            )
    return pd.DataFrame(rows)


def _full_sample(days: int = 25, codes: tuple[str, ...] = ("A", "B", "C")) -> pd.DataFrame:
    frame = _days(days, codes)
    day_number = frame.groupby("code", sort=False).cumcount().astype(float)
    code_number = frame["code"].map({code: index for index, code in enumerate(codes)}).astype(float)
    additions: dict[str, pd.Series] = {}
    for column_index, column in enumerate(level2.DAILY_BASE_COLUMNS):
        if column in {"date", "code", "daily_return"}:
            continue
        additions[column] = (
            10.0
            + column_index
            + day_number * (0.07 + (column_index % 5) * 0.01)
            + code_number * (0.11 + (column_index % 3) * 0.02)
        )
    frame = pd.concat([frame, pd.DataFrame(additions, index=frame.index)], axis=1)
    # 避免买卖金额完全共线，便于检查双变量回归分支。
    frame["buy_amount"] = 100.0 + day_number * day_number + code_number * 7.0
    frame["sell_amount"] = 80.0 + day_number * 2.0 + (day_number % 3.0) * 5.0 + code_number * 3.0
    frame["trade_amount"] = frame["buy_amount"] + frame["sell_amount"] + 100.0
    frame["trade_volume"] = 1000.0 + day_number * 10.0 + code_number
    frame["open_auction_volume"] = frame["trade_volume"] * 0.1
    frame["close_5m_volume"] = frame["trade_volume"] * 0.2
    return frame


def test_full_catalog_has_77_historical_and_69_current_targets() -> None:
    catalog = level2.load_factor_catalog()
    targets = level2.prepare_catalog_targets(catalog)
    counts = targets.groupby("source_scope").size().to_dict()
    assert counts == {"current_2026": 69, "historical": 77}
    assert targets["factor_id"].is_unique


def test_ocvp_and_obcvp_use_20_day_means_and_shared_components() -> None:
    base = _days(21)
    base["trade_volume"] = np.arange(1000.0, 1021.0)
    base["open_auction_volume"] = base["trade_volume"] * 0.10
    base["close_5m_volume"] = base["trade_volume"] * 0.20
    catalog = pd.DataFrame(
        [
            _catalog_row("HF0002__daily", "开盘集合竞价成交量占比 OCVP", "HF0002", "historical"),
            _catalog_row("HF0003__daily", "收盘集合竞价成交量占比 BCVP", "HF0003", "historical"),
            _catalog_row("HF0004__daily", "成交量占比复合因子 OBCVP", "HF0004", "historical"),
            _catalog_row("J08_D20_OPEN_CLOSE_VOLUME_SHARE", "成交量占比"),
        ]
    )
    values, status = level2.compute_level2_factors(base, catalog)
    last = values.groupby("factor_id", sort=False).tail(1).set_index("factor_id")["value"]
    assert np.isclose(last["HF0002__daily"], 0.10)
    assert np.isclose(last["HF0003__daily"], 0.20)
    assert np.isclose(last["HF0004__daily"], 0.15)
    assert np.isclose(last["J08_D20_OPEN_CLOSE_VOLUME_SHARE"], 0.15)
    assert set(status["implementation_quality"]) == {"strict", "trial"}


def test_missing_column_only_marks_affected_factor_unavailable() -> None:
    base = _days(5)
    base["midprice_change"] = np.arange(5.0)
    catalog = pd.DataFrame(
        [
            _catalog_row("J10_D17_MIDPRICE_CHANGE", "中间价变化率"),
            _catalog_row("J09_D16_MARKET_PRICE_BIAS", "市场价格偏离"),
        ]
    )
    values, status = level2.compute_level2_factors(base, catalog)
    assert set(values["factor_id"]) == {"J10_D17_MIDPRICE_CHANGE"}
    states = status.set_index("factor_id")
    assert states.loc["J10_D17_MIDPRICE_CHANGE", "status"] == "available"
    assert states.loc["J09_D16_MARKET_PRICE_BIAS", "status"] == "unavailable"
    assert states.loc["J09_D16_MARKET_PRICE_BIAS", "missing_columns"] == "market_price_bias"


def test_close_spread_is_scaled_by_daily_trade_amount() -> None:
    base = _days(3)
    base["close_spread"] = [0.02, 0.03, 0.04]
    base["trade_amount"] = [100.0, 100.0, 200.0]
    catalog = pd.DataFrame([_catalog_row("J07_D02_CLOSE_SPREAD", "收盘委托价差")])
    values, status = level2.compute_level2_factors(base, catalog)
    np.testing.assert_allclose(values["value"], [0.0002, 0.0003, 0.0002])
    assert status.loc[0, "implementation_quality"] == "strict"


def test_twenty_day_lag_rank_correlation_is_shared_by_duplicate_formula() -> None:
    base = _days(25)
    base["small_flow"] = np.arange(25.0)
    catalog = pd.DataFrame(
        [
            _catalog_row("F02_D03_SMALL_ORDER_LAG_RANKCORR", "小单净流入错位秩相关"),
            _catalog_row("HF0110__daily", "小单和小单的错位相关性", "HF0110", "historical"),
        ]
    )
    values, _ = level2.compute_level2_factors(base, catalog)
    pivot = values.pivot(index=["date", "code"], columns="factor_id", values="value")
    finite = pivot.dropna()
    assert not finite.empty
    np.testing.assert_allclose(
        finite["F02_D03_SMALL_ORDER_LAG_RANKCORR"],
        finite["HF0110__daily"],
    )
    assert np.isclose(finite.iloc[-1, 0], 1.0)


def test_ideal_reversal_uses_high_and_low_ten_day_return_sums() -> None:
    base = _days(20)
    base["avg_trade_amount"] = np.arange(1.0, 21.0)
    base["daily_return"] = np.arange(1.0, 21.0)
    catalog = pd.DataFrame([_catalog_row("J11_D26_IDEAL_REVERSAL", "理想反转")])
    values, status = level2.compute_level2_factors(base, catalog)
    assert np.isclose(values["value"].dropna().iloc[-1], 100.0)
    assert status.loc[0, "implementation_quality"] == "trial"
    assert "高低各10日" in status.loc[0, "method_note"]


def test_residual_noniso_removes_size_and_twenty_day_return() -> None:
    codes = ("A", "B", "C", "D", "E")
    base = _days(25, codes)
    day = base.groupby("code", sort=False).cumcount().astype(float)
    stock = base["code"].map({code: index + 1 for index, code in enumerate(codes)}).astype(float)
    base["close"] = 10.0 + stock + day * 0.02
    base["float_shares"] = 1_000_000.0 * (1.0 + stock * 0.2)
    base["daily_return"] = 0.001 * day + 0.002 * stock
    base["residual_noniso_imbalance"] = (
        0.03 * stock * stock + 0.02 * np.sin(day / 3.0 + stock)
    )
    catalog = pd.DataFrame(
        [_catalog_row("J07_D20_RESIDUAL_NONISO_IMB", "反转残差非孤立成交不平衡因子", frequency="月度")]
    )
    values, status = level2.compute_level2_factors(base, catalog)
    last_date = values["date"].max()
    last = values.loc[values["date"].eq(last_date)].sort_values("code")
    residual = last["value"].to_numpy(dtype=float)
    assert np.isfinite(residual).all()
    source = base.sort_values(["code", "date"])
    trailing = source.groupby("code", sort=False).tail(20)
    x_return = trailing.groupby("code")["daily_return"].sum()
    final = source.groupby("code", sort=True).tail(1).sort_values("code")
    x_size = np.log((final["close"] * final["float_shares"]).to_numpy(dtype=float))
    x_return = x_return.sort_index().to_numpy(dtype=float)
    assert abs(residual.sum()) < 1e-10
    assert abs(np.dot(residual, x_size)) < 1e-8
    assert abs(np.dot(residual, x_return)) < 1e-10
    assert status.loc[0, "implementation_quality"] == "strict"


def test_formula_typo_is_unavailable_even_when_daily_guess_exists() -> None:
    base = _days(5)
    base["price_impact_bias"] = 0.3
    catalog = pd.DataFrame(
        [
            _catalog_row(
                "J12_D31_PRICE_IMPACT_BIAS",
                "价格冲击偏差",
                reference="J12_D31_PRICE_IMPACT_BIAS",
            )
        ]
    )
    values, status = level2.compute_level2_factors(base, catalog)
    assert values.empty
    assert status.loc[0, "status"] == "unavailable"
    assert status.loc[0, "implementation_quality"] == "unavailable"
    assert "分母与分子相同" in status.loc[0, "method_note"]


def test_all_catalog_targets_are_recognized_with_complete_synthetic_base() -> None:
    values, status = level2.compute_level2_factors(_full_sample(), level2.DEFAULT_CATALOG)
    unavailable = set(status.loc[status["status"].eq("unavailable"), "factor_id"])
    assert len(status) == 146
    assert unavailable == {"HF0072__monthly", "J12_D31_PRICE_IMPACT_BIAS"}
    assert values["factor_id"].nunique() == 144
    assert not values.duplicated(["factor_id", "date", "code"]).any()
    assert list(values.columns) == level2.VALUE_COLUMNS


def test_writer_creates_required_parquet_and_status_files(tmp_path: Path) -> None:
    base = _days(5)
    base["midprice_change"] = np.arange(5.0)
    catalog = pd.DataFrame([_catalog_row("J10_D17_MIDPRICE_CHANGE", "中间价变化率")])
    value_path, status_path, status = level2.write_level2_factor_outputs(base, tmp_path, catalog)
    assert value_path.name == "factor_values.parquet"
    assert status_path.name == "factor_status.csv"
    assert value_path.exists() and status_path.exists()
    written = pd.read_parquet(value_path)
    assert list(written.columns) == level2.VALUE_COLUMNS
    assert len(written) == len(base)
    assert status.loc[0, "status"] == "available"


def test_daily_base_directory_reads_only_parquet_parts(tmp_path: Path) -> None:
    first = _days(2)
    second = _days(2)
    second["date"] += pd.Timedelta(days=10)
    first.to_parquet(tmp_path / "2024-01-02.parquet", index=False)
    second.to_parquet(tmp_path / "2024-01-12.parquet", index=False)
    pd.DataFrame({"note": ["不应读入"]}).to_csv(tmp_path / "build_progress.csv", index=False)
    actual = level2.read_daily_base(tmp_path)
    assert len(actual) == 4
    assert set(actual.columns) == set(first.columns)
