# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd

import historical_backtest_core as core


def test_normalize_frequency() -> None:
    assert core.normalize_frequency("每日") == "daily"
    assert core.normalize_frequency("月末") == "monthly"
    assert core.normalize_frequency("每5个交易日") == "every_5_days"
    assert core.normalize_frequency("周频（周二）") == "weekly_tuesday"


def test_panel_arrays_uses_unadjusted_price_for_market_cap() -> None:
    dates = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
    panel = pd.DataFrame(
        {
            "date": dates,
            "code": ["sh600000", "sh600000"],
            "open": [10.0, 11.0],
            "high": [10.0, 11.0],
            "low": [10.0, 11.0],
            "close": [10.0, 11.0],
            "volume": [100.0, 100.0],
            "amount": [1000.0, 1100.0],
            "float_shares": [10.0, 10.0],
            "total_shares": [20.0, 20.0],
            "qfq_ratio": [0.5, 0.5],
        }
    )
    stocks = ["sh600000"]
    date_map = {date: index for index, date in enumerate(dates)}
    stock_map = {"sh600000": 0}
    arrays = core.panel_arrays(panel, dates, stocks, date_map, stock_map)
    np.testing.assert_allclose(arrays["raw_close"][:, 0], [20.0, 22.0])
    np.testing.assert_allclose(arrays["market_cap"][:, 0], [400.0, 440.0])


def test_backtest_factor_returns_report_years(monkeypatch) -> None:
    dates = pd.bdate_range("2019-01-01", "2024-12-31")
    stocks = 60
    shape = (len(dates), stocks)
    base = np.linspace(10.0, 20.0, len(dates), dtype=np.float32)[:, None]
    cross = np.linspace(0.0, 1.0, stocks, dtype=np.float32)[None, :]
    opening = base + cross
    arrays = {
        "open": opening,
        "close": opening,
        "open_exit_mark": opening,
        "close_exit_mark": opening,
    }
    factor = np.broadcast_to(cross, shape).copy()
    eligible = np.ones(shape, dtype=bool)
    settings = core.BacktestSettings(
        qlib_dir=Path("."),
        start=str(dates[0].date()),
        backtest_start=str(dates[0].date()),
        end=str(dates[-1].date()),
    )
    frame = core.backtest_factor(
        "T__daily", factor, "daily", arrays, eligible, dates, settings
    )
    assert not frame.empty
    assert frame["entry_date"].dt.year.min() == 2022
    assert frame["entry_date"].dt.year.max() == 2024
    summary, yearly = core.summarize_unit(frame)
    assert len(summary) == 1
    assert set(yearly["year"]) == {2022, 2023, 2024}
