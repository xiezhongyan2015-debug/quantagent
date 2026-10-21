# -*- coding: utf-8 -*-

import numpy as np
import pandas as pd

import build_minute_daily_extra as target


def _write_synthetic_minutes(path) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=35)
    rows = []
    for day, date in enumerate(dates):
        ratio = 0.80 + day * 0.001
        for minute in range(240):
            timestamp = date + pd.Timedelta(hours=9, minutes=31 + minute)
            price = 10.0 + day * 0.01 + 0.02 * np.sin(minute / 13.0 + day / 7.0)
            previous_wave = 0.02 * np.sin((minute - 1) / 13.0 + day / 7.0)
            open_price = 10.0 + day * 0.01 + previous_wave
            volume = 1000.0 + 18.0 * np.sin(day * 1.7 + minute * 0.11)
            if day == len(dates) - 1 and minute in {20, 21, 80, 160}:
                volume += 1200.0
            raw_price = price / ratio
            rows.append(
                {
                    "datetime": timestamp,
                    "open": open_price,
                    "high": max(open_price, price) + 0.01,
                    "low": min(open_price, price) - 0.01,
                    "close": price,
                    "volume": volume,
                    "amount": volume * raw_price * (1.0 + 0.0005 * np.cos(minute)),
                    "qfq_ratio": ratio,
                }
            )
    frame = pd.DataFrame(rows)
    frame.to_parquet(path, index=False)
    return frame


def _minute_returns(day: pd.DataFrame) -> pd.Series:
    previous = day["close"].shift(1)
    previous.iloc[0] = day["open"].iloc[0]
    return np.log(day["close"] / previous)


def _simple_minute_returns(day: pd.DataFrame) -> pd.Series:
    previous = day["close"].shift(1)
    previous.iloc[0] = day["open"].iloc[0]
    return day["close"] / previous - 1.0


def _independent_last_day(frame: pd.DataFrame) -> dict[str, float]:
    days = [part.reset_index(drop=True) for _, part in frame.groupby(frame["datetime"].dt.date)]
    current = days[-1]
    history20 = days[-21:-1]
    ret = _minute_returns(current)
    simple_ret = _simple_minute_returns(current)

    volume_history = np.stack([day["volume"].to_numpy(float) for day in history20])
    amount_history = np.stack([day["amount"].to_numpy(float) for day in history20])
    volume = current["volume"].to_numpy(float)
    amount = current["amount"].to_numpy(float)
    ratio = current["qfq_ratio"].to_numpy(float)
    erupt = volume > volume_history.mean(axis=0) + volume_history.std(axis=0, ddof=0)
    previous_erupt = np.r_[False, erupt[:-1]]
    next_erupt = np.r_[erupt[1:], False]
    peak = erupt & ~previous_erupt & ~next_erupt
    ridge = erupt & (previous_erupt | next_erupt)
    valley = ~erupt

    adjusted_amount = (
        amount - amount_history.mean(axis=0)
    ) / amount_history.std(axis=0, ddof=0)
    ret_lag = ret.shift(1)
    adjusted_good = ret_lag.notna() & ret_lag.ne(0) & np.isfinite(adjusted_amount)

    volatility = ret.rolling(5, min_periods=5).std(ddof=1)
    fuzzy = volatility.rolling(5, min_periods=5).std(ddof=1)
    fuzzy.iloc[[0, -1]] = np.nan

    adjusted_amount_for_price = amount * ratio
    all_vwap = adjusted_amount_for_price.sum() / volume.sum()
    valley_vwap = adjusted_amount_for_price[valley].sum() / volume[valley].sum()
    ridge_vwap = adjusted_amount_for_price[ridge].sum() / volume[ridge].sum()
    gaps = np.diff(np.flatnonzero(peak)).astype(float)

    current_erupt = volume > volume.mean() + volume.std(ddof=0)
    current_positions = np.flatnonzero(current_erupt)
    retained_count = 0
    if len(current_positions):
        retained_count = 1 + int(np.sum(np.diff(current_positions) > 1))

    regional = pd.Series(volume).rolling(9, center=True, min_periods=9).sum()
    peak_minute = int(regional.iloc[5:232].idxmax())
    surge_minute = int(regional.iloc[4:peak_minute].idxmin())
    ebb_minute = int(regional.iloc[peak_minute + 1 : 233].idxmin())
    tide_rate = (
        (current.loc[ebb_minute, "close"] - current.loc[surge_minute, "close"])
        / current.loc[surge_minute, "close"]
        / (ebb_minute - surge_minute)
    )

    smart_parts = []
    for day in days[-10:]:
        day = day.copy()
        day["ret"] = _minute_returns(day)
        day["smart_score"] = day["ret"].abs() / day["volume"].pow(0.25)
        smart_parts.append(day)
    smart = pd.concat(smart_parts, ignore_index=True)
    smart = smart.sort_values(["smart_score", "datetime"], ascending=[False, True])
    smart["cumulative_before"] = smart["volume"].cumsum() - smart["volume"]
    selected = smart["cumulative_before"] < 0.20 * smart["volume"].sum()
    smart_adjusted_amount = smart["amount"] * smart["qfq_ratio"]
    smart_vwap = smart_adjusted_amount[selected].sum() / smart.loc[selected, "volume"].sum()
    all_smart_vwap = smart_adjusted_amount.sum() / smart["volume"].sum()

    previous_close = days[-2]["close"].iloc[-1]
    low_bound = min(float(current["low"].min()), float(previous_close))
    high_bound = max(float(current["high"].max()), float(previous_close))

    selected_count = int(np.ceil(len(current) / 3.0))
    high_volume_index = (
        current.assign(simple_ret=simple_ret)
        .sort_values(["volume", "datetime"], ascending=[False, True])
        .head(selected_count)
        .index
    )
    high_volume_returns = simple_ret.loc[high_volume_index]
    high_volume_mean = high_volume_returns.mean()
    centered_sum2 = ((high_volume_returns - high_volume_mean) ** 2).sum()
    centered_sum3 = ((high_volume_returns - high_volume_mean) ** 3).sum()
    high_volume_skew = (
        centered_sum3
        / (centered_sum2 / (selected_count - 2)) ** 1.5
        / (selected_count - 1)
    )

    volume_diff = current["volume"].diff()
    event = volume_diff > volume_diff.mean() + volume_diff.std(ddof=1)
    forward_std = pd.Series(
        [
            simple_ret.iloc[index : index + 5].std(ddof=1)
            if index + 5 <= len(simple_ret)
            else np.nan
            for index in range(len(simple_ret))
        ]
    )
    dazzling_volatility = forward_std[event].mean()

    price_volume = (
        current.groupby("close", sort=False)["volume"]
        .sum()
        .rename("price_volume")
        .reset_index()
        .rename(columns={"close": "price"})
    )
    support_price = float(
        price_volume.sort_values(
            ["price_volume", "price"], ascending=[False, True]
        ).iloc[0]["price"]
    )
    ordered_price_volume = price_volume.assign(
        distance=(price_volume["price"] - support_price).abs()
    ).sort_values(
        ["distance", "price_volume", "price"],
        ascending=[True, False, True],
    )
    cumulative_before = (
        ordered_price_volume["price_volume"].cumsum()
        - ordered_price_volume["price_volume"]
    )
    selected_prices = ordered_price_volume.loc[
        cumulative_before <= 0.5 * ordered_price_volume["price_volume"].sum(),
        "price",
    ]
    p_shaped = float(current["high"].max() - selected_prices.min())

    top_times = (
        current.assign(
            simple_ret=simple_ret,
            minute_of_day=(
                current["datetime"].dt.hour * 60
                + current["datetime"].dt.minute
            ),
        )
        .sort_values(
            ["simple_ret", "minute_of_day"], ascending=[False, True]
        )
        .head(5)["minute_of_day"]
        .astype(int)
        .tolist()
    )
    return {
        "smart_money_vwap_ratio_10d": smart_vwap / all_smart_vwap,
        "volume_peak_return_daily": float(ret[peak].sum()),
        "valley_relative_vwap_daily": valley_vwap / all_vwap,
        "ridge_relative_vwap_daily": ridge_vwap / all_vwap,
        "ridge_minute_count_daily": float(ridge.sum()),
        "current_day_volume_peak_count": float(retained_count),
        "adj_lag_absret_amount_corr_daily": float(
            ret_lag[adjusted_good].abs().corr(pd.Series(adjusted_amount)[adjusted_good])
        ),
        "fuzzy_amount_corr_daily": float(fuzzy.corr(current["amount"])),
        "volume_tide_price_rate_daily": float(tide_rate),
        "peak_gap_count": float(len(gaps)),
        "peak_gap_sum1": float(np.sum(gaps)),
        "peak_gap_sum2": float(np.sum(gaps**2)),
        "peak_gap_sum3": float(np.sum(gaps**3)),
        "peak_gap_sum4": float(np.sum(gaps**4)),
        "valley_price_percentile_daily": (valley_vwap - low_bound) / (high_bound - low_bound),
        "high_volume_realized_skew_daily": float(high_volume_skew),
        "dazzling_volatility_daily": float(dazzling_volatility),
        "p_shaped_volume_distribution_daily": p_shaped,
        "top_return_time_1": top_times[0],
        "top_return_time_2": top_times[1],
        "top_return_time_3": top_times[2],
        "top_return_time_4": top_times[3],
        "top_return_time_5": top_times[4],
    }


def test_sepdec_daily_cache_matches_independent_pandas(tmp_path) -> None:
    path = tmp_path / "sh600000.parquet"
    frame = _write_synthetic_minutes(path)
    expected = _independent_last_day(frame)
    start = frame["datetime"].min().date().isoformat()
    end = frame["datetime"].max().date().isoformat()

    import duckdb

    connection = duckdb.connect()
    try:
        actual = connection.execute(target.build_query(str(path), start, end)).fetchdf().iloc[-1]
    finally:
        connection.close()

    for column, value in expected.items():
        np.testing.assert_allclose(
            float(actual[column]),
            value,
            rtol=1e-7,
            atol=1e-9,
            err_msg=column,
        )


def test_p_shaped_distribution_ignores_zero_volume_prices(tmp_path) -> None:
    date = pd.Timestamp("2026-05-29 09:31:00")
    frame = pd.DataFrame(
        {
            "datetime": [date + pd.Timedelta(minutes=i) for i in range(4)],
            "open": [10.0, 10.0, 9.5, 11.0],
            "high": [10.1, 9.6, 11.1, 12.2],
            "low": [9.9, 9.4, 10.9, 11.9],
            "close": [10.0, 9.5, 11.0, 12.0],
            "volume": [40.0, 0.0, 20.0, 40.0],
            "amount": [400.0, 0.0, 220.0, 480.0],
            "qfq_ratio": [1.0] * 4,
        }
    )
    path = tmp_path / "sh600000.parquet"
    frame.to_parquet(path, index=False)

    import duckdb

    connection = duckdb.connect()
    try:
        actual = connection.execute(
            target.build_query(str(path), "2026-05-29", "2026-05-29")
        ).fetchdf().iloc[0]
    finally:
        connection.close()

    np.testing.assert_allclose(
        float(actual["p_shaped_volume_distribution_daily"]),
        12.2 - 10.0,
    )
