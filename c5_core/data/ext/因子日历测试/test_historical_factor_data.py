# -*- coding: utf-8 -*-

import historical_factor_data as data


def test_locate_columns_prefers_first_source() -> None:
    available = {
        "base_daily": ("date", "code", "shared", "base_only"),
        "minute_extra_daily": ("date", "code", "shared", "extra_only"),
    }
    grouped, missing = data.locate_columns(
        ["close", "shared", "extra_only", "unknown"], available
    )
    assert grouped == {
        "base_daily": ["shared"],
        "minute_extra_daily": ["extra_only"],
    }
    assert missing == ["unknown"]
