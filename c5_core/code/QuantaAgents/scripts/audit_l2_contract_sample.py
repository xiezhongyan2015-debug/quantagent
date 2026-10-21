"""Freeze and audit a small L2 sample; no returns, models, or source-data writes.

Run ``python scripts/audit_l2_contract_sample.py plan`` before ``audit``.
Only manifest-selected files, projected columns and matching row groups are read.
Compressed column-chunk bytes are a conservative payload budget, not OS I/O bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path("F:/2026(QQ群1097616051后续增量更新)/l2_a_share_parquet")
DEFAULT_OUT = PROJECT / "experiment_traces/meta_l2_contract_audit"
DAILY = PROJECT / "data_cache/periodic_active_buying/tencent_qfq_daily_20260101_20260722.parquet"
DATES = ["2026-01-06", "2026-01-07", "2026-01-08"]
COMMON = ["code", "wind_code", "exchange_code", "date", "time_raw", "event_ts", "source_row_no"]
COLUMNS = {
    "quotes": COMMON + ["last_price_x1e4", "trade_volume", "trade_amount", "trade_count", "trade_flag", "bs_flag",
        "cum_volume", "cum_amount", "high_price_x1e4", "low_price_x1e4", "open_price_x1e4", "prev_close_x1e4",
        "ask_price_1_x1e4", "bid_price_1_x1e4", "ask_volume_1", "bid_volume_1", "total_ask_volume", "total_bid_volume"],
    "orders": COMMON + ["order_id", "exchange_order_id", "order_type", "order_side", "order_price_x1e4", "order_quantity"],
    "trades": COMMON + ["trade_id", "trade_code", "order_code", "bs_flag", "trade_price_x1e4", "trade_quantity", "ask_order_id", "bid_order_id"],
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def jd(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def manifest(root, date):
    path = root / "manifests" / (date.replace("-", "") + ".json")
    raw = path.read_bytes()
    value = json.loads(raw)
    if value.get("status") != "complete" or value.get("trade_date") != date:
        raise ValueError(f"manifest incomplete/wrong date: {date}")
    return value, {"path": str(path), "sha256": digest(raw), "bytes": len(raw)}


def chunk_bytes(pf, group, columns):
    row_group = pf.metadata.row_group(group)
    return sum(row_group.column(pf.schema_arrow.get_field_index(c)).total_compressed_size for c in columns)


def stat_range(pf, group):
    stats = pf.metadata.row_group(group).column(pf.schema_arrow.get_field_index("wind_code")).statistics
    if not stats or not stats.has_min_max:
        return None
    return stats.min, stats.max


def create_plan(root, out, dates, cap_bytes):
    out.mkdir(parents=True, exist_ok=True)
    if (out / "plan.json").exists():
        raise FileExistsError("frozen plan exists; audit resumes that plan, it never reselects stocks")
    first, first_identity = manifest(root, "2026-01-05")
    available = list(first["validation"]["quotes"]["source_rows_by_wind_code"])
    selected = []
    for exchange, prefix in [("SH", "60"), ("SZ", "00")]:
        pool = [x for x in available if x.endswith("." + exchange) and x.startswith(prefix)]
        selected.extend(sorted(pool, key=lambda x: digest(x.encode("ascii")))[:4])
    if len(selected) != 8:
        raise ValueError("first-day manifest has fewer than four stocks on each main board")
    sources, identities = [], [first_identity]
    for date in dates:
        m, identity = manifest(root, date)
        identities.append(identity)
        declared = {x["relative_path"].replace("\\", "/"): x for x in m["output_files"]}
        for table, columns in COLUMNS.items():
            selected_parts = {}
            for code in selected:
                parts = [p for p in m["table_parts"][table] if p["first_code"] <= code <= p["last_code"]]
                if len(parts) != 1:
                    raise ValueError(f"no unique part for {table} {date} {code}")
                selected_parts.setdefault(parts[0]["file"], []).append(code)
            for name, codes in selected_parts.items():
                path = root / table / ("trade_date=" + date) / name
                pf = pq.ParquetFile(path)
                if set(columns) - set(pf.schema_arrow.names):
                    raise ValueError(f"required projection absent: {path}")
                candidates = []
                for i in range(pf.metadata.num_row_groups):
                    bounds = stat_range(pf, i)
                    if bounds is None or any(bounds[0] <= c <= bounds[1] for c in codes):
                        candidates.append(i)
                stat = path.stat()
                declared_file = declared[f"{table}/{name}"]
                if stat.st_size != declared_file["bytes"]:
                    raise ValueError(f"file size differs from manifest: {path}")
                sources.append({"table": table, "date": date, "path": str(path), "codes": codes,
                    "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                    "manifest_file_sha256_unverified": declared_file["sha256"],
                    "row_groups_total": pf.metadata.num_row_groups, "candidate_row_groups": candidates,
                    "candidate_rows": sum(pf.metadata.row_group(i).num_rows for i in candidates),
                    "code_scan_compressed_bytes": sum(chunk_bytes(pf, i, ["wind_code"]) for i in candidates),
                    "projection_upper_compressed_bytes": sum(chunk_bytes(pf, i, columns) for i in candidates),
                    "expected_selected_rows": {c: m["validation"][table]["source_rows_by_wind_code"].get(c, 0) for c in codes}})
    daily_pf = pq.ParquetFile(DAILY)
    daily_cols = ["code", "date", "qfq_open", "qfq_close", "qfq_high", "qfq_low", "volume", "corporate_action_note"]
    daily_cost = sum(chunk_bytes(daily_pf, i, daily_cols) for i in range(daily_pf.metadata.num_row_groups))
    plan = {"version": 1, "selection_date": "2026-01-05", "dates": dates, "codes": selected,
        "selection": "For SH 60* and SZ 00* in first-day quotes manifest: SHA256(ASCII wind_code), ascending, first four each; no returns or prices used.",
        "selection_hashes": {c: digest(c.encode("ascii")) for c in selected},
        "manifests": identities, "columns": COLUMNS, "sources": sources, "payload_cap_bytes": cap_bytes,
        "cost_before_read": {"code_scan_compressed_bytes": sum(s["code_scan_compressed_bytes"] for s in sources),
             "projection_upper_compressed_bytes": sum(s["projection_upper_compressed_bytes"] for s in sources),
             "daily_projection_compressed_bytes": daily_cost,
             "method": "Read candidate wind_code chunks first; only matched groups get wider projections. Footer/metadata/OS overhead excluded."},
        "daily": {"path": str(DAILY), "size": DAILY.stat().st_size, "mtime_ns": DAILY.stat().st_mtime_ns,
                  "columns": daily_cols},
        "no_return_calculation": True, "no_model_calls": True, "official_supplier_schema_available": False}
    # Daily identity is metadata here; do not claim a freshly computed full-file hash.
    plan["plan_sha256"] = jd(plan)
    save(out / "plan.json", plan)
    return plan


def frequencies(series):
    return {str(k): int(v) for k, v in series.fillna("<null>").value_counts().items()}


def ratio(a, b):
    return float(a / b) if b and np.isfinite(a) and np.isfinite(b) else None


def phase_counts(times):
    sec = times.dt.hour * 3600 + times.dt.minute * 60 + times.dt.second + times.dt.microsecond / 1e6
    conditions = [sec < 33300, sec < 33900, sec < 34200, sec < 36000, sec <= 41400,
                  sec < 46800, sec < 53820, sec <= 54000]
    labels = ["before_0915", "0915_0925", "0925_0930", "0930_1000", "1000_1130", "lunch_1130_1300", "1300_1457", "1457_1500"]
    return frequencies(pd.Series(np.select(conditions, labels, default="after_1500")))


def common_summary(frame, expected):
    ordered = frame.sort_values("source_row_no", kind="stable")
    ts = ordered.event_ts
    raw = ts.dt.hour * 10000000 + ts.dt.minute * 100000 + ts.dt.second * 1000 + ts.dt.microsecond // 1000
    logical = [c for c in frame.columns if c not in {"source_row_no"}]
    return {"rows": len(frame), "manifest_expected_rows": expected, "rows_match_manifest": len(frame) == expected,
        "nulls": {c: int(n) for c, n in frame.isna().sum().items() if n},
        "source_row_no_duplicates": int(frame.source_row_no.duplicated().sum()),
        "logical_full_row_duplicates_excluding_source_row_no": int(frame.duplicated(logical).sum()),
        "repeated_event_timestamps": int(frame.event_ts.duplicated().sum()),
        "physical_event_ts_backsteps": int((frame.event_ts.diff() < pd.Timedelta(0)).sum()),
        "source_order_event_ts_backsteps": int((ts.diff() < pd.Timedelta(0)).sum()),
        "raw_hhmmssmmm_timestamp_mismatch": int((raw != ordered.time_raw).sum()),
        "event_date_mismatch": int((ts.dt.date != ordered.date).sum()),
        "first_event": str(ts.min()), "last_event": str(ts.max()), "phase_rows": phase_counts(ts),
        "time_raw_samples": [int(x) for x in ordered.time_raw.iloc[[0, len(ordered)//2, -1]]],
        "projected_rows_sha256": digest(pd.util.hash_pandas_object(ordered, index=False).values.tobytes())}


def summarize_quotes(q, expected):
    result = common_summary(q, expected)
    ordered = q.sort_values(["event_ts", "source_row_no"], kind="stable")
    active = ordered[ordered.cum_volume > 0]
    last = active.iloc[-1] if len(active) else ordered.iloc[-1]
    sec = ordered.event_ts.dt.hour * 3600 + ordered.event_ts.dt.minute * 60 + ordered.event_ts.dt.second
    regular = ordered[((sec >= 34200) & (sec <= 41400)) | ((sec >= 46800) & (sec < 53820))]
    both = regular[(regular.ask_price_1_x1e4 > 0) & (regular.bid_price_1_x1e4 > 0)]
    result.update(cum_volume_backsteps=int((ordered.cum_volume.diff() < 0).sum()),
        cum_amount_backsteps=int((ordered.cum_amount.diff() < 0).sum()),
        final_cum_volume=int(last.cum_volume), final_cum_amount=int(last.cum_amount),
        raw_daily_ohlc={"open": float(last.open_price_x1e4 / 10000), "high": float(last.high_price_x1e4 / 10000),
                        "low": float(last.low_price_x1e4 / 10000), "close": float(last.last_price_x1e4 / 10000)},
        trade_flag_counts=frequencies(q.trade_flag), bs_flag_counts=frequencies(q.bs_flag),
        quote_trade_volume_sum=int(q.trade_volume.sum()), quote_trade_amount_sum=int(q.trade_amount.sum()),
        positive_two_sided_regular_quotes=len(both), regular_quotes=len(regular),
        crossed_regular_quotes=int((both.bid_price_1_x1e4 > both.ask_price_1_x1e4).sum()),
        zero_best_bid_or_ask_regular=int(((regular.bid_price_1_x1e4 <= 0) | (regular.ask_price_1_x1e4 <= 0)).sum()),
        positive_best_price_not_multiple_100=int(((both.ask_price_1_x1e4 % 100 != 0) | (both.bid_price_1_x1e4 % 100 != 0)).sum()),
        negative_depth_rows=int(((q.ask_volume_1 < 0) | (q.bid_volume_1 < 0)).sum()),
        max_observed_continuous_quote_gap_seconds=_max_continuous_gap(ordered.event_ts))
    return result


def _max_continuous_gap(times):
    seconds = times.dt.hour * 3600 + times.dt.minute * 60 + times.dt.second
    gaps = []
    for lo, hi in [(34200, 41400), (46800, 53820)]:
        group = times[(seconds >= lo) & (seconds <= hi)].sort_values()
        if len(group) > 1:
            gaps.append(group.diff().dt.total_seconds().max())
    return float(max(gaps)) if gaps else None


def summarize_trades(t, expected):
    result = common_summary(t, expected)
    positive = t[(t.trade_price_x1e4 > 0) & (t.trade_quantity > 0) & t.trade_code.fillna("").ne("C")]
    result.update(trade_code_counts=frequencies(t.trade_code), order_code_counts=frequencies(t.order_code),
        bs_flag_counts=frequencies(t.bs_flag), positive_price_quantity_rows=len(positive),
        zero_or_negative_price_rows=int((t.trade_price_x1e4 <= 0).sum()),
        zero_or_negative_quantity_rows=int((t.trade_quantity <= 0).sum()),
        positive_quantity_sum=int(positive.trade_quantity.sum()),
        positive_price_times_quantity_div_1e4=float((positive.trade_price_x1e4.astype(float) * positive.trade_quantity / 10000).sum()),
        positive_quantity_not_multiple_100=int((positive.trade_quantity % 100 != 0).sum()),
        positive_price_not_multiple_100=int((positive.trade_price_x1e4 % 100 != 0).sum()),
        duplicate_trade_id=int(t.trade_id.duplicated().sum()),
        zero_ask_order_id=int((t.ask_order_id == 0).sum()), zero_bid_order_id=int((t.bid_order_id == 0).sum()),
        flag_quantity_sums={str(k): int(v) for k, v in positive.groupby("bs_flag").trade_quantity.sum().items()})
    return result


def summarize_orders(o, expected):
    result = common_summary(o, expected)
    result.update(order_type_counts=frequencies(o.order_type), order_side_counts=frequencies(o.order_side),
        zero_or_negative_price_rows=int((o.order_price_x1e4 <= 0).sum()),
        zero_or_negative_quantity_rows=int((o.order_quantity <= 0).sum()),
        positive_quantity_not_multiple_100=int(((o.order_quantity > 0) & (o.order_quantity % 100 != 0)).sum()),
        duplicate_order_id=int(o.order_id.duplicated().sum()), duplicate_exchange_order_id=int(o.exchange_order_id.duplicated().sum()))
    return result


def cross_check(q, t, o, daily, summaries):
    positive = t[(t.trade_price_x1e4 > 0) & (t.trade_quantity > 0) & t.trade_code.fillna("").ne("C")].sort_values(["event_ts", "source_row_no"])
    quotes = q.sort_values(["event_ts", "source_row_no"])
    running = positive.groupby("event_ts").trade_quantity.sum().cumsum().rename("trade_cum_quantity").reset_index()
    aligned = pd.merge_asof(quotes[["event_ts", "cum_volume"]], running, on="event_ts", direction="backward")
    diff = aligned.cum_volume - aligned.trade_cum_quantity.fillna(0)
    end = summaries["quotes"]
    amount = summaries["trades"]["positive_price_times_quantity_div_1e4"]
    result = {"final_quote_volume_minus_positive_trade_quantity": end["final_cum_volume"] - summaries["trades"]["positive_quantity_sum"],
        "final_quote_amount_minus_price_quantity_div_1e4": float(end["final_cum_amount"] - amount),
        "final_quote_amount_to_price_quantity_ratio": ratio(end["final_cum_amount"], amount),
        "snapshot_volume_exact_at_or_before_ts_fraction": float((diff == 0).mean()),
        "snapshot_volume_abs_diff_max": float(diff.abs().max()),
        "snapshot_volume_abs_diff_median": float(diff.abs().median()),
        "daily_rows": len(daily)}
    if len(daily) == 1:
        row = daily.iloc[0]
        scales = {k: ratio(float(row["qfq_" + k]), v) for k, v in end["raw_daily_ohlc"].items()}
        usable = [x for x in scales.values() if x is not None]
        result.update(daily_volume=float(row.volume), l2_volume_to_daily_volume=ratio(end["final_cum_volume"], float(row.volume)),
            l2_volume_minus_daily_times_100=float(end["final_cum_volume"] - row.volume * 100),
            qfq_to_l2_raw_ohlc_ratios=scales, qfq_ratio_ohlc_spread=max(usable)-min(usable) if usable else None,
            daily_corporate_action_note=None if pd.isna(row.corporate_action_note) else str(row.corporate_action_note))
    # Direction checks are empirical consistency only, never an investor identity.
    both = positive[(positive.ask_order_id > 0) & (positive.bid_order_id > 0)]
    result["flag_vs_order_id_ordering"] = {str(flag): {
        "rows": len(group), "bid_id_larger_fraction": float((group.bid_order_id > group.ask_order_id).mean())}
        for flag, group in both.groupby("bs_flag")}
    previous = pd.merge_asof(positive[["event_ts", "bs_flag", "trade_price_x1e4"]],
        quotes[["event_ts", "ask_price_1_x1e4", "bid_price_1_x1e4"]], on="event_ts", direction="backward",
        allow_exact_matches=False, tolerance=pd.Timedelta(seconds=3))
    matched = previous[(previous.ask_price_1_x1e4 > 0) & (previous.bid_price_1_x1e4 > 0)]
    result["flag_vs_strict_prior_3s_quote"] = {str(flag): {"rows": len(group),
        "at_or_above_ask_fraction": float((group.trade_price_x1e4 >= group.ask_price_1_x1e4).mean()),
        "at_or_below_bid_fraction": float((group.trade_price_x1e4 <= group.bid_price_1_x1e4).mean())}
        for flag, group in matched.groupby("bs_flag")}
    result["order_linkage"] = {}
    for order_id in ["order_id", "exchange_order_id"]:
        unique = o[~o[order_id].duplicated(keep=False) & (o[order_id] > 0)].set_index(order_id).order_side
        for trade_id in ["ask_order_id", "bid_order_id"]:
            mapped = positive[trade_id].map(unique)
            result["order_linkage"][order_id + "<-" + trade_id] = {"positive_trade_rows": len(positive),
                "matched_unique_order_rows": int(mapped.notna().sum()), "mapped_order_side_counts": frequencies(mapped.dropna())}
    result["official_bs_semantics_verified"] = False
    result["main_player_identity_inference_allowed"] = False
    return result


def audit(out):
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    if jd({k: v for k, v in plan.items() if k != "plan_sha256"}) != plan["plan_sha256"]:
        raise ValueError("frozen plan hash mismatch")
    for item in plan["manifests"]:
        if digest(Path(item["path"]).read_bytes()) != item["sha256"]:
            raise ValueError("manifest changed after selection")
    # First pass reads only wind_code in candidate row groups. This is explicitly
    # charged, and avoids broad min/max ranges expanding the expensive projection.
    spent = 0
    read_plan = []
    for source in plan["sources"]:
        path = Path(source["path"])
        stat = path.stat()
        if stat.st_size != source["size"] or stat.st_mtime_ns != source["mtime_ns"]:
            raise ValueError("source file metadata changed after freezing")
        pf = pq.ParquetFile(path)
        hit = []
        needles = pa.array(source["codes"])
        for group in source["candidate_row_groups"]:
            cost = chunk_bytes(pf, group, ["wind_code"])
            if spent + cost > plan["payload_cap_bytes"]:
                raise ValueError("code-only prepass would exceed payload cap")
            values = pf.read_row_group(group, columns=["wind_code"]).column(0)
            spent += cost
            if pc.any(pc.is_in(values, value_set=needles)).as_py():
                hit.append(group)
        projected = sum(chunk_bytes(pf, i, plan["columns"][source["table"]]) for i in hit)
        read_plan.append({**source, "matched_row_groups": hit, "projected_compressed_bytes": projected})
    expected_cost = spent + sum(s["projected_compressed_bytes"] for s in read_plan) + plan["cost_before_read"]["daily_projection_compressed_bytes"]
    report = {"plan_sha256": plan["plan_sha256"], "code_prepass_compressed_bytes": spent,
              "total_projected_payload_compressed_bytes": expected_cost, "payload_cap_bytes": plan["payload_cap_bytes"],
              "sources": read_plan, "within_cap": expected_cost <= plan["payload_cap_bytes"]}
    save(out / "read_plan.json", report)
    print(json.dumps({k: report[k] for k in report if k != "sources"}), flush=True)
    if not report["within_cap"]:
        raise ValueError("selected projected payload exceeds cap; no wide projection was read; freeze a smaller-date plan explicitly")
    frames = {}
    for source in read_plan:
        pf = pq.ParquetFile(source["path"])
        columns = plan["columns"][source["table"]]
        for group in source["matched_row_groups"]:
            table = pf.read_row_group(group, columns=columns)
            table = table.filter(pc.is_in(table["wind_code"], value_set=pa.array(source["codes"])))
            if table.num_rows:
                frames.setdefault((source["date"], source["table"]), []).append(table.to_pandas())
    frame_map = {key: pd.concat(parts, ignore_index=True) for key, parts in frames.items()}
    ds = plan["daily"]
    if Path(ds["path"]).stat().st_size != ds["size"] or Path(ds["path"]).stat().st_mtime_ns != ds["mtime_ns"]:
        raise ValueError("daily file metadata changed")
    daily_codes = [c[-2:].lower() + c[:6] for c in plan["codes"]]
    date_type = pq.ParquetFile(ds["path"]).schema_arrow.field("date").type
    dates = ([pd.Timestamp(d).date() for d in plan["dates"]] if pa.types.is_date(date_type) else
             [pd.Timestamp(d).to_pydatetime() for d in plan["dates"]] if pa.types.is_timestamp(date_type) else plan["dates"])
    daily = pq.read_table(ds["path"], columns=ds["columns"],
                          filters=[("code", "in", daily_codes), ("date", "in", dates)]).to_pandas()
    daily["date"] = pd.to_datetime(daily.date).dt.strftime("%Y-%m-%d")
    daily = daily[daily.date.isin(plan["dates"])]
    results = []
    functions = {"quotes": summarize_quotes, "trades": summarize_trades, "orders": summarize_orders}
    for date in plan["dates"]:
        for code in plan["codes"]:
            tables, summaries = {}, {}
            for table in COLUMNS:
                tables[table] = frame_map[(date, table)].loc[lambda d: d.wind_code == code].copy()
                expected = next(s["expected_selected_rows"][code] for s in read_plan if s["date"] == date and s["table"] == table and code in s["codes"])
                if not len(tables[table]):
                    summaries[table] = {"rows": 0, "manifest_expected_rows": expected, "rows_match_manifest": expected == 0}
                else:
                    summaries[table] = functions[table](tables[table], expected)
            d = daily[(daily.date == date) & (daily.code == code[-2:].lower() + code[:6])]
            cross = cross_check(tables["quotes"], tables["trades"], tables["orders"], d, summaries) if all(len(x) for x in tables.values()) else {"missing_table": True}
            results.append({"date": date, "wind_code": code, "tables": summaries, "cross": cross})
    outcome = {"plan_sha256": plan["plan_sha256"], "codes": plan["codes"], "dates": plan["dates"],
        "read_cost": {k: report[k] for k in report if k != "sources"}, "sample_rows": sum(len(f) for f in frame_map.values()),
        "sample_by_table": {t: sum(len(f) for (d, name), f in frame_map.items() if name == t) for t in COLUMNS},
        "stock_days": results, "price_scale_contract": "integer _x1e4 / 10000, empirical consistency only; local vendor specification absent",
        "official_supplier_schema_verified": False, "t_plus_one_execution_ready": False,
        "formal_target_success": False, "promotion": False, "no_returns_computed": True,
        "limitations": ["Eight stocks across three days are engineering samples, not alpha evidence or full-market coverage.",
            "Manifest full-file hashes are recorded, not recomputed; selected projection hashes are newly computed.",
            "Timezone/receipt latency and exchange sequence channels are not documented; source_row_no is not a global feed sequence.",
            "B/S does not identify institutions, accumulation, or a main player.",
            "Daily QFQ is not raw execution price; corporate actions require separate entitlement, cash and tradability dates.",
            "No authoritative historical ST, suspension, price limits or per-account available-share ledger is supplied here."]}
    save(out / "result.json", outcome)
    print(json.dumps({k: outcome[k] for k in ["sample_rows", "sample_by_table", "t_plus_one_execution_ready", "no_returns_computed"]}), flush=True)
    return outcome


def supplement(out):
    """Read a few already selected stock-days to explain detected contract issues."""
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    reads = json.loads((out / "read_plan.json").read_text(encoding="utf-8"))
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    if jd({k: v for k, v in plan.items() if k != "plan_sha256"}) != plan["plan_sha256"]:
        raise ValueError("plan hash mismatch")
    if any(x["plan_sha256"] != plan["plan_sha256"] for x in [reads, result]):
        raise ValueError("outputs belong to different plans")
    for source in plan["manifests"]:
        if digest(Path(source["path"]).read_bytes()) != source["sha256"]:
            raise ValueError("manifest changed")
    first_day, first_sh, first_sz = plan["dates"][0], plan["codes"][0], plan["codes"][4]
    targets = {(first_day, first_sh, "orders"), (first_day, first_sz, "orders"), (first_day, first_sz, "trades")}
    targets.update((x["date"], x["wind_code"], "orders") for x in result["stock_days"]
                   if x["tables"]["orders"].get("source_order_event_ts_backsteps", 0))
    entries, total = [], 0
    for date, code, table in sorted(targets):
        source = next(s for s in reads["sources"] if s["date"] == date and s["table"] == table and code in s["codes"])
        path = Path(source["path"])
        if path.stat().st_size != source["size"] or path.stat().st_mtime_ns != source["mtime_ns"]:
            raise ValueError("source changed")
        pf = pq.ParquetFile(path)
        columns = COLUMNS[table]
        cost = sum(chunk_bytes(pf, group, columns) for group in source["matched_row_groups"])
        total += cost
        if reads["total_projected_payload_compressed_bytes"] + total > plan["payload_cap_bytes"]:
            raise ValueError("combined audit/supplement payload cap exceeded")
        data = pf.read_row_groups(source["matched_row_groups"], columns=columns)
        data = data.filter(pc.equal(data["wind_code"], code)).to_pandas().sort_values("source_row_no")
        if table == "orders":
            backwards = data.event_ts.diff() < pd.Timedelta(0)
            examples = pd.concat([data.groupby(["order_type", "order_side"], dropna=False).head(2),
                                  data[backwards | backwards.shift(-1, fill_value=False)]])
            stats = {"order_id_min": int(data.order_id.min()), "order_id_max": int(data.order_id.max()),
                "type_side_counts": [{"order_type": str(k[0]), "order_side": str(k[1]), "rows": int(v)}
                                      for k, v in data.groupby(["order_type", "order_side"]).size().items()],
                "backstep_pairs": json.loads(data[backwards | backwards.shift(-1, fill_value=False)].to_json(orient="records", date_format="iso"))}
        else:
            examples = data.groupby(["trade_code", "bs_flag"], dropna=False).head(3)
            stats = {"trade_code_groups": [{"trade_code": str(k), "rows": len(g),
                 "positive_price_rows": int((g.trade_price_x1e4 > 0).sum()),
                 "positive_quantity_rows": int((g.trade_quantity > 0).sum()),
                 "zero_ask_id_rows": int((g.ask_order_id == 0).sum()), "zero_bid_id_rows": int((g.bid_order_id == 0).sum())}
                 for k, g in data.groupby("trade_code", dropna=False)]}
        entries.append({"date": date, "code": code, "table": table, "rows_checked": len(data),
                        "projected_compressed_bytes": cost, **stats,
                        "examples": json.loads(examples.drop_duplicates().to_json(orient="records", date_format="iso"))})
    output = {"plan_sha256": plan["plan_sha256"], "purpose": "inspect first SH/SZ examples and the already-detected source timestamp reversal; no re-selection by return",
              "additional_projected_compressed_bytes": total, "entries": entries,
              "script_sha256": digest(Path(__file__).read_bytes()), "pandas_version": pd.__version__, "pyarrow_version": pa.__version__}
    save(out / "semantic_samples.json", output)
    save(out / "adapter_contract_draft.json", contract_draft(result))
    print(json.dumps({"additional_projected_compressed_bytes": total, "sample_tables": len(entries)}), flush=True)
    return output


def contract_draft(result):
    """Proposed next adapter contract; no claim that an adapter was implemented."""
    stock_days = result["stock_days"]
    return {"status": "draft_for_next_adapter", "plan_sha256": result["plan_sha256"],
        "scope": "EOD features used no earlier than the next trading day; not an execution simulator",
        "sample_stock_days": len(stock_days),
        "observed_checks": {
            "all_72_table_counts_match_manifest": all(v["rows_match_manifest"] for x in stock_days for v in x["tables"].values()),
            "all_24_final_volume_matches": all(x["cross"]["final_quote_volume_minus_positive_trade_quantity"] == 0 for x in stock_days),
            "max_abs_end_amount_difference": max(abs(x["cross"]["final_quote_amount_minus_price_quantity_div_1e4"]) for x in stock_days),
            "max_abs_daily_volume_times_100_difference": max(abs(x["cross"]["l2_volume_minus_daily_times_100"]) for x in stock_days)},
        "valid_trade_filter": "price_x1e4 > 0 AND quantity > 0 AND (trade_code IS NULL OR trade_code != 'C'); unknown non-null codes require quarantine before the universe expands",
        "trade_code_allowlists_observed": {"SH": [None], "SZ": ["0"]},
        "excluded_record_classes": ["SZ trade_code=C", "non-positive trade price or quantity", "all order-derived features pending source event-type mapping"],
        "sorting": "Within each (table, date, wind_code), stable sort(event_ts, source_row_no). This is deterministic replay order, not a cross-table causal sequence. Do not deduplicate by timestamp.",
        "schema": {
            "wind_code": "string", "trade_date": "date", "last_event_ts": "timestamp[us], source local wall clock",
            "market_timezone_assumption": "Asia/Shanghai; supplier confirmation absent", "available_at": "nullable timestamp; never manufacture feed receipt/availability time",
            "trade_quantity_native": "int64; empirically stock-count-like, formal supplier unit unverified",
            "trade_amount_price_x_quantity": "float64: sum(price_x1e4 * quantity)/10000; candidate yuan amount",
            "trade_vwap_price": "float64: sum(price_x1e4 * quantity)/sum(quantity)/10000",
            "quote_median_spread_bps": "nullable float64, valid two-sided continuous-session quotes only",
            "quote_depth_imbalance_l1": "nullable float64: (bid_volume_1-ask_volume_1)/(bid_volume_1+ask_volume_1); proposed time-weighted aggregation, no carry over lunch/auction/gaps",
            "vendor_bs_flag": "optional raw record field, retained for audit only",
            "aggressor_direction": "null until source mapping and applicability verified; null is not zero/neutral",
            "active_buy_amount": "null until direction contract passes", "order_imbalance_or_cancel_rate": "null until order event classes and reconstruction contract pass",
            "quality": "schema/version, manifest hash, projected-data hash, expected/observed rows, duplicate keys, bad clocks, phase counts, coverage gaps, end-volume and amount discrepancy",
            "feature_status": "accepted_provisional / rejected / unavailable with reason_codes"},
        "proposed_gates": [
            "Complete date manifest and each requested stock/table count; retain missing stock-days as rejected, never silently delete them.",
            "Required identity/price/quantity fields present; source row identity unique; raw clock/date consistent; no unknown record class enters a feature.",
            "EOD positive genuine trades match final cumulative quote quantity exactly, amount within one candidate-yuan rounding unit; document unit assumptions.",
            "For quote-time features predeclare valid continuous-session coverage and maximum carry time. Current audit gives observed gap maxima, not a validated time-coverage percentage.",
            "Keep auctions, continuous trading and post-15:00 records separate; do not discard the latter before checking whether closing prints explain EOD totals.",
            "Null direction/order features stay unavailable. Never replace null with zero or infer investor identity from B/S.",
            "Trade no earlier than t+1; shares bought on t+1 are not sellable until at least t+2. Apply actual trading calendar and per-lot available shares.",
            "Execution gate remains closed until raw share/cash corporate-action accounting, ST/suspension/price-limit data, availability timing and executable capacity are verified."],
        "execution_ready": False, "formal_target_success": False, "promotion": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "audit", "supplement"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dates", nargs="+", default=DATES)
    parser.add_argument("--max-payload-mib", type=int, default=1536)
    args = parser.parse_args()
    if args.command == "plan":
        plan = create_plan(args.root, args.output_dir, args.dates, args.max_payload_mib * 1024 * 1024)
        print(json.dumps({"codes": plan["codes"], "cost_before_read": plan["cost_before_read"], "plan_sha256": plan["plan_sha256"]}), flush=True)
    elif args.command == "audit":
        audit(args.output_dir)
    else:
        supplement(args.output_dir)


if __name__ == "__main__":
    main()
