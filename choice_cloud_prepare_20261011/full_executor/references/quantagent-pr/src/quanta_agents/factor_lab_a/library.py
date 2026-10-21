"""Reference factor sets: classic A-share anomalies, an Alpha158-lite feature set (anchor), legacy 46,
and the minute-derived base set (each intraday daily field with standard time aggregations)."""
from __future__ import annotations
import json
import os

CLASSIC = {
    # name: (expression, mechanism_id, note)
    "rev20": ("-(close / lag(close, 20) - 1)", "short_reversal", "20-day reversal"),
    "rev5": ("-(close / lag(close, 5) - 1)", "short_reversal", "5-day reversal"),
    "mom_120_20": ("lag(close, 20) / lag(close, 120) - 1", "medium_momentum", "6m momentum skipping 1m"),
    "vol20_low": ("-rolling_std(ret, 20)", "low_volatility", "20-day realised vol (low is good)"),
    "vol60_low": ("-rolling_std(ret, 60)", "low_volatility", "60-day realised vol"),
    "turn20_low": ("-rolling_mean(turnover, 20)", "turnover_liquidity", "20-day mean turnover (low is good)"),
    "abn_turn_5_60": ("-(rolling_mean(turnover, 5) / rolling_mean(turnover, 60))", "abnormal_turnover", "abnormal turnover"),
    "size_small": ("-log_cap", "size", "small cap"),
    "amihud20": ("rolling_mean(abs(ret) / amount, 20)", "illiquidity", "Amihud illiquidity"),
    "maxret20_low": ("-rolling_max(ret, 20)", "lottery_maxret", "max daily return (low is good)"),
    "intra_over20": ("-rolling_mean(log(close / open) - log(open / prev_close), 20)", "intraday_overnight", "intraday minus overnight (reverse)"),
    "overnight20": ("rolling_mean(open / prev_close - 1, 20)", "intraday_overnight", "overnight return persistence"),
    "idio_vol20_low": ("-rolling_std(rolling_residual(ret, mkt_ret, 60), 20)", "idiosyncratic_vol", "idiosyncratic vol vs market"),
    "skew20_low": ("-rolling_skew(ret, 20)", "return_skewness", "return skewness"),
    "clv20": ("rolling_mean(where(high > low, (2 * close - high - low) / (high - low), 0), 20)", "close_location", "close location value"),
    "high52_prox": ("close / rolling_max(high, 250)", "52w_high", "proximity to 52-week high"),
    "vwap_dev": ("-(close / vwap - 1)", "vwap_deviation", "close vs vwap"),
    "pv_corr20": ("-rolling_corr(ret, log(volume), 20)", "price_volume_corr", "return-volume correlation"),
    "vol_of_turn20": ("-rolling_std(turnover, 20) / rolling_mean(turnover, 20)", "turnover_volatility", "cv of turnover"),
    "rev_vol_scaled": ("-(close / lag(close, 20) - 1) / rolling_std(ret, 20)", "short_reversal", "vol-scaled reversal"),
    "size_neutral_turn20": ("-cs_neutralize(cs_rank(rolling_mean(turnover, 20)), log_cap)", "turnover_liquidity", "size-neutral turnover"),
    "range20_low": ("-rolling_mean(log(high / low), 20)", "low_volatility", "average log range"),
}

ALPHA158_WINDOWS = (5, 10, 20, 30, 60)


def alpha158_lite() -> dict[str, str]:
    f = {"KMID": "(close - open) / open", "KLEN": "(high - low) / open", "KMID2": "(close - open) / (high - low + 1e-12)",
         "KUP": "(high - max(open, close)) / open", "KLOW": "(min(open, close) - low) / open",
         "KSFT": "(2 * close - high - low) / open"}
    for w in ALPHA158_WINDOWS:
        f[f"ROC{w}"] = f"lag(close, {w}) / close"
        f[f"MA{w}"] = f"rolling_mean(close, {w}) / close"
        f[f"STD{w}"] = f"rolling_std(close, {w}) / close"
        f[f"MAX{w}"] = f"rolling_max(high, {w}) / close"
        f[f"MIN{w}"] = f"rolling_min(low, {w}) / close"
        f[f"RSV{w}"] = f"(close - rolling_min(low, {w})) / (rolling_max(high, {w}) - rolling_min(low, {w}) + 1e-12)"
        f[f"CORR{w}"] = f"rolling_corr(close, log(volume + 1), {w})"
        f[f"CORD{w}"] = f"rolling_corr(close / lag(close, 1), log(volume / lag(volume, 1) + 1), {w})"
        f[f"CNTP{w}"] = f"rolling_mean(where(close > lag(close, 1), 1, 0), {w})"
        f[f"SUMP{w}"] = f"rolling_sum(max(close - lag(close, 1), 0), {w}) / (rolling_sum(abs(close - lag(close, 1)), {w}) + 1e-12)"
        f[f"VMA{w}"] = f"rolling_mean(volume, {w}) / (volume + 1e-12)"
        f[f"VSTD{w}"] = f"rolling_std(volume, {w}) / (volume + 1e-12)"
        f[f"WVMA{w}"] = f"rolling_std(abs(close / lag(close, 1) - 1) * volume, {w}) / (rolling_mean(abs(close / lag(close, 1) - 1) * volume, {w}) + 1e-12)"
        f[f"VSUMP{w}"] = f"rolling_sum(max(volume - lag(volume, 1), 0), {w}) / (rolling_sum(abs(volume - lag(volume, 1)), {w}) + 1e-12)"
        f[f"BETA{w}"] = f"rolling_beta(close / lag(close, 1) - 1, mkt_ret, {w})"
    return f


def legacy_catalog(path: str) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
    out = {}
    for it in items:
        expr = it["spec"]["expression"]
        out[it["family_id"]] = {"expression": expr, "roles": it.get("roles", []), "legacy_id": it["factor_id"]}
    return out


MINUTE_AGGS = {
    "d1": "{f}", "m5": "rolling_mean({f}, 5)", "m20": "rolling_mean({f}, 20)", "m60": "rolling_mean({f}, 60)",
    "ema10": "ema({f}, 10)", "s20": "rolling_std({f}, 20)", "tsz20": "ts_zscore({f}, 20)",
    "m20_ncap": "cs_neutralize(cs_rank(rolling_mean({f}, 20)), log_cap)",
    "m20_nturn": "cs_neutralize(cs_rank(rolling_mean({f}, 20)), cs_rank(rolling_mean(turnover, 20)))",
    "d5_20": "rolling_mean({f}, 5) - rolling_mean({f}, 20)",
}


def minute_set() -> dict[str, tuple[str, str, str]]:
    """Base candidates from the intraday fields: every field x every aggregation (mechanism = the field)."""
    from .minute_features import MINUTE_FIELDS
    out = {}
    for f in MINUTE_FIELDS:
        for tag, tmpl in MINUTE_AGGS.items():
            out[f"{f[3:]}~{tag}"] = (tmpl.format(f=f), f"minute:{f[3:]}", f"minute field {f} aggregated ({tag})")
    return out
