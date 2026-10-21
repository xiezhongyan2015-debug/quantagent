# -*- coding: utf-8 -*-

import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from scipy.special import ndtr

import build_minute_daily_extra as target
import build_minute_daily_extra_by_stock as stock_target
import duckdb_factor_store as factor_store


SAMPLE_PATH = factor_store.DEFAULT_MINUTE_DIR / "sh600000.parquet"
START = "2025-01-02"
END = "2025-01-06"
CHECK_COLUMNS = [
    "absret_lag_amount_corr",
    "lag_absret_amount_corr",
    "normal_active_share",
    "uniform_active_share",
    "amount_cv_1m",
    "time_avg_relative_price",
    "intraday_trend_ratio_5m",
    "realized_bipower_var_5m",
    "realized_tripower_var_5m",
    "intraday_max_drawdown_5m",
    "realized_jump_volatility_5m",
    "up_jump_volatility_5m",
    "volume_share_skew_10m",
    "volume_share_std_60m",
]


def _require_sample() -> Path:
    if not SAMPLE_PATH.is_file():
        pytest.skip(f"本机没有分钟样本：{SAMPLE_PATH}")
    return SAMPLE_PATH


def _read_sample() -> pd.DataFrame:
    path = _require_sample()
    end_exclusive = pd.Timestamp(END) + pd.Timedelta(days=1)
    frame = pd.read_parquet(
        path,
        columns=[
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ],
        filters=[
            ("datetime", ">=", pd.Timestamp(START)),
            ("datetime", "<", end_exclusive),
        ],
    )
    return frame.sort_values("datetime").reset_index(drop=True)


def _complete_volume_bars(day: pd.DataFrame, size: int) -> pd.Series:
    grouped = (
        day.assign(slot=np.arange(len(day), dtype=np.int64) // size)
        .groupby("slot", sort=True)["volume"]
        .agg(["size", "sum"])
    )
    return grouped.loc[grouped["size"].eq(size), "sum"].astype(float)


def _independent_day(day: pd.DataFrame) -> dict[str, float | int]:
    day = day.sort_values("datetime").reset_index(drop=True)
    close = day["close"].astype(float)
    amount = day["amount"].astype(float)
    previous_close = close.shift(1)
    previous_close.iloc[0] = float(day.loc[0, "open"])
    ret1m = np.log(close / previous_close)

    bars5 = (
        day.assign(slot=np.arange(len(day), dtype=np.int64) // 5)
        .groupby("slot", sort=True)
        .agg(
            size=("close", "size"),
            open5=("open", "first"),
            close5=("close", "last"),
        )
    )
    bars5 = bars5.loc[bars5["size"].eq(5)]
    close5 = bars5["close5"].astype(float)
    previous_close5 = close5.shift(1)
    previous_close5.iloc[0] = float(bars5["open5"].iloc[0])
    close_change5 = close5 - previous_close5
    ret5 = np.log(close5 / previous_close5)
    ret5_lag1 = ret5.shift(1)
    ret5_lag2 = ret5.shift(2)
    mu_two_thirds = (
        2.0 ** (1.0 / 3.0)
        * math.gamma(5.0 / 6.0)
        / np.sqrt(np.pi)
    )
    realized_bipower = (
        np.pi / 2.0 * (ret5.abs() * ret5_lag1.abs()).sum()
    )
    realized_tripower = mu_two_thirds ** -3.0 * (
        ret5.abs() ** (2.0 / 3.0)
        * ret5_lag1.abs() ** (2.0 / 3.0)
        * ret5_lag2.abs() ** (2.0 / 3.0)
    ).sum()
    realized_variance = (ret5 * ret5).sum()
    upside_realized_variance = (ret5[ret5 > 0] ** 2).sum()

    volume10 = _complete_volume_bars(day, 10)
    volume_share10 = volume10 / volume10.sum()
    volume60 = _complete_volume_bars(day, 60)
    volume_share60 = volume60 / volume60.sum()

    typical_price = day[["open", "high", "low", "close"]].mean(axis=1)
    price_range = float(day["high"].max() - day["low"].min())
    return {
        "minute_count": len(day),
        "absret_lag_amount_corr": ret1m.abs().corr(amount.shift(1)),
        "lag_absret_amount_corr": ret1m.shift(1).abs().corr(amount),
        "normal_active_share": (
            amount * ndtr(ret1m / 0.1 * 1.96)
        ).sum()
        / amount.sum(),
        "uniform_active_share": (
            amount * np.clip((ret1m + 0.1) / 0.2, 0.0, 1.0)
        ).sum()
        / amount.sum(),
        "amount_cv_1m": amount.std(ddof=1) / amount.mean(),
        "time_avg_relative_price": (
            typical_price.mean() - day["low"].min()
        )
        / price_range,
        "intraday_trend_ratio_5m": (
            close5.iloc[-1] - bars5["open5"].iloc[0]
        )
        / close_change5.abs().sum(),
        "realized_bipower_var_5m": realized_bipower,
        "realized_tripower_var_5m": realized_tripower,
        "intraday_max_drawdown_5m": (
            close5 / close5.cummax() - 1.0
        ).min(),
        "realized_jump_volatility_5m": max(
            realized_variance - realized_tripower, 0.0
        ),
        "up_jump_volatility_5m": max(
            upside_realized_variance - 0.5 * realized_tripower, 0.0
        ),
        "volume_share_skew_10m": volume_share10.skew(),
        "volume_share_std_60m": volume_share60.std(ddof=1),
    }


def _independent_result() -> pd.DataFrame:
    frame = _read_sample()
    rows = []
    for date, day in frame.groupby(frame["datetime"].dt.normalize(), sort=True):
        row = _independent_day(day)
        row["date"] = date
        rows.append(row)
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def _query_result(start: str = START, end: str = END) -> pd.DataFrame:
    path = _require_sample()
    connection = duckdb.connect()
    try:
        result = connection.execute(
            target.build_query(str(path), start, end)
        ).fetchdf()
    finally:
        connection.close()
    result["date"] = pd.to_datetime(result["date"])
    return result.sort_values("date").reset_index(drop=True)


def test_new_scripts_compile():
    for path in (
        Path(target.__file__),
        Path(stock_target.__file__),
    ):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_duckdb_matches_independent_pandas_numpy_on_three_days():
    actual = _query_result()
    expected = _independent_result()

    assert actual["code"].tolist() == ["sh600000"] * 3
    pd.testing.assert_series_equal(
        actual["date"], expected["date"], check_names=False, check_dtype=False
    )
    np.testing.assert_array_equal(
        actual["minute_count"].to_numpy(),
        expected["minute_count"].to_numpy(),
    )
    assert actual["minute_count"].eq(240).all()
    np.testing.assert_allclose(
        actual[CHECK_COLUMNS].to_numpy(dtype=float),
        expected[CHECK_COLUMNS].to_numpy(dtype=float),
        rtol=1e-6,
        atol=1e-8,
        equal_nan=True,
    )


def test_by_stock_worker_writes_the_same_values(tmp_path: Path):
    source = _require_sample()
    output = tmp_path / "sh600000.parquet"
    temp_dir = tmp_path / "duckdb_temp"

    if stock_target._WORKER_CONNECTION is not None:
        stock_target._WORKER_CONNECTION.close()
        stock_target._WORKER_CONNECTION = None
    stock_target._init_worker(str(temp_dir), "1GB")
    try:
        summary = stock_target._build_one(
            str(source), str(output), START, START
        )
    finally:
        assert stock_target._WORKER_CONNECTION is not None
        stock_target._WORKER_CONNECTION.close()
        stock_target._WORKER_CONNECTION = None

    saved = pd.read_parquet(output)
    saved["date"] = pd.to_datetime(saved["date"])
    expected = _independent_result().iloc[[0]].reset_index(drop=True)

    assert summary["code"] == "sh600000"
    assert summary["status"] == "completed"
    assert summary["rows"] == 1
    assert output.is_file()
    assert not output.with_name("sh600000.tmp.parquet").exists()
    pd.testing.assert_series_equal(
        saved["date"], expected["date"], check_names=False, check_dtype=False
    )
    np.testing.assert_allclose(
        saved[CHECK_COLUMNS].to_numpy(dtype=float),
        expected[CHECK_COLUMNS].to_numpy(dtype=float),
        rtol=1e-6,
        atol=1e-8,
        equal_nan=True,
    )
