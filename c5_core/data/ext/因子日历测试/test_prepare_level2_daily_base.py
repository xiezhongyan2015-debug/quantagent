# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd

from prepare_level2_daily_base import build_one_stock, normalize_code


def test_normalize_code_accepts_two_orders() -> None:
    assert normalize_code("SH600000") == "sh600000"
    assert normalize_code("600000.SH") == "sh600000"


def test_build_one_stock_applies_latest_normalized_factor(tmp_path: Path) -> None:
    source = tmp_path / "sh600000.csv"
    source.write_text(
        "说明行\n"
        "股票代码,股票名称,交易日期,开盘价,最高价,最低价,收盘价,前收盘价,成交量,成交额,流通市值,总市值\n"
        "sh600000,测试,2026-07-15,10,11,9,10,9.8,100,1000,10000,20000\n"
        "sh600000,测试,2026-07-16,8,9,7,8,10,120,960,8000,16000\n",
        encoding="gbk",
    )
    factors = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-07-15", "2026-07-16"]),
            "adj_factor": [1.6, 2.0],
        }
    )
    target = tmp_path / "out.parquet"
    report = build_one_stock(source, target, "2026-07-15", "2026-07-16", factors)
    result = pd.read_parquet(target)
    assert report["status"] == "ok"
    assert np.isclose(result.loc[0, "qfq_ratio"], 0.8)
    assert np.isclose(result.loc[0, "close"], 8.0)
    assert np.isclose(result.loc[1, "close"], 8.0)
    assert np.isclose(result.loc[0, "float_shares"], 1000.0)
