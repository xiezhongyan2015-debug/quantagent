# -*- coding: utf-8 -*-

import numpy as np
import pandas as pd

from build_level2_daily_features import (
    BAD_DATES,
    _quote_group_features,
    _safe_div,
    aggregate_trade_minutes,
    combine_daily_features,
)


def make_minute_rows() -> pd.DataFrame:
    rows = []
    for code, scale in (("sh600000", 1.0), ("sz000001", 2.0)):
        rows.append(
            {
                "code": code,
                "minute_ts": "2026-01-05 09:25:00",
                "minute_close": 10 * scale,
                "volume": 100,
                "amount": 1000 * scale,
                "trade_count": 10,
                "buy_volume": 60,
                "sell_volume": 40,
                "buy_amount": 600 * scale,
                "sell_amount": 400 * scale,
                "buy_count": 6,
                "sell_count": 4,
                "tick_price_volume_corr": 0.1,
            }
        )
        for minute in range(30, 50):
            rows.append(
                {
                    "code": code,
                    "minute_ts": f"2026-01-05 09:{minute:02d}:00",
                    "minute_close": (10 + (minute - 30) * 0.01) * scale,
                    "volume": 100 + minute,
                    "amount": (1000 + minute * 10) * scale,
                    "trade_count": 10 + minute % 3,
                    "buy_volume": 60 + minute,
                    "sell_volume": 40,
                    "buy_amount": (600 + minute * 6) * scale,
                    "sell_amount": (400 + minute * 4) * scale,
                    "buy_count": 6 + minute % 3,
                    "sell_count": 4,
                    "tick_price_volume_corr": (minute - 40) / 20,
                }
            )
    return pd.DataFrame(rows)


def test_safe_div_handles_zero() -> None:
    assert np.isnan(_safe_div(1.0, 0.0))
    result = _safe_div(pd.Series([2.0, 3.0]), pd.Series([2.0, 0.0]))
    assert result.iloc[0] == 1.0
    assert np.isnan(result.iloc[1])


def test_trade_aggregation_keeps_auction_separate() -> None:
    daily, minute = aggregate_trade_minutes(make_minute_rows())
    assert len(daily) == 2
    assert daily["open_auction_volume"].eq(100).all()
    assert minute["minute_ts"].dt.minute.min() == 30
    assert daily["trade_amount"].notna().all()


def test_quote_features_include_order_trade_correlation() -> None:
    rows = []
    for minute in range(6):
        row = {
            "minute_ts": pd.Timestamp("2026-01-05 09:30:00") + pd.Timedelta(minutes=minute),
            "minute_amount": 1_000_000.0 + minute * 10_000.0,
            "minute_return": minute * 0.001,
        }
        for level in range(1, 6):
            row[f"ask_price_{level}_x1e4"] = 100_000 + level * 10
            row[f"bid_price_{level}_x1e4"] = 100_000 - level * 10
            row[f"ask_volume_{level}"] = 1_000 - minute * 10
            row[f"bid_volume_{level}"] = 1_000 + minute * 10
        rows.append(row)

    result = _quote_group_features(pd.DataFrame(rows))
    assert "order_trade_corr" in result.index
    assert np.isfinite(result["order_trade_corr"])


def test_combine_builds_shared_ratios() -> None:
    prices = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-01-05")],
            "code": ["sh600000"],
            "open": [10.0],
            "close": [10.1],
            "float_shares": [1_000_000.0],
            "daily_return": [0.01],
            "market_return": [0.005],
        }
    )
    trades = pd.DataFrame(
        {
            "code": ["sh600000"],
            "trade_amount": [1000.0],
            "trade_volume": [100.0],
            "buy_amount": [600.0],
            "sell_amount": [400.0],
            "buy_volume": [60.0],
            "sell_volume": [40.0],
            "buy_count": [6.0],
            "sell_count": [4.0],
            "trade_count": [10.0],
            "open_auction_volume": [10.0],
            "close_5m_volume": [20.0],
        }
    )
    orders = pd.DataFrame(
        {
            "code": ["sh600000"],
            "small_buy_amount_40k": [100.0],
            "small_sell_amount_40k": [50.0],
            "large_buy_amount": [300.0],
            "large_sell_amount": [200.0],
            "super_buy_amount": [20.0],
            "super_sell_amount": [10.0],
            "active_large_buy_amount": [250.0],
            "active_large_sell_amount": [150.0],
            "open_large_active_buy_amount": [50.0],
            "open_large_active_sell_amount": [30.0],
        }
    )
    quotes = pd.DataFrame({"code": ["sh600000"], "mofi": [2.0], "five_level_soir": [0.2], "net_bid_change": [10.0], "open_net_bid_change": [5.0]})
    events = pd.DataFrame(
        {
            "code": ["sh600000"],
            "buy_order_arrivals": [100],
            "sell_order_arrivals": [80],
            "buy_order_cancels": [10],
            "sell_order_cancels": [5],
        }
    )
    result = combine_daily_features("2026-01-05", prices, trades, orders, quotes, events)
    assert np.isclose(result.loc[0, "open_auction_share"], 0.1)
    assert np.isclose(result.loc[0, "retail_bsi"], 1 / 3)
    assert np.isfinite(result.loc[0, "pin"])


def test_bad_dates_are_fixed() -> None:
    assert set(BAD_DATES) == {
        "2026-03-12",
        "2026-04-14",
        "2026-05-12",
        "2026-07-06",
        "2026-07-09",
        "2026-07-10",
        "2026-07-13",
        "2026-07-15",
    }
