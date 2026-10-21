"""Causal daily/PIT features and one-time, training-only legacy-factor selection.

Source data and legacy factor implementations are read-only. Outputs are raw
values with NaNs, for normalization by the common hourly sample assembler.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "gru_research/artifacts"
DAILY_ROOT = ROOT / "中证500_历年成分股_日频_2010-2026_iFinD_后复权"
FUND_ROOT = ROOT / "中证500_历年成分股_基本面_2010-2026_iFinD_PIT"
DAILY16 = ["d_ret1", "d_ret5", "d_ret20", "d_ret60", "d_vol5", "d_vol20",
           "d_ma5_dev", "d_ma20_dev", "d_ma60_dev", "d_relvol20",
           "d_relamount20", "d_turnover", "d_overnight", "d_intraday",
           "d_amihud20", "d_log_total_cap"]
FUND16 = ["f_roe_ttm", "f_roa_ttm", "f_gross_margin", "f_parent_net_margin",
          "f_debt_asset", "f_current_ratio", "f_cash_asset", "f_receivable_asset",
          "f_inventory_asset", "f_ocf_ttm_asset", "f_capex_ttm_asset",
          "f_rd_ttm_revenue", "f_revenue_yoy", "f_parent_profit_yoy",
          "f_earnings_price", "f_book_price"]
AGES2 = ["age_report_days", "age_version_days"]


def safe_ratio(a, b, *, positive=False):
    if np.isfinite(a) and np.isfinite(b) and (b > 0 if positive else b != 0):
        return float(a / b)
    return np.nan


def next_session(asof, calendar):
    """A historical EOD query is usable strictly after its query date."""
    days = pd.DatetimeIndex(calendar)
    positions = days.searchsorted(pd.DatetimeIndex(asof), side="right")
    result = np.full(len(positions), np.datetime64("NaT"), dtype="datetime64[ns]")
    ok = positions < len(days)
    result[ok] = days.values[positions[ok]]
    return pd.to_datetime(result)


def load_legacy_compiler():
    sys.path.insert(0, str(ROOT / "quantagent/src"))
    module = importlib.import_module("quanta_agents.factor_lab_a.dsl")
    return module.Compiler(strict=False)


def financial_events(financial, calendar):
    """Compute snapshots only from versions already present at each query EOD.

    Updates to old reports enter the state at their own query date, never at the
    report-period date. Missing prior-year operands produce missing TTM values.
    """
    fin = financial.rename(columns={"thscode": "symbol"}).copy()
    fin["asof_date"] = pd.to_datetime(fin.asof_date)
    fin["report_period"] = pd.to_datetime(fin.report_period)
    if fin.duplicated(["symbol", "asof_date", "report_period"]).any():
        raise ValueError("duplicate PIT report-version keys")
    if (fin.report_period > fin.asof_date).any():
        raise ValueError("future report period in PIT data")
    events = []
    for symbol, group in fin.groupby("symbol", sort=False):
        state = {}
        for asof, updates in group.sort_values(["asof_date", "report_period"]).groupby("asof_date", sort=False):
            for row in updates.to_dict("records"):
                state[row["report_period"]] = row
            period = max(state)
            cur = state[period]
            previous = state.get(period - pd.DateOffset(years=1), {})
            annual = state.get(pd.Timestamp(year=period.year - 1, month=12, day=31), {})
            def value(record, field):
                return record.get(field, np.nan)
            def ttm(field):
                if period.month == 12:
                    return value(cur, field)
                a, b, c = value(cur, field), value(annual, field), value(previous, field)
                return float(a + b - c) if np.isfinite([a, b, c]).all() else np.nan
            np_ttm = ttm("parent_net_profit")
            revenue_ttm = ttm("revenue")
            assets, equity = value(cur, "total_assets"), value(cur, "parent_equity")
            average_assets = (assets + value(previous, "total_assets")) / 2
            average_equity = (equity + value(previous, "parent_equity")) / 2
            revenue, np_cur = value(cur, "revenue"), value(cur, "parent_net_profit")
            record = {"symbol": symbol, "event_query_date": asof,
                      "report_period": period, "report_query_date": cur["asof_date"],
                      "ttm_parent_profit": np_ttm, "parent_equity": equity,
                      "f_roe_ttm": safe_ratio(np_ttm, average_equity),
                      "f_roa_ttm": safe_ratio(np_ttm, average_assets, positive=True),
                      "f_gross_margin": safe_ratio(revenue - value(cur, "operating_cost"), revenue),
                      "f_parent_net_margin": safe_ratio(np_cur, revenue),
                      "f_debt_asset": safe_ratio(value(cur, "total_liabilities"), assets, positive=True),
                      "f_current_ratio": safe_ratio(value(cur, "current_assets"), value(cur, "current_liabilities"), positive=True),
                      "f_cash_asset": safe_ratio(value(cur, "cash"), assets, positive=True),
                      "f_receivable_asset": safe_ratio(value(cur, "accounts_receivable"), assets, positive=True),
                      "f_inventory_asset": safe_ratio(value(cur, "inventory"), assets, positive=True),
                      "f_ocf_ttm_asset": safe_ratio(ttm("operating_cash_flow"), assets, positive=True),
                      "f_capex_ttm_asset": safe_ratio(ttm("capex_cash_paid"), assets, positive=True),
                      "f_rd_ttm_revenue": safe_ratio(ttm("rd_expense"), revenue_ttm),
                      "f_revenue_yoy": safe_ratio(revenue - value(previous, "revenue"), value(previous, "revenue")),
                      "f_parent_profit_yoy": safe_ratio(np_cur - value(previous, "parent_net_profit"), abs(value(previous, "parent_net_profit")))}
            events.append(record)
    result = pd.DataFrame(events)
    result["financial_available_date"] = next_session(result.event_query_date, calendar)
    result["report_version_available_date"] = next_session(result.report_query_date, calendar)
    return result.dropna(subset=["financial_available_date"])


def pit_join(member_days, financial, market, calendar):
    """Join only strictly query-lagged snapshots and retain their provenance."""
    target = member_days.copy()
    target["date"] = pd.to_datetime(target.date)
    events = financial_events(financial, calendar)
    market = market.rename(columns={"thscode": "symbol"}).copy()
    market["asof_date"] = pd.to_datetime(market.asof_date)
    market["market_available_date"] = next_session(market.asof_date, calendar)
    market = market.rename(columns={"asof_date": "market_query_date"}).dropna(subset=["market_available_date"])
    if market.duplicated(["symbol", "market_query_date"]).any():
        raise ValueError("duplicate weekly market keys")
    result = pd.merge_asof(target.sort_values(["date", "symbol"]),
                           events.sort_values(["financial_available_date", "symbol"]),
                           left_on="date", right_on="financial_available_date", by="symbol", direction="backward")
    result = pd.merge_asof(result.sort_values(["date", "symbol"]),
                           market.sort_values(["market_available_date", "symbol"]),
                           left_on="date", right_on="market_available_date", by="symbol", direction="backward")
    cap = result.total_market_cap_cny.where(result.total_market_cap_cny > 0)
    result["f_earnings_price"] = result.ttm_parent_profit / cap
    result["f_book_price"] = result.parent_equity / cap
    result["age_report_days"] = (result.date - result.report_period).dt.days.astype(float)
    result["age_version_days"] = (result.date - result.report_version_available_date).dt.days.astype(float)
    # Taxonomy revision and name mapping are unverified: no invented embedding ID.
    result["industry_code"] = np.int16(0)
    for key in ["financial_available_date", "report_version_available_date", "market_available_date"]:
        if (result[key] > result.date).any():
            raise AssertionError(f"future PIT join: {key}")
    return result.sort_values(["date", "symbol"]).reset_index(drop=True)


def daily_state(fields):
    """15 market features use the previous full exchange session (no gap fill)."""
    close, open_ = fields["close"], fields["open"]
    ret = np.log(close / close.shift(1))
    out = {}
    for n in [1, 5, 20, 60]:
        out[f"d_ret{n}"] = np.log(close / close.shift(n)).shift(1)
    for n in [5, 20]:
        out[f"d_vol{n}"] = ret.rolling(n, min_periods=n).std(ddof=0).shift(1)
    for n in [5, 20, 60]:
        out[f"d_ma{n}_dev"] = (close / close.rolling(n, min_periods=n).mean() - 1).shift(1)
    for field, name in [("volume", "d_relvol20"), ("amount", "d_relamount20")]:
        denominator = fields[field].rolling(20, min_periods=20).mean()
        out[name] = (fields[field] / denominator.where(denominator > 0)).shift(1)
    out["d_turnover"] = fields["turnover"].shift(1)
    out["d_overnight"] = np.log(open_ / close.shift(1)).shift(1)
    out["d_intraday"] = np.log(close / open_).shift(1)
    out["d_amihud20"] = (ret.abs() / fields["amount"].where(fields["amount"] > 0)).rolling(20, min_periods=20).mean().shift(1)
    return out


def read_daily(root=DAILY_ROOT, *, history_start="2016-01-01", end="2026-09-23", symbols=None):
    frames = []
    needed = ["thscode", "date", "open", "high", "low", "close", "volume", "amount", "turnoverRatio"]
    for path in sorted((root / "bars").rglob("*.parquet")):
        year = path.relative_to(root / "bars").parts[0]
        if year < history_start[:4] or year > end[:4]:
            continue
        frame = pd.read_parquet(path, columns=needed)
        frame = frame[(frame.date >= history_start) & (frame.date <= end)]
        if symbols is not None:
            frame = frame[frame.thscode.isin(symbols)]
        frames.append(frame)
    daily = pd.concat(frames, ignore_index=True).rename(columns={"thscode": "symbol"})
    daily["date"] = pd.to_datetime(daily.date)
    duplicates = int(daily.duplicated(["date", "symbol"]).sum())
    if duplicates:
        # Request groups may overlap only if values agree exactly.
        conflicts = daily[daily.duplicated(["date", "symbol"], keep=False)].groupby(["date", "symbol"])[needed[2:]].nunique(dropna=False)
        if (conflicts > 1).any().any():
            raise ValueError("conflicting duplicate daily source rows")
        daily = daily.drop_duplicates(["date", "symbol"])
    prices = daily[["open", "high", "low", "close"]]
    valid = np.isfinite(prices).all(axis=1) & prices.gt(0).all(axis=1)
    valid &= daily.high.ge(prices.max(axis=1)) & daily.low.le(prices.min(axis=1))
    valid &= np.isfinite(daily.volume) & daily.volume.ge(0) & np.isfinite(daily.amount) & daily.amount.ge(0)
    bad = int((~valid).sum())
    daily.loc[~valid, needed[2:]] = np.nan
    daily["turnover"] = daily.turnoverRatio / 100.0
    return daily.sort_values(["date", "symbol"]), {"source_rows": len(daily), "identical_duplicates_removed": duplicates, "invalid_ohlcv_rows_masked": bad}


def wide_fields(daily, calendar, symbols):
    fields = {name: daily.pivot(index="date", columns="symbol", values=name).reindex(index=calendar, columns=symbols)
              for name in ["open", "high", "low", "close", "volume", "amount", "turnover"]}
    fields["prev_close"] = fields["close"].shift(1)
    fields["ret"] = fields["close"] / fields["prev_close"] - 1
    return fields


def at_members(frame, members):
    a = frame.index.get_indexer(pd.to_datetime(members.date))
    b = frame.columns.get_indexer(members.symbol)
    if (a < 0).any() or (b < 0).any():
        raise ValueError("member keys outside daily matrix")
    return frame.to_numpy()[a, b]


def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def build_base(*, end="2026-09-23", limit_symbols=None):
    members = pd.read_parquet(ART / "minute_memberships.parquet")
    members = members[members.date <= end].sort_values(["date", "symbol"]).reset_index(drop=True)
    symbols = sorted(members.symbol.unique())
    if limit_symbols:
        symbols = symbols[:limit_symbols]
        members = members[members.symbol.isin(symbols)].reset_index(drop=True)
    all_calendar = pd.DatetimeIndex(sorted(p.name[:10] for p in (DAILY_ROOT / "memberships").glob("*.json.gz")))
    calendar = all_calendar[(all_calendar >= "2016-01-01") & (all_calendar <= end)]
    daily, audit = read_daily(end=end, symbols=symbols)
    fields = wide_fields(daily, calendar, symbols)
    financial = pd.read_parquet(FUND_ROOT / "financial_verified_19_fields.parquet")
    market = pd.read_parquet(FUND_ROOT / "weekly_market_and_industry.parquet")
    financial = financial[financial.thscode.isin(symbols)]
    market = market[market.thscode.isin(symbols)]
    raw = pit_join(members, financial, market, all_calendar)
    for name, values in daily_state(fields).items():
        raw[name] = at_members(values, raw)
    raw["d_log_total_cap"] = np.log(raw.total_market_cap_cny.where(raw.total_market_cap_cny > 0))
    numeric = DAILY16 + FUND16 + AGES2
    raw[numeric] = raw[numeric].replace([np.inf, -np.inf], np.nan).astype(np.float32)
    raw["daily_source_date"] = calendar.to_series().shift(1).reindex(pd.to_datetime(raw.date)).to_numpy()
    path = ART / ("slow_sample_raw.parquet" if limit_symbols else "slow_daily_raw.parquet")
    raw.to_parquet(path, index=False, compression="zstd")
    coverage = raw.assign(year=raw.date.dt.year).groupby("year")[numeric].agg(lambda x: np.isfinite(x).mean())
    coverage.to_csv(ART / ("slow_sample_coverage.csv" if limit_symbols else "slow_coverage.csv"))
    manifest = {"raw_path": str(path), "rows": len(raw), "symbols": len(symbols), "start": str(raw.date.min()), "end": str(raw.date.max()),
                "daily16": DAILY16, "fund16": FUND16, "ages2": AGES2, "audit": audit,
                "industry_code_policy": "All 0 (unknown): historical supplier S-codes retained separately; taxonomy revisions/name mapping unverified",
                "daily_timing": "15 market features: previous full exchange session, no price forward-fill; d_log_total_cap: most recent weekly query available strictly next session",
                "daily_return_definition": "log returns, population daily return volatility; relative volume/amount over trailing 20 full observations including source session",
                "fundamental_definition": "TTM current YTD + previous annual - previous same-period YTD, using visible revisions only; annual current YTD is TTM; R&D denominator is TTM revenue",
                "pit_timing": "asof_date historical EOD queries first usable next exchange session; preserve missing statement operands",
                "sources": {"financial_verified_19_fields.parquet": sha(FUND_ROOT / "financial_verified_19_fields.parquet"), "weekly_market_and_industry.parquet": sha(FUND_ROOT / "weekly_market_and_industry.parquet"), "memberships": sha(ART / "minute_memberships.parquet")},
                "data_missing_policy": "Raw NaN retained; assembler must supply per-feature missing flags and normalize on historical membership"}
    write_json(ART / ("slow_sample_manifest.json" if limit_symbols else "slow_manifest.json"), manifest)
    print(json.dumps({"phase": "base_complete", "rows": len(raw), "path": str(path), "coverage": coverage.mean().round(4).to_dict()}, ensure_ascii=False), flush=True)
    return raw, fields, calendar, symbols


def price_scale_invariant(compiler, expression):
    """Reject formulas whose output changes under per-security price rebasing.

    Daily backend is forward-in-time HFQ; ratio formulas are unchanged by a
    constant security-specific QFQ/HFQ rebase. Absolute-price cross-sectional
    signals do not have that property and are explicitly excluded.
    """
    rng = np.random.default_rng(118)
    def required_history(node):
        child = max([required_history(x) for x in ast.iter_child_nodes(node)] or [0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in {"lag", "delay", "delta", "pct_change"}:
                return required_history(node.args[0]) + int(node.args[-1].value)
            if name.startswith("rolling_") or name.startswith("ts_") or name in {"ema", "decay_linear"}:
                return max(required_history(x) for x in node.args[:-1]) + int(node.args[-1].value) - 1
        return child
    length = max(1000, required_history(ast.parse(expression, mode="eval").body) + 500)
    index = pd.date_range("1990-01-01", periods=length)
    columns = list("abcdef")
    close = pd.DataFrame(np.exp(np.cumsum(rng.normal(0, .02, (length, 6)), axis=0)) * 50, index=index, columns=columns)
    fields = {"close": close, "open": close * np.exp(rng.normal(0, .004, close.shape)),
              "high": close * 1.03, "low": close * .97,
              "volume": pd.DataFrame(rng.uniform(1e6, 1e7, close.shape), index=index, columns=columns),
              "amount": pd.DataFrame(rng.uniform(1e8, 1e9, close.shape), index=index, columns=columns),
              "turnover": pd.DataFrame(rng.uniform(.002, .08, close.shape), index=index, columns=columns)}
    fields["prev_close"] = close.shift(1)
    fields["ret"] = close / close.shift(1) - 1
    scale = pd.Series([.3, 1.2, 3., 8., 22., 100.], index=columns)
    balanced = {k: (v / 1e7 if k in {"volume", "amount"} else v * 1000 if k == "turnover" else v)
                for k, v in fields.items()}
    for probe_fields in [fields, balanced]:
        rebased = {k: (v * scale if k in {"open", "high", "low", "close", "prev_close"} else v)
                   for k, v in probe_fields.items()}
        a = np.asarray(compiler.evaluate(expression, fields=probe_fields), dtype=np.float64)
        b = np.asarray(compiler.evaluate(expression, fields=rebased), dtype=np.float64)
        common = np.isfinite(a) & np.isfinite(b)
        if not (common.sum() >= 500 and np.array_equal(np.isfinite(a), np.isfinite(b))
                and np.allclose(a[common], b[common], rtol=1e-9, atol=1e-10)):
            return False
    return True


def audit_factor_catalog(compiler, available):
    catalog = json.loads((ROOT / "quantagent/research/factors/catalog.json").read_text())["records"]
    eligible, rejected = [], []
    for row in catalog:
        definition = row["definition"]
        record = {key: definition.get(key) for key in ["id", "name", "kind", "canonical", "expression", "source"]}
        if definition.get("kind", "dsl") != "dsl":
            record["reason"] = "External implementation absent/unverifiable or composite depends on old trained direction/normalization"
            rejected.append(record)
            continue
        try:
            expression = compiler.canonical(definition["canonical"])
            required = compiler.required_fields(expression)
            missing = required - available
            if missing:
                record["reason"] = "Unavailable fields or exact input definition unverified: " + ",".join(sorted(missing))
                rejected.append(record)
                continue
            if not price_scale_invariant(compiler, expression):
                record["reason"] = "Not invariant to per-security price rebase, or no valid invariant test outputs; absolute QFQ formula cannot be verified on HFQ backend"
                rejected.append(record)
                continue
            record.update(canonical=expression, required_fields=sorted(required), price_rebase_probe="passed")
            eligible.append(record)
        except Exception as exc:
            record["reason"] = f"Formula audit failed: {type(exc).__name__}: {exc}"
            rejected.append(record)
    return eligible, rejected


def build_candidates():
    raw = pd.read_parquet(ART / "slow_daily_raw.parquet", columns=["date", "symbol"])
    symbols = sorted(raw.symbol.unique())
    calendar = pd.DatetimeIndex(sorted(p.name[:10] for p in (DAILY_ROOT / "memberships").glob("*.json.gz")))
    calendar = calendar[(calendar >= "2010-01-01") & (calendar <= raw.date.max())]
    daily, _ = read_daily(history_start="2010-01-01", end=str(raw.date.max())[:10], symbols=symbols)
    fields = wide_fields(daily, calendar, symbols)
    compiler = load_legacy_compiler()
    eligible, rejected = audit_factor_catalog(compiler, set(fields))
    train = raw[(raw.date >= "2019-01-01") & (raw.date <= "2022-12-31")].reset_index(drop=True)
    target = ART / "slow_factor_candidates"
    expected_cache = {"history_start": "2010-01-01", "formula_backend": sha(ROOT / "quantagent/src/quanta_agents/factor_lab_a/dsl.py"),
                      "timing": "calendar shift1", "train_start": "2019-01-01", "train_end": "2022-12-31"}
    if target.exists() and not (target / "cache_manifest.json").exists():
        # Keep the earlier trial, including any negative evidence, before a
        # changed history contract forces full recomputation.
        archive = ART / "slow_factor_candidates_history2016_unselected"
        if archive.exists():
            raise RuntimeError("unrecognized candidate cache and existing archive; inspect before replacing")
        target.rename(archive)
    target.mkdir(parents=True, exist_ok=True)
    if (target / "cache_manifest.json").exists() and json.loads((target / "cache_manifest.json").read_text()) != expected_cache:
        raise RuntimeError("candidate cache contract changed; do not silently reuse")
    write_json(target / "cache_manifest.json", expected_cache)
    train.to_parquet(target / "keys.parquet", index=False, compression="zstd")
    write_json(ART / "slow_factor_formula_audit.json", {"catalog_total": len(eligible) + len(rejected), "eligible": eligible, "rejected": rejected,
               "legacy_compiler_sha256": sha(ROOT / "quantagent/src/quanta_agents/factor_lab_a/dsl.py"),
               "scope": "Only registered formulas; no historical ranking/direction imported; full 2019-2022 reranking awaits hourly final-target labels"})
    for number, definition in enumerate(eligible, 1):
        path = target / f"{definition['id']}.parquet"
        if path.exists():
            continue
        try:
            value = compiler.evaluate(definition["canonical"], fields=fields).shift(1)
            arr = at_members(value, train).astype(np.float32)
            arr[~np.isfinite(arr)] = np.nan
            pd.DataFrame({"value": arr}).to_parquet(path, index=False, compression="zstd")
            definition["train_coverage"] = float(np.isfinite(arr).mean())
            print(json.dumps({"phase": "candidate", "i": number, "n": len(eligible), "id": definition["id"], "coverage": definition["train_coverage"]}), flush=True)
        except Exception as exc:
            definition["compute_error"] = f"{type(exc).__name__}: {exc}"
            print(json.dumps({"phase": "candidate_error", "id": definition["id"], "error": definition["compute_error"]}), flush=True)
    write_json(ART / "slow_factor_formula_audit.json", {"catalog_total": len(eligible) + len(rejected), "eligible": eligible, "rejected": rejected,
               "legacy_compiler_sha256": sha(ROOT / "quantagent/src/quanta_agents/factor_lab_a/dsl.py"), "train_keys_sha256": sha(target / "keys.parquet")})


def mean_cross_section_corr(a, b, day_positions):
    """Training-period mean of actual same-day Pearson correlations."""
    if isinstance(day_positions, np.ndarray):
        codes = day_positions
    else:
        codes = np.empty(len(a), dtype=int)
        for code, positions in enumerate(day_positions):
            codes[positions] = code
    a, b = a.astype(np.float64), b.astype(np.float64)
    mask = np.isfinite(a) & np.isfinite(b)
    count = np.bincount(codes, weights=mask)
    mean_a = np.bincount(codes, weights=np.where(mask, a, 0)) / np.maximum(count, 1)
    mean_b = np.bincount(codes, weights=np.where(mask, b, 0)) / np.maximum(count, 1)
    da, db = np.where(mask, a - mean_a[codes], 0), np.where(mask, b - mean_b[codes], 0)
    covariance = np.bincount(codes, weights=da * db)
    denominator = np.sqrt(np.bincount(codes, weights=da * da) * np.bincount(codes, weights=db * db))
    good = (count >= 100) & (denominator > 0) & np.isfinite(denominator)
    return float(np.mean(covariance[good] / denominator[good])) if good.any() else np.nan


def row_rank_ic(x, y):
    """Pairwise-valid cross-section Spearman correlations for complete matrices."""
    mask = np.isfinite(x) & np.isfinite(y)
    n = mask.sum(axis=1)
    xr = pd.DataFrame(x).where(mask).rank(axis=1).to_numpy()
    yr = pd.DataFrame(y).where(mask).rank(axis=1).to_numpy()
    mx = np.nansum(xr, axis=1) / np.maximum(n, 1)
    my = np.nansum(yr, axis=1) / np.maximum(n, 1)
    dx, dy = np.where(mask, xr - mx[:, None], 0), np.where(mask, yr - my[:, None], 0)
    denominator = np.sqrt((dx * dx).sum(axis=1) * (dy * dy).sum(axis=1))
    result = np.full(len(x), np.nan)
    good = (n >= 100) & (denominator > 0) & np.isfinite(denominator)
    result[good] = (dx[good] * dy[good]).sum(axis=1) / denominator[good]
    return result


def normalize_training_factor(values, day_codes, within_day_positions):
    """Apply the PLAN input transform over every historical member-day cohort."""
    n_days = int(day_codes.max()) + 1
    width = int(within_day_positions.max()) + 1
    matrix = np.full((n_days, width), np.nan, dtype=np.float64)
    matrix[day_codes, within_day_positions] = values
    with np.errstate(all="ignore"):
        lo = np.nanquantile(matrix, .01, axis=1, method="linear")
        hi = np.nanquantile(matrix, .99, axis=1, method="linear")
        clipped = np.clip(matrix, lo[:, None], hi[:, None])
        mean = np.nanmean(clipped, axis=1)
        std = np.nanstd(clipped, axis=1, ddof=0)
        scale = np.where(np.isfinite(std) & (std > 0), std, 1.0)
        normalized = np.clip((clipped - mean[:, None]) / scale[:, None], -5, 5)
    return normalized[day_codes, within_day_positions].astype(np.float32)


def exchange_timestamps(values):
    """Normalize numeric UTC ns or stored timestamps to naive Shanghai clock."""
    if pd.api.types.is_numeric_dtype(values.dtype):
        return pd.to_datetime(values, utc=True, unit="ns").dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    result = pd.to_datetime(values)
    if result.dt.tz is not None:
        result = result.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    return result


def read_training_labels(path):
    """Push down the training interval before loading any later numeric labels."""
    schema = pq.ParquetFile(path).schema_arrow
    dtype = schema.field("signal_time").type
    if pa.types.is_integer(dtype) or pa.types.is_floating(dtype):
        lower = pd.Timestamp("2019-01-01", tz="Asia/Shanghai").tz_convert("UTC").value
        upper = pd.Timestamp("2023-01-01", tz="Asia/Shanghai").tz_convert("UTC").value
    elif pa.types.is_timestamp(dtype) and dtype.tz:
        lower = pd.Timestamp("2019-01-01", tz="Asia/Shanghai").tz_convert(dtype.tz)
        upper = pd.Timestamp("2023-01-01", tz="Asia/Shanghai").tz_convert(dtype.tz)
    else:
        lower, upper = pd.Timestamp("2019-01-01"), pd.Timestamp("2023-01-01")
    columns = ["signal_time", "symbol", "y4", "label_end_time4"]
    columns += [name for name in ["is_member", "window80_complete"] if name in schema.names]
    frame = pd.read_parquet(path, columns=columns, filters=[("signal_time", ">=", lower), ("signal_time", "<", upper)])
    for name in ["is_member", "window80_complete"]:
        if name in frame:
            frame = frame[frame[name].fillna(False).astype(bool)].copy()
    frame["signal_time"] = exchange_timestamps(frame.signal_time)
    frame["label_end_time4"] = exchange_timestamps(frame.label_end_time4)
    return frame


def purge_training_labels(labels, calendar):
    """All target endpoints precede the first of two boundary buffer sessions."""
    labels = labels.copy()
    labels["date"] = labels.signal_time.dt.normalize()
    days = pd.DatetimeIndex(calendar)
    train_days = days[(days >= "2019-01-01") & (days < "2023-01-01")]
    if len(train_days) < 2:
        raise ValueError("training calendar must include at least two boundary sessions")
    buffer_start = train_days[-2]
    return labels[np.isfinite(labels.y4) & (labels.signal_time >= "2019-01-01") &
                  (labels.signal_time < buffer_start) & (labels.label_end_time4 < buffer_start)].copy()


def select_factors(label_paths):
    """Execute once on mature, purged 2019-2022 hourly y4; never consult later labels."""
    result_path = ART / "slow_factor_selection.json"
    if result_path.exists():
        raise RuntimeError("factor selection already frozen; reuse its list (no repeated selection)")
    keys = pd.read_parquet(ART / "slow_factor_candidates/keys.parquet")
    keys["date"] = pd.to_datetime(keys.date)
    if not ((keys.date >= "2019-01-01") & (keys.date < "2023-01-01")).all():
        raise ValueError("factor candidate keys must contain only 2019-2022 training dates")
    inputs = []
    for path in label_paths:
        frame = read_training_labels(path)
        if len(frame):
            inputs.append(frame)
    if not inputs:
        raise ValueError("no training labels supplied")
    labels = pd.concat(inputs, ignore_index=True)
    calendar = pd.DatetimeIndex(json.loads((ART / "minute_calendar.json").read_text())["dates"])
    labels = purge_training_labels(labels, calendar)
    keys["key_position"] = np.arange(len(keys))
    labels = labels.merge(keys, on=["date", "symbol"], how="inner", validate="many_to_one")
    times = pd.DatetimeIndex(sorted(labels.signal_time.unique()))
    symbols = sorted(keys.symbol.unique())
    row = times.get_indexer(labels.signal_time)
    col = pd.Index(symbols).get_indexer(labels.symbol)
    if labels.duplicated(["signal_time", "symbol"]).any():
        raise ValueError("duplicate training label keys")
    key_grid = np.full((len(times), len(symbols)), -1, dtype=np.int32)
    label_grid = np.full(key_grid.shape, np.nan, dtype=np.float32)
    key_grid[row, col] = labels.key_position.to_numpy()
    label_grid[row, col] = labels.y4.to_numpy()
    valid_cross_sections = np.isfinite(label_grid).sum(axis=1) >= 100
    if not valid_cross_sections.any() or set(times.year[valid_cross_sections]) != {2019, 2020, 2021, 2022}:
        raise ValueError("all four complete training years are required for one-time selection")
    times = times[valid_cross_sections]
    key_grid, label_grid = key_grid[valid_cross_sections], label_grid[valid_cross_sections]
    definitions = json.loads((ART / "slow_factor_formula_audit.json").read_text())["eligible"]
    day_positions = pd.factorize(keys.date, sort=True)[0]
    within_day_positions = keys.groupby("date", sort=False).cumcount().to_numpy()
    norm_cache = ART / "slow_factor_normalized_train"
    norm_cache.mkdir(parents=True, exist_ok=True)
    scored = []
    for number, definition in enumerate(definitions, 1):
        path = ART / "slow_factor_candidates" / f"{definition['id']}.parquet"
        if not path.exists():
            scored.append(dict(definition, status="compute_failed"))
            continue
        values = pd.read_parquet(path).value.to_numpy()
        coverage = {str(year): float(np.isfinite(values[keys.date.dt.year.to_numpy() == year]).mean()) for year in [2019, 2020, 2021, 2022]}
        overall_coverage = float(np.isfinite(values).mean())
        cohort_values = values[np.maximum(key_grid, 0)]
        cohort_finite = (key_grid >= 0) & np.isfinite(cohort_values) & np.isfinite(label_grid)
        label_cohort_coverage = float(cohort_finite.sum() / np.isfinite(label_grid).sum())
        if overall_coverage < .90:
            scored.append(dict(definition, status="coverage_rejected", overall_coverage=overall_coverage, annual_coverage=coverage, label_cohort_coverage=label_cohort_coverage))
            continue
        values = normalize_training_factor(values, day_positions, within_day_positions)
        pd.DataFrame({"value": values}).to_parquet(norm_cache / f"{definition['id']}.parquet", index=False, compression="zstd")
        x_grid = values[np.maximum(key_grid, 0)]
        x_grid[key_grid < 0] = np.nan
        ic = row_rank_ic(x_grid, label_grid)
        year_ics = {year: ic[(times.year == year) & np.isfinite(ic)] for year in [2019, 2020, 2021, 2022]}
        annual = {str(y): float(np.mean(v)) if len(v) else np.nan for y, v in year_ics.items()}
        numbers = np.array(list(annual.values()))
        stable = np.isfinite(numbers).all() and max((numbers > 0).sum(), (numbers < 0).sum()) >= 3
        scored.append(dict(definition, status="eligible" if stable else "sign_stability_rejected", overall_coverage=overall_coverage, annual_coverage=coverage, label_cohort_coverage=label_cohort_coverage,
                           annual_rank_ic=annual, score=float(np.median(np.abs(numbers))) if np.isfinite(numbers).all() else None,
                           annual_valid_cross_sections={str(y): len(v) for y, v in year_ics.items()}))
        if number % 10 == 0:
            print(json.dumps({"phase": "selection_ic", "done": number, "total": len(definitions), "eligible_so_far": sum(x["status"] == "eligible" for x in scored)}), flush=True)
    chosen = []
    selected_values = []
    for record in sorted((x for x in scored if x["status"] == "eligible"), key=lambda x: (-x["score"], x["id"])):
        if len(chosen) >= 64:
            record["status"] = "beyond_64"
            continue
        values = pd.read_parquet(norm_cache / f"{record['id']}.parquet").value.to_numpy()
        duplicate = None
        for selected, selected_array in zip(chosen, selected_values):
            corr = mean_cross_section_corr(values, selected_array, day_positions)
            if np.isfinite(corr) and abs(corr) > .90:
                duplicate = {"id": selected["id"], "mean_daily_cross_section_pearson": corr}
                break
        if duplicate:
            record.update(status="correlation_rejected", duplicate=duplicate)
        elif len(chosen) < 64:
            record["status"] = "selected"
            chosen.append(record)
            selected_values.append(values)
            print(json.dumps({"phase": "selection_dedup", "selected": len(chosen), "id": record["id"]}), flush=True)
        else:
            record["status"] = "beyond_64"
    result = {"train_start": "2019-01-01", "train_end": "2022-12-31", "label": "hourly y4 execution-window log return",
              "coverage_rule": "overall 2019-2022 historical membership-day finite coverage >= 0.90; annual and paired label-cohort coverage reported without additional rejection thresholds",
              "factor_preprocessing": "historical member-day 1%/99% linear-quantile winsorization, population z-score, clip +/-5; missing values excluded from IC and correlation, never imputed for selection",
              "rank_rule": "median absolute annual mean hourly cross-section RankIC; >=3 of 4 years same sign",
              "dedup_rule": "absolute training-period mean same-day normalized/winsorized Pearson cross-sectional correlation >0.90",
              "purge": "signal and actual label endpoint both strictly before first of final two complete training sessions",
              "selected_ids": [x["id"] for x in chosen], "selected": chosen, "all_results": scored,
              "training_cross_sections": len(times), "training_label_rows": len(labels),
              "keys_sha256": sha(ART / "slow_factor_candidates/keys.parquet")}
    write_json(result_path, result)
    print(json.dumps({"phase": "selection_frozen", "selected": len(chosen), "cross_sections": len(times)}), flush=True)
    return result


def finalize():
    selection = json.loads((ART / "slow_factor_selection.json").read_text())
    raw = pd.read_parquet(ART / "slow_daily_raw.parquet")
    symbols = sorted(raw.symbol.unique())
    calendar = pd.DatetimeIndex(sorted(p.name[:10] for p in (DAILY_ROOT / "memberships").glob("*.json.gz")))
    calendar = calendar[(calendar >= "2010-01-01") & (calendar <= raw.date.max())]
    daily, _ = read_daily(history_start="2010-01-01", end=str(raw.date.max())[:10], symbols=symbols)
    fields = wide_fields(daily, calendar, symbols)
    compiler = load_legacy_compiler()
    names = []
    for definition in selection["selected"]:
        name = f"factor_{definition['id']}"
        value = compiler.evaluate(definition["canonical"], fields=fields).shift(1)
        raw[name] = at_members(value, raw).astype(np.float32)
        names.append(name)
    raw[DAILY16 + FUND16 + AGES2 + names] = raw[DAILY16 + FUND16 + AGES2 + names].replace([np.inf, -np.inf], np.nan)
    raw.to_parquet(ART / "slow_raw.parquet", index=False, compression="zstd")
    manifest = json.loads((ART / "slow_manifest.json").read_text())
    manifest.update(raw_path=str(ART / "slow_raw.parquet"), factors=names, factor_selection_sha256=sha(ART / "slow_factor_selection.json"), raw_sha256=sha(ART / "slow_raw.parquet"))
    write_json(ART / "slow_manifest.json", manifest)
    print(json.dumps({"phase": "finalized", "rows": len(raw), "factors": len(names)}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["sample", "base", "candidates", "select", "finalize"])
    parser.add_argument("--labels", nargs="+", type=Path)
    args = parser.parse_args()
    ART.mkdir(parents=True, exist_ok=True)
    if args.phase == "sample":
        build_base(end="2019-06-30", limit_symbols=5)
    elif args.phase == "base":
        build_base()
    elif args.phase == "candidates":
        build_candidates()
    elif args.phase == "select":
        select_factors(args.labels or [])
    else:
        finalize()


if __name__ == "__main__":
    main()
