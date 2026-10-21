"""Once-only, isolated source reconstruction of a fixed issuer/window.

No original feature/DSL module is imported. Only primitive Arrow/pandas/NumPy
operations implement the pinned symbolic definitions. Default prints contract
usage; all source I/O and third-party imports require explicit execution.
"""
from __future__ import annotations

import argparse
import ast
import bisect
import datetime as dt
import gzip
import hashlib
import json
import math
import os
from pathlib import Path

SYMBOL = "000009.SZ"
START_DAY, END_DAY = 1545, 1565
START_T, END_T = 6181, 6260
RAW_COLUMNS = ["thscode", "date", "open", "high", "low", "close", "volume", "amount", "turnoverRatio"]
WINDOW_OPS = {"ema", "lag", "pct_change", "rolling_mean", "rolling_sum", "rolling_std", "rolling_max", "rolling_min", "rolling_skew", "rolling_cov", "rolling_corr", "ts_zscore"}
OPS = WINDOW_OPS | {"abs", "log", "min", "max", "where", "cs_rank", "cs_neutralize"}


class AuditError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise AuditError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def stat_identity(path):
    p = Path(path)
    require(p.is_file() and not p.is_symlink(), "not a regular original file: " + str(p))
    s = p.stat()
    return {"path": str(p), "bytes": s.st_size, "mtime_ns": s.st_mtime_ns, "inode": s.st_ino}


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "item"):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def write_json(path, value):
    data = (json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return {"path": str(path), "bytes": len(data), "sha256": sha(data)}


def read_pinned(item, observed):
    require(stat_identity(item["path"]) == item["stat"], "source STAT changed before read")
    data = Path(item["path"]).read_bytes()
    actual_sha = sha(data)
    require(len(data) == item["bytes"] and actual_sha == item["sha256"], "same-read source byte pin failed: " + item["path"])
    require(stat_identity(item["path"]) == item["stat"], "source STAT changed after read")
    observed.append({"path": item["path"], "bytes": len(data), "sha256": actual_sha, "body_reads": 1})
    return data


def validate_expression(text, expected_id):
    require(sha(text.encode())[:16] == expected_id, "canonical identity changed")
    tree = ast.parse(text, mode="eval")
    fields = set()
    for n in ast.walk(tree):
        require(not isinstance(n, (ast.Attribute, ast.Subscript, ast.Lambda, ast.ListComp, ast.GeneratorExp)), "unsafe symbolic syntax")
        if isinstance(n, ast.Call):
            require(isinstance(n.func, ast.Name) and n.func.id in OPS and not n.keywords, "unknown symbolic call")
            if n.func.id in WINDOW_OPS:
                w = n.args[-1]
                require(isinstance(w, ast.Constant) and type(w.value) is int and 1 <= w.value <= 250, "noncausal/unknown window")
        elif isinstance(n, ast.Name) and n.id not in OPS:
            require(n.id in {"open", "high", "low", "close", "volume", "amount", "turnover", "prev_close", "ret"}, "unknown raw field")
            fields.add(n.id)
        elif isinstance(n, ast.Constant):
            require(type(n.value) in (int, float) and math.isfinite(n.value), "invalid numeric constant")
    return tree.body, fields


def evaluate(node, fields, np, pd):
    """Restricted AST walker; never eval/compile expressions or import source Ops."""
    def frame(x, like):
        return x if isinstance(x, pd.DataFrame) else pd.DataFrame(float(x), index=like.index, columns=like.columns)

    def power(x, p):
        return np.sign(x) * np.abs(x) ** float(p) if p != int(p) else x ** int(p)

    if isinstance(node, ast.Name):
        return fields[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp):
        a = evaluate(node.operand, fields, np, pd)
        if isinstance(node.op, ast.USub):
            return -a
        if isinstance(node.op, ast.UAdd):
            return a
        if isinstance(node.op, (ast.Not, ast.Invert)):
            return (1 - (a != 0).astype(float)).where(a.notna()) if isinstance(a, pd.DataFrame) else float(not a)
        raise AuditError("unsupported unary operator")
    if isinstance(node, ast.BinOp):
        a = evaluate(node.left, fields, np, pd)
        b = evaluate(node.right, fields, np, pd)
        with np.errstate(all="ignore"):
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div):
                out = a / b if isinstance(a, pd.DataFrame) or isinstance(b, pd.DataFrame) else np.float64(a) / np.float64(b)
                return out.replace([np.inf, -np.inf], np.nan) if isinstance(out, pd.DataFrame) else float(out) if np.isfinite(out) else np.nan
            if isinstance(node.op, ast.Pow):
                return power(a, b)
        raise AuditError("unsupported arithmetic operator")
    if isinstance(node, ast.Compare):
        require(len(node.ops) == 1, "chained comparison refused")
        a = evaluate(node.left, fields, np, pd)
        b = evaluate(node.comparators[0], fields, np, pd)
        like = a if isinstance(a, pd.DataFrame) else b
        a, b = frame(a, like), frame(b, like)
        op = node.ops[0]
        if isinstance(op, ast.Gt): out = a > b
        elif isinstance(op, ast.GtE): out = a >= b
        elif isinstance(op, ast.Lt): out = a < b
        elif isinstance(op, ast.LtE): out = a <= b
        elif isinstance(op, ast.Eq): out = a == b
        elif isinstance(op, ast.NotEq): out = a != b
        else: raise AuditError("unknown comparison")
        return out.astype(float).where(a.notna() & b.notna())
    if isinstance(node, ast.BoolOp):
        vals = [evaluate(v, fields, np, pd) for v in node.values]
        like = next(v for v in vals if isinstance(v, pd.DataFrame))
        vals = [frame(v, like) for v in vals]
        out = vals[0]
        for value in vals[1:]:
            mask = (out != 0) & (value != 0) if isinstance(node.op, ast.And) else (out != 0) | (value != 0)
            out = mask.astype(float).where(out.notna() & value.notna())
        return out
    require(isinstance(node, ast.Call), "unsupported AST node")
    op = node.func.id
    a = [evaluate(n, fields, np, pd) for n in node.args]
    x = a[0]
    with np.errstate(all="ignore"):
        if op == "abs": out = x.abs()
        elif op == "log": out = np.log(x.where(x > 0))
        elif op in {"min", "max"}:
            like = x if isinstance(x, pd.DataFrame) else a[1]
            xx, yy = frame(x, like), frame(a[1], like)
            out = xx.combine(yy, np.minimum if op == "min" else np.maximum)
        elif op == "where":
            c = frame(x, a[1] if isinstance(a[1], pd.DataFrame) else a[2])
            aa, bb = frame(a[1], c), frame(a[2], c)
            out = bb.where(c <= 0, aa).where(c.notna())
        elif op == "lag": out = x.shift(a[1])
        elif op == "pct_change": out = x / x.shift(a[1]) - 1
        elif op == "ema": out = x.ewm(span=a[1], adjust=False, min_periods=a[1], ignore_na=False).mean()
        elif op == "cs_rank": out = x.rank(axis=1, pct=True)
        elif op == "cs_neutralize":
            y, z = x, frame(a[1], x)
            mask = y.notna() & z.notna()
            yy, zz = y.where(mask), z.where(mask)
            xm, ym = zz.sub(zz.mean(axis=1), axis=0), yy.sub(yy.mean(axis=1), axis=0)
            beta = (xm * ym).sum(axis=1) / (xm * xm).sum(axis=1)
            out = ym - xm.mul(beta, axis=0)
        else:
            w = a[-1]
            roll = x.rolling(w, min_periods=w)
            if op == "rolling_mean": out = roll.mean()
            elif op == "rolling_sum": out = roll.sum()
            elif op == "rolling_std": out = roll.std(ddof=1)
            elif op == "rolling_max": out = roll.max()
            elif op == "rolling_min": out = roll.min()
            elif op == "rolling_skew": out = roll.skew()
            elif op == "rolling_cov": out = roll.cov(frame(a[1], x))
            elif op == "rolling_corr": out = roll.corr(frame(a[1], x))
            elif op == "ts_zscore": out = (x - roll.mean()) / roll.std(ddof=1)
            else: raise AuditError("operator not in pinned selected set")
        return out.replace([np.inf, -np.inf], np.nan) if isinstance(out, pd.DataFrame) else out


def fields_for(frame, calendar, columns, np):
    values = {k: frame.pivot(index="date", columns="symbol", values=k).reindex(index=calendar, columns=columns) for k in ["open", "high", "low", "close", "volume", "amount", "turnover"]}
    values["prev_close"] = values["close"].shift(1)
    values["ret"] = values["close"] / values["prev_close"] - 1
    return values


def cleaned_daily(frame, np):
    key = ["date", "symbol"]
    numeric = RAW_COLUMNS[2:]
    duplicate = frame.duplicated(key, keep=False)
    if duplicate.any():
        conflicts = frame.loc[duplicate].groupby(key)[numeric].nunique(dropna=False)
        require(not (conflicts > 1).any().any(), "conflicting original daily numeric keys")
    out = frame.drop_duplicates(key).copy()
    prices = out[["open", "high", "low", "close"]]
    ok = np.isfinite(prices).all(axis=1) & prices.gt(0).all(axis=1)
    ok &= out.high.ge(prices.max(axis=1)) & out.low.le(prices.min(axis=1))
    ok &= np.isfinite(out.volume) & out.volume.ge(0) & np.isfinite(out.amount) & out.amount.ge(0)
    out.loc[~ok, numeric] = np.nan
    out["turnover"] = out.turnoverRatio / 100.0
    return out.sort_values(key), int((~ok).sum())


def daily15(fields, dates, np):
    c, o = fields["close"], fields["open"]
    r = np.log(c / c.shift(1))
    out = {}
    for n in (1, 5, 20, 60): out[f"d_ret{n}"] = np.log(c / c.shift(n)).shift(1)
    for n in (5, 20): out[f"d_vol{n}"] = r.rolling(n, min_periods=n).std(ddof=0).shift(1)
    for n in (5, 20, 60): out[f"d_ma{n}_dev"] = (c / c.rolling(n, min_periods=n).mean() - 1).shift(1)
    for key, name in (("volume", "d_relvol20"), ("amount", "d_relamount20")):
        den = fields[key].rolling(20, min_periods=20).mean()
        out[name] = (fields[key] / den.where(den > 0)).shift(1)
    out["d_turnover"] = fields["turnover"].shift(1)
    out["d_overnight"] = np.log(o / c.shift(1)).shift(1)
    out["d_intraday"] = np.log(c / o).shift(1)
    out["d_amihud20"] = (r.abs() / fields["amount"].where(fields["amount"] > 0)).rolling(20, min_periods=20).mean().shift(1)
    return {k: v.loc[dates, SYMBOL].to_numpy() for k, v in out.items()}


def financial18(financial, market, dates, calendar, np, pd):
    require(not financial.duplicated(["thscode", "asof_date", "report_period"]).any(), "duplicate original financial version")
    require(not market.duplicated(["thscode", "asof_date"]).any(), "duplicate original weekly query")
    require(not (financial.report_period > financial.asof_date).any(), "future financial report period")
    for key in ("source_update_date", "first_provider_event_date"):
        if key in financial:
            require(not (financial[key] > financial.asof_date).any(), "provider event after historical query")

    def next_session(query):
        pos = bisect.bisect_right(calendar, query)
        return calendar[pos] if pos < len(calendar) else pd.NaT

    def ratio(a, b, positive=False):
        return float(a / b) if np.isfinite(a) and np.isfinite(b) and (b > 0 if positive else b != 0) else np.nan

    records = financial.sort_values(["asof_date", "report_period"]).to_dict("records")
    grouped = []
    for row in records:
        if not grouped or grouped[-1][0] != row["asof_date"]: grouped.append((row["asof_date"], []))
        grouped[-1][1].append(row)
    mq = market.sort_values("asof_date").to_dict("records")
    state, cursor, mc, visible_market, last_query = {}, 0, 0, None, None
    values, provenance = [], []
    for day in dates:
        while cursor < len(grouped) and next_session(grouped[cursor][0]) <= day:
            last_query, updates = grouped[cursor]
            for row in updates: state[row["report_period"]] = row
            cursor += 1
        while mc < len(mq) and next_session(mq[mc]["asof_date"]) <= day:
            visible_market = mq[mc]
            mc += 1
        require(state and visible_market is not None, "no original visible snapshot for fixed issuer/date")
        period = max(state)
        cur = state[period]
        prev = state.get(period - pd.DateOffset(years=1), {})
        annual = state.get(pd.Timestamp(year=period.year - 1, month=12, day=31), {})
        def value(record, field): return record.get(field, np.nan)
        def ttm(field):
            if period.month == 12: return value(cur, field)
            a, b, c = value(cur, field), value(annual, field), value(prev, field)
            return float(a + b - c) if np.isfinite([a, b, c]).all() else np.nan
        profit, revenue_ttm = ttm("parent_net_profit"), ttm("revenue")
        assets, equity = value(cur, "total_assets"), value(cur, "parent_equity")
        revenue, net = value(cur, "revenue"), value(cur, "parent_net_profit")
        cap = visible_market["total_market_cap_cny"]
        cap = cap if np.isfinite(cap) and cap > 0 else np.nan
        version_available = next_session(cur["asof_date"])
        result = {
            "f_roe_ttm": ratio(profit, (equity + value(prev, "parent_equity")) / 2),
            "f_roa_ttm": ratio(profit, (assets + value(prev, "total_assets")) / 2, True),
            "f_gross_margin": ratio(revenue - value(cur, "operating_cost"), revenue),
            "f_parent_net_margin": ratio(net, revenue),
            "f_debt_asset": ratio(value(cur, "total_liabilities"), assets, True),
            "f_current_ratio": ratio(value(cur, "current_assets"), value(cur, "current_liabilities"), True),
            "f_cash_asset": ratio(value(cur, "cash"), assets, True),
            "f_receivable_asset": ratio(value(cur, "accounts_receivable"), assets, True),
            "f_inventory_asset": ratio(value(cur, "inventory"), assets, True),
            "f_ocf_ttm_asset": ratio(ttm("operating_cash_flow"), assets, True),
            "f_capex_ttm_asset": ratio(ttm("capex_cash_paid"), assets, True),
            "f_rd_ttm_revenue": ratio(ttm("rd_expense"), revenue_ttm),
            "f_revenue_yoy": ratio(revenue - value(prev, "revenue"), value(prev, "revenue")),
            "f_parent_profit_yoy": ratio(net - value(prev, "parent_net_profit"), abs(value(prev, "parent_net_profit"))),
            "f_earnings_price": profit / cap, "f_book_price": equity / cap,
            "d_log_total_cap": np.log(cap),
            "age_report_days": float((day - period).days),
            "age_version_days": float((day - version_available).days),
        }
        require(last_query < day and visible_market["asof_date"] < day, "same-day EOD snapshot leaked")
        values.append(result)
        provenance.append({"date": day, "symbol": SYMBOL, "event_query_date": last_query, "report_period": period, "report_query_date": cur["asof_date"], "financial_available_date": next_session(last_query), "report_version_available_date": version_available, "market_query_date": visible_market["asof_date"], "market_available_date": next_session(visible_market["asof_date"]), "TTM_operand_periods": [period, period - pd.DateOffset(years=1), pd.Timestamp(year=period.year - 1, month=12, day=31)]})
    return values, provenance


def reconstruct(daily, financial, market, contract, selected, np, pd):
    days = pd.DatetimeIndex(contract["mapping"]["date_order"])
    calendar = pd.DatetimeIndex(contract["all_calendar_dates"])
    cut = pd.Timestamp(contract["future_cut_date"])
    long_calendar = calendar[(calendar >= "2010-01-01") & (calendar <= cut)]
    state_calendar = calendar[(calendar >= "2016-01-01") & (calendar <= cut)]
    cs_calendar = calendar[(calendar >= contract["cs_history_start"]) & (calendar <= cut)]
    data, invalid = cleaned_daily(daily, np)
    single = data[data.symbol.eq(SYMBOL)]
    long_fields = fields_for(single, long_calendar, [SYMBOL], np)
    state_fields = fields_for(single, state_calendar, [SYMBOL], np)
    cs_data = data[data.date.ge(cs_calendar[0]) & data.symbol.isin(contract["union_symbols"])]
    cs_fields = fields_for(cs_data, cs_calendar, contract["union_symbols"], np)
    out = daily15(state_fields, days, np)
    fund, provenance = financial18(financial, market, days, list(calendar), np, pd)
    for key in fund[0]: out[key] = np.asarray([v[key] for v in fund], dtype=np.float64)
    for definition in selected:
        tree, required = validate_expression(definition["canonical"], definition["id"])
        require(sorted(required) == definition["required_fields"], "frozen source fields changed")
        cs = any(isinstance(n, ast.Call) and n.func.id.startswith("cs_") for n in ast.walk(tree))
        result = evaluate(tree, cs_fields if cs else long_fields, np, pd)
        out["factor_" + definition["id"]] = result.shift(1).loc[days, SYMBOL].to_numpy()
    array = np.column_stack([out[name] for name in contract["mapping"]["feature_names"]])
    array[~np.isfinite(array)] = np.nan
    return array.astype(np.float32), provenance, invalid


def compare(a, b, np):
    require(a.shape == b.shape == (21, 98) and a.dtype == b.dtype == np.float32, "raw comparison shape/dtype changed")
    mask = np.isfinite(a) & np.isfinite(b)
    mismatch = mask & (a.view(np.uint32) != b.view(np.uint32))
    return {"shape": [21, 98], "finite_cells": int(mask.sum()), "NaN_mask_mismatches": int((np.isnan(a) != np.isnan(b)).sum()), "finite_FP32_bit_mismatches": int(mismatch.sum()), "nonfinite_not_nan": int((~np.isfinite(a) & ~np.isnan(a)).sum() + (~np.isfinite(b) & ~np.isnan(b)).sum()), "mismatch_locations_first20": np.argwhere(mismatch)[:20].tolist()}


def read_reference(item, names, date_order, observed, np, pd, pa, pq):
    # One actual whole-file body read/hash is budgeted explicitly. Two in-memory
    # projections of that same buffer do not become two source-body reads.
    data = read_pinned(item, observed)
    stream = pa.BufferReader(data)
    parquet = pq.ParquetFile(stream, pre_buffer=False)
    group_rows = [parquet.metadata.row_group(i).num_rows for i in range(parquet.metadata.num_row_groups)]
    require(parquet.metadata.num_rows == item["rows"] and parquet.metadata.num_row_groups == item["num_row_groups"] and group_rows == item["row_group_rows"] and sum(group_rows) == item["rows"], "reference exact row groups/row count changed")
    for name in names:
        require(parquet.schema_arrow.field(name).type == pa.float32(), "original reference feature is not raw FP32")
    requested = set(date_order)
    keys = ["date", "symbol", "event_query_date", "report_period", "report_query_date", "financial_available_date", "report_version_available_date", "market_query_date", "market_available_date", "daily_source_date"]
    found, position = {}, 0
    for batch in parquet.iter_batches(batch_size=8192, columns=keys, use_threads=False):
        records = batch.to_pylist()
        for i, row in enumerate(records):
            date = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
            if row["symbol"] == SYMBOL and date in requested:
                require(date not in found, "duplicate original fixed issuer/date tuple")
                found[date] = {"row_ordinal": position + i, "provenance": row}
        position += len(records)
    require(set(found) == requested, "missing original raw tuples; issuer is never changed")
    indices = {v["row_ordinal"]: i for i, date in enumerate(date_order) for v in [found[date]]}
    result = np.full((21, 98), np.nan, dtype=np.float32)
    position, copied = 0, set()
    for batch in parquet.iter_batches(batch_size=8192, columns=names, use_threads=False):
        for ordinal in sorted(set(indices) - copied):
            if position <= ordinal < position + batch.num_rows:
                j = ordinal - position
                result[indices[ordinal]] = [np.nan if not batch.column(k)[j].is_valid else batch.column(k)[j].as_py() for k in range(98)]
                copied.add(ordinal)
        position += batch.num_rows
        if len(copied) == 21: break
    require(len(copied) == 21, "reference numeric projection incomplete")
    trace = {"whole_file_STAT_bytes": item["bytes"], "actual_physical_source_body_bytes_read": len(data), "source_body_reads": 1, "actual_num_row_groups": parquet.metadata.num_row_groups, "actual_row_group_rows": group_rows, "global_row_ordinal_across_row_groups": True, "same_read_whole_file_SHA256": observed[-1]["sha256"], "logical_selected_numeric_bytes": int(result.nbytes), "projected_numeric_rows_selected": 21, "current_whole_file_SHA_recomputed_in_same_read": True, "numeric_stream_rows_decoded": position, "provenance_stream_rows_decoded": parquet.metadata.num_rows, "two_projections_of_one_buffer_not_two_source_reads": True}
    stream.close()
    require(stat_identity(item["path"]) == item["stat"], "reference STAT changed after projection")
    return result, found, trace


def execute(contract_path, manifest_path, expected_contract_sha, output):
    # Exclusive marker exists even if imports, data or parity checks fail.
    contract_bytes = Path(contract_path).read_bytes()
    require(sha(contract_bytes) == expected_contract_sha, "explicit contract pin differs")
    contract = json.loads(contract_bytes)
    require(sha(Path(__file__).read_bytes()) == contract["auditor_source_sha256"], "isolated auditor source changed")
    manifest_bytes = Path(manifest_path).read_bytes()
    require(sha(manifest_bytes) == contract["manifest_sha256"], "manifest changed")
    manifest = json.loads(manifest_bytes)
    output = Path(output)
    require(str(output) == contract["output_directory"], "output identity changed")
    output.mkdir(parents=True, exist_ok=False)
    marker = write_json(output / "READONLY_SOURCE_RECONSTRUCTION_STARTED_v2.json", {"created_utc": utc(), "source_sha256": contract["auditor_source_sha256"], "contract_sha256": expected_contract_sha, "no_retry_or_replacement_output": True})
    observed, artifacts, before = [], [], {}
    result = {"schema_version": "ISOLATED_SLOW98_RAW_SOURCE_RECONSTRUCTION_V2", "started_utc": utc(), "status": "STARTED_NOT_ACCEPTED", "mapping": contract["mapping"], "marker": marker, "implementation_repair_provenance": contract["implementation_repair_provenance"], "full_PLAN_complete": False, "economic_target_accepted": False, "all_issuer_PIT_verified": False, "strict_end_to_end_model_future_invariance_passed": False, "issuer_publication_and_historical_arrival": "UNKNOWN", "original_pipeline_tests_models_scores_IC_training_or_network_executed": False}
    try:
        import numpy as np
        import pandas as pd
        import pyarrow as pa
        import pyarrow.parquet as pq
        require(contract["mapping"]["symbol"] == SYMBOL and contract["mapping"]["symbol_index"] == 2, "fixed issuer changed")
        require(contract["mapping"]["day_indices"] == list(range(START_DAY, END_DAY + 1)) and contract["mapping"]["time_indices"] == list(range(START_T, END_T + 1)), "fixed window changed")
        guarded_bytes = {}
        for item in manifest["small_guards"]:
            data = read_pinned(item, observed)
            before[item["path"]] = sha(data)
            guarded_bytes[item["path"]] = data
        require(contract["selection_path"] in guarded_bytes, "selection is not a pinned small source")
        selected = json.loads(guarded_bytes[contract["selection_path"]])["selected"]
        require(len(selected) == 64, "frozen64 definitions changed")
        require(len(manifest["daily"]) == 243 and manifest["daily_rows"] == 1758978, "fixed daily decode roster changed")
        members = {}
        for item in manifest["memberships"]:
            payload = json.loads(gzip.decompress(read_pinned(item, observed)))
            codes = payload["thscode"]
            require(len(codes) == len(set(codes)), "duplicate source membership codes")
            members[item["date"]] = set(codes)
        for date in contract["mapping"]["date_order"]:
            require(SYMBOL in members[date], "fixed issuer not a historical member; no replacement permitted")
        chunks = []
        decoded_rows = 0
        cut, cs_start = pd.Timestamp(contract["future_cut_date"]), pd.Timestamp(contract["cs_history_start"])
        for item in manifest["daily"]:
            data = read_pinned(item, observed)
            table = pq.read_table(pa.BufferReader(data), columns=RAW_COLUMNS)
            require(table.num_rows == item["rows"], "current daily source row count changed")
            decoded_rows += table.num_rows
            frame = table.to_pandas().rename(columns={"thscode": "symbol"})
            frame["date"] = pd.to_datetime(frame.date)
            require(frame.date.dt.strftime("%Y-%m").eq(item["month"]).all(), "source dates outside original monthly partition")
            keep = frame.symbol.eq(SYMBOL) | (frame.date.ge(cs_start) & frame.date.le(cut) & frame.symbol.isin(contract["union_symbols"]))
            frame = frame.loc[keep].copy()
            for row in frame.loc[frame.date.ge(cs_start) & frame.date.le(cut), ["date", "symbol"]].itertuples(index=False):
                require(row.symbol in members[row.date.strftime("%Y-%m-%d")], "numeric CS source contains a nonmember")
            chunks.append(frame)
        require(decoded_rows == manifest["daily_rows"], "daily decode bound differs")
        daily = pd.concat(chunks, ignore_index=True)
        financial = pq.read_table(pa.BufferReader(read_pinned(manifest["financial"], observed)), columns=manifest["financial_columns"]).to_pandas()
        market = pq.read_table(pa.BufferReader(read_pinned(manifest["market"], observed)), columns=["thscode", "asof_date", "total_market_cap_cny"]).to_pandas()
        require(len(financial) == manifest["financial"]["rows"] and len(market) == manifest["market"]["rows"], "financial/weekly row bound differs")
        financial = financial.loc[financial.thscode.eq(SYMBOL)].copy()
        market = market.loc[market.thscode.eq(SYMBOL)].copy()
        for name in ["asof_date", "report_period", "source_update_date", "first_provider_event_date"]:
            if name in financial: financial[name] = pd.to_datetime(financial[name])
        market["asof_date"] = pd.to_datetime(market.asof_date)
        baseline, provenance, invalid = reconstruct(daily, financial, market, contract, selected, np, pd)
        # Preserve completed numeric reconstruction even if reference reading fails.
        baseline_path = output / "rebuilt_slow98_000009_21days.npy"
        with baseline_path.open("xb") as f: np.save(f, baseline, allow_pickle=False)
        baseline_bytes = baseline_path.read_bytes()
        artifacts.append({"path": str(baseline_path), "bytes": len(baseline_bytes), "sha256": sha(baseline_bytes), "shape": [21, 98], "dtype": "float32"})
        reference, source_rows, read_trace = read_reference(manifest["raw_reference"], contract["mapping"]["feature_names"], contract["mapping"]["date_order"], observed, np, pd, pa, pq)
        result["original_raw_FP32_comparison"] = compare(baseline, reference, np)
        result["reference_tuple_rows"] = source_rows
        result["reference_read_trace"] = read_trace
        result["retained_numeric_daily_rows"] = len(daily)
        result["decoded_daily_rows"] = decoded_rows
        result["invalid_retained_source_rows_masked"] = invalid
        for expected in provenance:
            date = expected["date"].strftime("%Y-%m-%d")
            actual = source_rows[date]["provenance"]
            for key in ["event_query_date", "report_period", "report_query_date", "financial_available_date", "report_version_available_date", "market_query_date", "market_available_date"]:
                require(pd.Timestamp(actual[key]) == expected[key], "original raw financial provenance mismatch: " + date + "/" + key)
            calendar = contract["all_calendar_dates"]
            previous = calendar[bisect.bisect_left(calendar, date) - 1]
            require(pd.Timestamp(actual["daily_source_date"]) == pd.Timestamp(previous), "original daily_source_date changed")
        # Three nonvacuous future branches, all changed only in memory.
        future_daily = daily.copy(deep=True)
        dmask = future_daily.date.ge(cut)
        require(dmask.any(), "no real future daily/CS numeric source witness")
        changed_daily = 0
        for name in ["open", "high", "low", "close", "volume", "amount", "turnoverRatio"]:
            mask = dmask & np.isfinite(future_daily[name])
            changed_daily += int(mask.sum())
            future_daily.loc[mask, name] = future_daily.loc[mask, name] * 1.13 + (0 if name in {"open", "high", "low", "close"} else 1)
            if name in {"open", "high", "low", "close"}:
                # A distinct issuer-local rebase makes the late CS witness
                # nonvacuous instead of only a rank-preserving common scale.
                target = mask & future_daily.symbol.eq(SYMBOL)
                future_daily.loc[target, name] *= 1.37
        require(changed_daily > 0 and (dmask & future_daily.symbol.ne(SYMBOL)).any(), "future CS pool mutation vacuous")
        cs_calendar = pd.DatetimeIndex(contract["all_calendar_dates"])
        cs_calendar = cs_calendar[(cs_calendar >= cs_start) & (cs_calendar <= cut)]
        original_cs, _ = cleaned_daily(daily, np)
        changed_cs, _ = cleaned_daily(future_daily, np)
        cs_original_fields = fields_for(original_cs[original_cs.date.ge(cs_start)], cs_calendar, contract["union_symbols"], np)
        cs_changed_fields = fields_for(changed_cs[changed_cs.date.ge(cs_start)], cs_calendar, contract["union_symbols"], np)
        cs_changes = {}
        for definition in selected:
            node, _ = validate_expression(definition["canonical"], definition["id"])
            if any(isinstance(n, ast.Call) and n.func.id.startswith("cs_") for n in ast.walk(node)):
                a = evaluate(node, cs_original_fields, np, pd).loc[cut].to_numpy()
                b = evaluate(node, cs_changed_fields, np, pd).loc[cut].to_numpy()
                cs_changes[definition["id"]] = int((np.isfinite(a) & np.isfinite(b) & (a != b)).sum())
        require(len(cs_changes) == 4 and sum(cs_changes.values()) > 0, "changed late CS source never changed a CS result")
        future_fin, future_market = financial.copy(deep=True), market.copy(deep=True)
        changed_fin = changed_market = 0
        for name in ("parent_net_profit", "revenue"):
            mask = future_fin.asof_date.ge(cut) & np.isfinite(future_fin[name])
            changed_fin += int(mask.sum())
            future_fin.loc[mask, name] = future_fin.loc[mask, name] * 1.17 + 1000000
        mask = future_market.asof_date.ge(cut) & np.isfinite(future_market.total_market_cap_cny)
        changed_market = int(mask.sum())
        future_market.loc[mask, "total_market_cap_cny"] *= 1.21
        require(changed_fin > 0 and changed_market > 0, "future financial/query source mutation vacuous")
        past_rows = financial[financial.asof_date.lt(cut)].sort_values(["report_period", "asof_date"])
        latest_period = past_rows.report_period.max()
        old = past_rows[past_rows.report_period.lt(latest_period)].iloc[-1].copy()
        old["asof_date"] = cut
        require(np.isfinite(old["parent_net_profit"]), "old-report query revision witness missing")
        old["parent_net_profit"] += 2000000
        duplicate = future_fin.asof_date.eq(cut) & future_fin.report_period.eq(old["report_period"])
        future_fin = pd.concat([future_fin.loc[~duplicate], pd.DataFrame([old])], ignore_index=True)
        mutated, mutated_provenance, _ = reconstruct(future_daily, future_fin, future_market, contract, selected, np, pd)
        result["future_prefix_FP32_comparison"] = compare(baseline, mutated, np)
        require(clean(provenance) == clean(mutated_provenance), "future branch changed past provenance")
        calendar = pd.DatetimeIndex(contract["all_calendar_dates"])
        next_cut = calendar[calendar.searchsorted(cut, side="right")]
        late_fin = financial[financial.asof_date.ge(cut)].sort_values(["asof_date", "report_period"]).iloc[0]
        real_future_available = calendar[calendar.searchsorted(late_fin.asof_date, side="right")]
        future_dates = pd.DatetimeIndex(sorted({next_cut, real_future_available}))
        original_late, original_late_provenance = financial18(financial, market, future_dates, list(calendar), np, pd)
        mutated_late, mutated_late_provenance = financial18(future_fin, future_market, future_dates, list(calendar), np, pd)
        late_keys = contract["mapping"]["feature_names"][80:]
        late_a = np.asarray([[v[k] for k in late_keys] for v in original_late], dtype=np.float64)
        late_b = np.asarray([[v[k] for k in late_keys] for v in mutated_late], dtype=np.float64)
        late_changed = np.isfinite(late_a) & np.isfinite(late_b) & (late_a != late_b)
        require(late_changed.any(), "late financial mutation never became visible")
        require(late_changed[:, :14].any(), "late financial witness changed only market cap-derived values")
        require(mutated_late_provenance[0]["event_query_date"] == cut and mutated_late_provenance[0]["financial_available_date"] == next_cut, "same-day old revision did not first activate next session")
        result["future_mutation_witness"] = {"cut_date": cut, "late_EOD_queries_first_usable_strictly_next_session": True, "actual_future_daily_numeric_cells_changed": changed_daily, "unshifted_late_CS_finite_cells_changed_by_factor": cs_changes, "unshifted_late_CS_not_an_eligible_June18_feature": True, "actual_future_financial_cells_changed": changed_fin, "actual_future_market_cells_changed": changed_market, "first_real_future_financial_query_date": late_fin.asof_date, "first_real_future_financial_report_period": late_fin.report_period, "first_real_future_financial_available_date": real_future_available, "future_check_dates_not_training_or_forecast_results": future_dates.tolist(), "late_financial_finite_values_changed": int(late_changed.sum()), "late_original_provenance": original_late_provenance, "late_mutated_provenance": mutated_late_provenance, "additional_old_report_same_day_EOD_revision": {"report_period": old["report_period"], "query_date": cut, "first_available_date": next_cut, "explicitly_counterfactual_not_provider_fact": True}, "changes_only_in_memory_original_sources_not_written": True}
        for filename, array in [("original_slow98_000009_21days.npy", reference), ("future_mutated_rebuilt_slow98_000009_21days.npy", mutated)]:
            with (output / filename).open("xb") as f: np.save(f, array, allow_pickle=False)
            data = (output / filename).read_bytes()
            artifacts.append({"path": str(output / filename), "bytes": len(data), "sha256": sha(data), "shape": [21, 98], "dtype": "float32"})
        artifacts.append(write_json(output / "mapping.json", contract["mapping"]))
        artifacts.append(write_json(output / "provenance_21.json", provenance))
        for key in ("original_raw_FP32_comparison", "future_prefix_FP32_comparison"):
            comparison = result[key]
            require(comparison["NaN_mask_mismatches"] == comparison["finite_FP32_bit_mismatches"] == comparison["nonfinite_not_nan"] == 0, key + " failed; no tolerance/retry")
        for item in manifest["small_guards"]:
            require(stat_identity(item["path"]) == item["stat"] and sha(Path(item["path"]).read_bytes()) == before[item["path"]], "small guard changed")
        for item in manifest["daily"] + manifest["memberships"] + [manifest["financial"], manifest["market"], manifest["raw_reference"]]:
            require(stat_identity(item["path"]) == item["stat"], "original source STAT changed at finish")
        result["status"] = "PASS_FIXED_ISSUER_SOURCE98x21_RAW_FP32_AND_FUTURE_PREFIX_QUERY_CONVENTION_ONLY"
        result["original_small_SHA_and_large_STAT_unchanged"] = True
    except BaseException as exc:
        result["status"] = "FAILED_OR_INTERRUPTED_NO_ACCEPTANCE_NO_RETRY"
        result["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        result["source_read_observations"] = observed
        result["artifacts"] = artifacts
        result["finished_utc"] = utc()
        write_json(output / "actual_source_reconstruction_receipt_v2.json", result)
        raise
    result["source_read_observations"] = observed
    result["artifacts"] = artifacts
    result["finished_utc"] = utc()
    receipt = write_json(output / "actual_source_reconstruction_receipt_v2.json", result)
    print(json.dumps({"status": result["status"], "receipt": receipt, "full_PLAN_complete": False}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--contract-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status": "PREPARED_ONLY_NO_SOURCE_READ", "requires": "--execute --contract PATH --manifest PATH --contract-sha256 SHA --output EXACT_NEW_ATTEMPT1", "arrays": {"rebuilt_slow98_000009_21days.npy": [21, 98], "original_slow98_000009_21days.npy": [21, 98], "future_mutated_rebuilt_slow98_000009_21days.npy": [21, 98]}, "full_PLAN_complete": False}))
        return
    require(all([args.contract, args.manifest, args.contract_sha256, args.output]), "explicit execution inputs missing")
    execute(args.contract, args.manifest, args.contract_sha256, args.output)


if __name__ == "__main__":
    main()
