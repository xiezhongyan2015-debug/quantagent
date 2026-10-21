"""Independent, once-only fixed-source H24 reconstruction; default is contract.

No legacy source module is imported. All 41 original payloads are decoded once,
then counterfactuals clone these real arrays. Only the fixed issuer's retained
raw slices are read. This does not run normalization, a model, IC, or training.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import resource
import stat as stat_module
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "gru_research/trading_data/fixed_000009_h24_reconstruction_preparation_v1_20261002"
CONTRACT = PREP / "research_contract_v1.json"
CONTRACT_SHA = "d2243d2cf73738ed66fb40cb0e72b7f5e1d713c525a56bb859d4c3a74fe7ab40"
ACQUISITION_SHA = "f23e6d5ec07abc50251b31195b8b13da1ae21ddeffdec7698e5c4ee2e835255f"
NSPACE = ROOT / "gru_research/trading_data/fixed_000009_original_h24_reconstruction_v1_20261002"
FIELDS = ("股票代码", "datetime", "datetime_nano", "open", "high", "low", "close", "volume",
          "qfq_ratio", "qfq_open", "qfq_high", "qfq_low", "qfq_close")
FUTURE_CASES = ("future_valid_price_volume", "future_conflicting_duplicate", "future_dual_clock_mismatch",
                "future_backdated_clock", "future_factor_change", "future_wrong_symbol")
OFFSET_SLOTS = (630, 690, 840, 900)
PASS_STATUS = "PASS_FIXED000009_ORIGINAL_H24_80_EXACT_QUALIFIED"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def identity(path, cap=4 * 1024**2):
    path = Path(path)
    s = path.lstat()
    require(stat_module.S_ISREG(s.st_mode) and s.st_size <= cap, "missing/unsafe/oversize identity: " + str(path))
    data = path.read_bytes()
    require(len(data) == s.st_size, "file changed while reading identity")
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def stat(path):
    path = Path(path)
    s = path.lstat()
    require(stat_module.S_ISREG(s.st_mode), "only regular paths are allowed: " + str(path))
    return {"path": str(path), "st_size": s.st_size, "st_mtime_ns": s.st_mtime_ns,
            "st_dev": s.st_dev, "st_ino": s.st_ino, "st_mode": s.st_mode}


def write_json(path, value):
    body = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    require(len(body) <= 4 * 1024**2, "new JSON evidence exceeds4MiB budget")
    with path.open("xb") as handle:
        handle.write(body)
    return {"path": str(path), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def midnight_ns(day):
    return int(datetime.fromisoformat(day + "T00:00:00+08:00").timestamp()) * 10**9


def pure_contract():
    return {"schema_version": 1, "status": "PREPARED_ONLY_NOT_EXECUTED", "contract": str(CONTRACT),
            "contract_sha256": CONTRACT_SHA, "acquisition_receipt_sha256": ACQUISITION_SHA,
            "fixed_symbol": "000009.SZ", "symbol_universe_index": 2,
            "source_full_files": 41, "source_full_file_hours": [6100, 6263],
            "needed_hour_span": [6101, 6260], "target_hour_span": [6181, 6260],
            "planned_values": {"name": "H24_raw_80_24.npy", "shape": [80, 24], "dtype": "<f4"},
            "default_market_body_reads": 0, "once_output_namespace": str(NSPACE),
            "actual_requires": ["--execute", "--source-sha", "--contract-sha", "--acquisition-receipt-sha"],
            "original_all_day_gate_kept_and_future_counterexample_not_hidden": True,
            "full_end_to_end_causality_proven": False, "full_PLAN_complete": False}


def array_body_sha(array):
    return hashlib.sha256(array.tobytes(order="C")).hexdigest()


def save_array(output, name, array, role, artifacts):
    path = output / name
    with path.open("xb") as handle:
        np.save(handle, array, allow_pickle=False)
    item = {"role": role, **identity(path), "dtype": array.dtype.str,
            "shape": list(array.shape), "body_sha256": array_body_sha(array)}
    artifacts.append(item)
    return item


def numeric_quality(frame, day):
    """Original all-file quality semantics, written here independently."""
    if not np.all(np.asarray(frame["股票代码"]).astype(str) == "000009"):
        return None, {"file_error": "source_symbol_mismatch", "whole_bad": True}
    ns = frame["datetime_nano"].astype(np.int64)
    local = frame["datetime"].astype("datetime64[ns]").astype(np.int64)
    mismatched_clock = int(np.count_nonzero(local - 8 * 3600 * 10**9 != ns))
    order = np.argsort(ns, kind="stable")
    a = {k: frame[k][order] for k in FIELDS}
    ns = ns[order]
    unique = np.r_[True, np.diff(ns) != 0] if len(ns) else np.empty(0, bool)
    conflicts = 0
    for j in np.flatnonzero(~unique):
        conflicts += any(a[k][j] != a[k][j - 1] for k in FIELDS if k != "datetime")
    a = {k: v[unique] for k, v in a.items()}
    ns = ns[unique]
    raw = np.column_stack([a[k] for k in ("open", "high", "low", "close")]).astype(float)
    adjusted = np.column_stack([a[k] for k in ("qfq_open", "qfq_high", "qfq_low", "qfq_close")]).astype(float)
    volume = a["volume"].astype(float)
    factor = a["qfq_ratio"].astype(float)
    valid = (np.isfinite(raw).all(1) & np.isfinite(adjusted).all(1) & (raw > 0).all(1)
             & (adjusted > 0).all(1) & np.isfinite(volume) & (volume >= 0)
             & np.isfinite(factor) & (factor > 0)
             & (raw[:, 1] >= raw[:, [0, 2, 3]].max(1) - 1e-8)
             & (raw[:, 2] <= raw[:, [0, 1, 3]].min(1) + 1e-8))
    factor_relation_bad = ~np.isclose(adjusted, raw * factor[:, None], rtol=1e-7, atol=1e-6).all(1)
    valid &= ~factor_relation_bad
    changed_factor = bool(len(factor) and not np.allclose(factor, factor[0], rtol=1e-7, atol=1e-10))
    offsets = (ns - midnight_ns(day)) // (60 * 10**9)
    regular = np.r_[np.arange(570, 690), np.arange(780, 900)]
    report = {"file_error": "intraday_adjustment_factor_change" if changed_factor else "",
              "physical_input_rows": len(frame["datetime_nano"]), "duplicate_rows": int((~unique).sum()),
              "duplicate_conflicts": int(conflicts), "timestamp_mismatch_rows": mismatched_clock,
              "qfq_mismatch_rows": int(factor_relation_bad.sum()), "invalid_rows": int((~valid).sum()),
              "outside_session_rows": int((~np.isin(offsets, regular)).sum()),
              "whole_bad": bool(mismatched_clock or conflicts or changed_factor)}
    return (offsets, adjusted, volume, valid), report


def hour_values(state, report, slot):
    empty = np.full(15, np.nan, dtype=np.float64)
    if state is None:
        return empty, False
    offsets, adjusted, volume, valid = state
    expected = np.r_[np.arange(570, 690), np.arange(780, 900)][slot * 60:(slot + 1) * 60]
    pos = np.flatnonzero(np.isin(offsets, expected))
    complete = (len(pos) == 60 and np.array_equal(offsets[pos], expected) and valid[pos].all()
                and not report["whole_bad"] and volume[pos].sum() > 0)
    if not complete:
        return empty, False
    o = adjusted[pos[0], 0]
    h, l, c = adjusted[pos, 1].max(), adjusted[pos, 2].min(), adjusted[pos[-1], 3]
    v = volume[pos]
    returns = np.log(adjusted[pos, 3] / np.r_[o, adjusted[pos[:-1], 3]])
    square_sum = float(np.square(returns).sum())
    std = returns.std()
    log_volume = np.log1p(v)
    path = float(np.abs(np.diff(np.r_[o, adjusted[pos, 3]])).sum())
    def ratio(numerator, denominator):
        return float(numerator / denominator) if np.isfinite(denominator) and denominator != 0 else np.nan
    stats = [np.sqrt(square_sum), float(np.mean(((returns - returns.mean()) / std)**3)) if std > 1e-12 else np.nan,
             returns.max(), returns.min(), ratio(np.square(returns[returns > 0]).sum(), square_sum),
             float(np.corrcoef(returns, log_volume)[0, 1]) if std > 1e-12 and log_volume.std() > 1e-12 else np.nan,
             ratio(v[-15:].sum(), v.sum()), float(np.mean(v == 0)), ratio(abs(c - o), path),
             ratio(v[returns > 0].sum(), v.sum())]
    return np.asarray([o, h, l, c, v.sum(), *stats], dtype=np.float64), True


def reconstruct(frames, days, names, local_prefix):
    values = np.full((164, 15), np.nan, dtype=np.float64)
    complete = np.zeros(164, dtype=bool)
    reports = []
    for j, day in enumerate(days):
        source = frames[day]
        full_state, full_report = numeric_quality(source, day)
        reports.append({"date": day, **full_report})
        for slot in range(4):
            global_hour = 6100 + 4 * j + slot
            # 6100 and6261..6263 only participate in full-file quality. Their
            # OHLC/volume never enter a target feature's rolling numerical path.
            if not 6101 <= global_hour <= 6260:
                continue
            state, report = full_state, full_report
            if local_prefix:
                cut = midnight_ns(day) + OFFSET_SLOTS[slot] * 60 * 10**9
                include = source["_original_completion_ns"] <= cut
                selected = {k: v[include] for k, v in source.items()}
                state, report = numeric_quality(selected, day)
            values[4 * j + slot], complete[4 * j + slot] = hour_values(state, report, slot)
    f = pd.DataFrame(values[:, :5], columns=["open", "high", "low", "close", "volume"])
    f["slot"] = np.tile(np.arange(4), 41)
    o, h, l, c, v = [f[k].where(complete) for k in ("open", "high", "low", "close", "volume")]
    out = pd.DataFrame(index=f.index)
    out[names[0]] = np.log(c / c.shift(1))
    out[names[1]] = np.log(o / c.shift(1))
    out[names[2]] = np.log(c / o)
    out[names[3]] = (h - l) / o
    spread = (h - l).replace(0, np.nan)
    out[names[4]] = (h - pd.concat([o, c], axis=1).max(axis=1)) / spread
    out[names[5]] = (pd.concat([o, c], axis=1).min(axis=1) - l) / spread
    out[names[6]] = (c - l) / spread
    prior_same_slot = v.groupby(f["slot"]).transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    out[names[7]] = v / prior_same_slot.replace(0, np.nan)
    out[names[8]] = v / v.shift(1).replace(0, np.nan)
    for i, length in enumerate((4, 20, 80), 9):
        out[names[i]] = c / c.rolling(length, min_periods=length).mean() - 1
    for i, length in enumerate((4, 20), 12):
        out[names[i]] = np.log(c / c.shift(length))
    for i in range(14, 24):
        out[names[i]] = values[:, i - 9]
    matrix = np.ascontiguousarray(out[names].replace([np.inf, -np.inf], np.nan).astype(np.float32).to_numpy(), dtype="<f4")
    eligible = pd.Series(complete.astype(np.int16)).rolling(80, min_periods=80).sum().eq(80).to_numpy(bool)
    return {"values": matrix[81:161].copy(), "hour_complete": complete[81:161].copy(),
            "dependency160": complete[1:161].copy(), "window80_complete": eligible[81:161].copy(), "source_quality": reports}


def same_outputs(left, right):
    return {"nan_masks_equal": bool(np.array_equal(np.isnan(left["values"]), np.isnan(right["values"]))),
            "all_80x24_body_bytes_equal": array_body_sha(left["values"]) == array_body_sha(right["values"]),
            "finite_FP32_bits_equal": bool(np.array_equal(left["values"].view("<u4")[np.isfinite(left["values"])],
                                                         right["values"].view("<u4")[np.isfinite(left["values"])])),
            "hour_complete_80_equal": bool(np.array_equal(left["hour_complete"], right["hour_complete"])),
            "dependency160_hour_complete_equal": bool(np.array_equal(left["dependency160"], right["dependency160"])),
            "window80_complete_80_equal": bool(np.array_equal(left["window80_complete"], right["window80_complete"]))}


def mutate_future(original, name, cutoff):
    changed = {k: v.copy() for k, v in original.items()}
    future = changed["_original_completion_ns"] > cutoff
    require(int(future.sum()) == 180, "fixed last day future physical minute count differs")
    j = int(np.flatnonzero(future)[-1])
    if name == "future_valid_price_volume":
        for k in ("open", "high", "low", "close", "qfq_open", "qfq_high", "qfq_low", "qfq_close"):
            changed[k][future] *= 1.25
        changed["volume"][future] *= 2
    elif name == "future_conflicting_duplicate":
        changed = {k: np.concatenate([v, v[j:j + 1]]) for k, v in changed.items()}
        changed["volume"][-1] += 1
    elif name == "future_dual_clock_mismatch":
        later = changed["datetime"].astype("datetime64[ns]")[j] + np.timedelta64(1, "m")
        changed["datetime"][j] = later if changed["datetime"].dtype.kind == "M" else str(later)
    elif name == "future_backdated_clock":
        backdated = np.datetime64("2024-06-18T09:30:00", "ns")
        changed["datetime"][j] = backdated if changed["datetime"].dtype.kind == "M" else str(backdated)
        changed["datetime_nano"][j] = midnight_ns("2024-06-18") + 570 * 60 * 10**9
    elif name == "future_factor_change":
        changed["qfq_ratio"][j] *= 1.005
        for k in ("qfq_open", "qfq_high", "qfq_low", "qfq_close"):
            changed[k][j] *= 1.005
    elif name == "future_wrong_symbol":
        changed["股票代码"] = changed["股票代码"].astype(str)
        changed["股票代码"][j] = "000010"
    else:
        raise ValueError("unregistered counterfactual")
    past = original["_original_completion_ns"] <= cutoff
    for k in FIELDS:
        require(np.array_equal(original[k][past].astype(str), changed[k][:len(past)][past].astype(str)), "counterfactual changed an original past row")
    require(any(not np.array_equal(original[k].astype(str), changed[k].astype(str)) for k in FIELDS), "counterfactual injection was empty")
    return changed, {"case": name, "original_future_rows": 180, "only_original_completion_after_cut_modified": True,
                     "immutable_completion_witness_retained": True, "claimed_clock_is_feed_arrival": False,
                     "original_past13_fields_exact_unchanged": True, "input_rows_after_injection": len(changed["datetime_nano"])}


def actual_once(args):
    global np, pd
    source_id = identity(Path(__file__).resolve())
    require(args.source_sha == source_id["sha256"] and args.contract_sha == CONTRACT_SHA and
            args.acquisition_receipt_sha == ACQUISITION_SHA, "actual mode needs exact out-of-band source/contract/acquisition SHA pins")
    NSPACE.mkdir(exist_ok=False)
    marker = write_json(NSPACE / "EXECUTION_STARTED_v1.json", {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
                         "source": source_id, "contract_sha256": CONTRACT_SHA, "automatic_retry": False})
    output = NSPACE / "attempt1"
    output.mkdir(exist_ok=False)
    started = time.monotonic()
    artifacts, small_before, payload_before, dense_before = [], {}, {}, {}
    failure = None
    result = {"schema_version": 1, "status": "RUNNING_FIXED000009_H24_RECONSTRUCTION", "source": source_id,
              "execution_marker": marker, "fixed_symbol": "000009.SZ", "symbol_universe_index": 2,
              "fixed_time_index": 6260, "window_start_index": 6181, "window_end_index": 6260, "window_length": 80,
              "automatic_retry": False, "new_training_fits": 0, "full_PLAN_complete": False,
              "full_end_to_end_causality_proven": False, "economic_comparison_eligible": False,
              "original_sources_models_R2_or_results_modified": False}
    try:
        require(identity(CONTRACT)["sha256"] == CONTRACT_SHA, "sealed reconstruction contract changed")
        contract = json.loads(CONTRACT.read_bytes())
        expected_small = dict(contract["small_identity_guards"])
        expected_small[str(CONTRACT)] = identity(CONTRACT)
        small_before = {p: identity(p) for p in expected_small}
        require(small_before == expected_small, "closed small input SHA guard changed")
        acq_id = contract["acquisition_receipt"]
        acq = json.loads(Path(acq_id["path"]).read_bytes())
        require(acq_id["sha256"] == ACQUISITION_SHA and acq["status"] == "PASS_FIXED41_ORIGINAL_PAYLOAD_ACQUISITION_ONLY_VALIDATION_PENDING" and
                acq["source_STAT_and_small_SHA_before_after_unchanged"] and acq["actual_targets_fully_saved"] == 41 and not acq["missing_exact_targets"], "acquisition is not complete and closed")
        keys = ("date", "symbol", "actual_header_name", "actual_tar_header_offset", "actual_tar_data_offset", "declared_payload_bytes", "payload", "payload_STAT_after_save")
        roster = [{k: v[k] for k in keys} for v in acq["targets_in_fixed_date_order"]]
        roster_bytes = json.dumps(roster, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        require(hashlib.sha256(roster_bytes).hexdigest() == contract["source_roster_canonical_sha256"], "exact41 source roster changed")
        dense_before = {k: stat(v["path"]) for k, v in contract["retained_large_raw_STAT_only"].items()}
        require(dense_before == contract["retained_large_raw_STAT_only"], "retained dense source STAT changed")
        payload_before = {v["payload"]["path"]: stat(v["payload"]["path"]) for v in roster}
        require(payload_before == {v["payload"]["path"]: v["payload_STAT_after_save"] for v in roster}, "saved original payload STAT changed")
        sys.path.insert(0, str(ROOT / "gru_research/.deps"))
        import numpy as np
        import pandas as pd
        import pyarrow.parquet as pq
        names = contract["original_24_feature_names"]
        meta = json.loads((ROOT / "gru_research/artifacts/minute_feature_metadata.json").read_bytes())
        calendar = json.loads((ROOT / "gru_research/artifacts/minute_calendar.json").read_bytes())["dates"]
        symbols = json.loads((ROOT / "gru_research/artifacts/dataset/symbols.json").read_bytes())
        require(len(names) == 24 and names == meta["hour_features"] and symbols[2] == "000009.SZ", "hour order or fixed universe index differs")
        days = calendar[1525:1566]
        require(len(days) == 41 and days == [v["date"] for v in roster], "fixed41 chronological calendar differs")
        times = np.asarray([midnight_ns(calendar[i // 4]) + OFFSET_SLOTS[i % 4] * 60 * 10**9 for i in range(6181, 6261)], dtype="<i8")
        require(int(times[-1]) == 1718677800000000000, "fixed signal time differs")
        frames, schemas, bytes_read = {}, {}, 0
        for row in roster:
            require(time.monotonic() - started <= 300, "numeric runtime budget exceeded")
            require(row["symbol"] == "000009.SZ" and row["actual_header_name"] == "A股分钟k线数据/" + row["date"].replace("-", "") + "/000009.parquet", "target symbol/header differs")
            p = Path(row["payload"]["path"])
            require(p.parent == ROOT / "gru_research/trading_data/fixed_000009_original_minute_acquisition_v1_20261002/attempt1/targets" and
                    0 < row["payload"]["bytes"] <= 2 * 1024**2, "unexpected payload path/size")
            body = p.read_bytes()  # The only body read for this original payload.
            bytes_read += len(body)
            require(bytes_read <= 16 * 1024**2 and len(body) == row["payload"]["bytes"] and hashlib.sha256(body).hexdigest() == row["payload"]["sha256"], "original payload bytes/SHA differs")
            table = pq.read_table(io.BytesIO(body), columns=list(FIELDS), use_threads=False)
            require(table.column_names == list(FIELDS) and len(table) == 240, "original13-field/240-row schema differs; no adaptive source repair")
            a = {k: table[k].to_numpy(zero_copy_only=False).copy() for k in FIELDS}
            require(a["datetime_nano"].dtype.kind in "iu", "original UTC nano clock is not integer")
            ns = a["datetime_nano"].astype(np.int64)
            local = a["datetime"].astype("datetime64[ns]").astype(np.int64)
            expected_ns = midnight_ns(row["date"]) + np.r_[np.arange(570, 690), np.arange(780, 900)] * 60 * 10**9
            require(np.array_equal(np.sort(ns), expected_ns) and np.array_equal(local - 8 * 3600 * 10**9, ns) and
                    np.all(a["股票代码"].astype(str) == "000009"), "original exact240 clock/symbol differs; fail instead of filling/dropping")
            a["_original_completion_ns"] = ns + 60 * 10**9
            frames[row["date"]] = a
            schemas[row["date"]] = str(table.schema)
            del table, body
        require(bytes_read == 652369, "fixed original payload total differs")
        result.update({"source_receipt": acq_id, "original_payload_identities": roster,
                       "actual_payload_read_pass_count_each": 1, "actual_Parquet_numeric_decodes": 41,
                       "actual_payload_bytes_read": bytes_read, "actual_required_field_rows": 9840,
                       "actual_original_schemas": schemas, "feature_names": names,
                       "historical_row_clock": "Original dual clock exactly verified; immutable left+1minute completion, supplier arrival not observed",
                       "qfq_version_availability": "UNKNOWN", "volume_unit": "Inherited original minute/daily shares crosscheck, no new-provider claim"})
        legacy = reconstruct(frames, days, names, local_prefix=False)
        local = reconstruct(frames, days, names, local_prefix=True)
        retained = {}
        shape = (8476, 1178)
        for key, expected_shape, expected_dtype in (("raw_h24", (*shape, 24), np.dtype("<f4")),
                                                  ("hour_complete", shape, np.dtype(bool)), ("window80_complete", shape, np.dtype(bool))):
            mapped = np.load(dense_before[key]["path"], mmap_mode="r", allow_pickle=False)
            require(mapped.shape == expected_shape and mapped.dtype == expected_dtype, "retained dense schema differs")
            if key == "raw_h24":
                retained["values"] = np.ascontiguousarray(mapped[6181:6261, 2, :], dtype="<f4")
            elif key == "hour_complete":
                retained["hour_complete"] = np.ascontiguousarray(mapped[6181:6261, 2], dtype=bool)
                retained["dependency160"] = np.ascontiguousarray(mapped[6101:6261, 2], dtype=bool)
            else:
                retained["window80_complete"] = np.ascontiguousarray(mapped[6181:6261, 2], dtype=bool)
            del mapped
        comparison = same_outputs(legacy, retained)
        result["raw_parity"] = {k: comparison[k] for k in ("nan_masks_equal", "all_80x24_body_bytes_equal", "finite_FP32_bits_equal")}
        result["complete_parity"] = {k: comparison[k] for k in ("hour_complete_80_equal", "dependency160_hour_complete_equal", "window80_complete_80_equal")}
        result["raw_comparison_body_sha256"] = {"reconstructed": array_body_sha(legacy["values"]), "retained_raw": array_body_sha(retained["values"])}
        result["baseline_source_quality"] = legacy["source_quality"]
        result["baseline_local_prefix_comparison"] = same_outputs(local, legacy)
        row_proofs = [{"time_index": i, "time_utc_ns": int(times[i - 6181]), "date": calendar[i // 4], "slot": i % 4,
                       "reconstructed_row_body_sha256": array_body_sha(legacy["values"][i - 6181]),
                       "retained_raw_row_body_sha256": array_body_sha(retained["values"][i - 6181]),
                       "hour_complete": bool(legacy["hour_complete"][i - 6181]),
                       "window80_complete": bool(legacy["window80_complete"][i - 6181])} for i in range(6181, 6261)]
        artifacts.append({"role": "row_proofs", **write_json(output / "actual_80_row_raw_parity_proofs_v1.json", row_proofs)})
        save_array(output, "H24_raw_80_24.npy", legacy["values"], "raw_h24_values", artifacts)
        save_array(output, "times_ns.npy", times, "times_ns", artifacts)
        save_array(output, "hour_complete_80.npy", legacy["hour_complete"], "hour_complete", artifacts)
        save_array(output, "window80_complete_80.npy", legacy["window80_complete"], "window80_complete", artifacts)
        artifacts.append({"role": "feature_names", **write_json(output / "feature_names.json", names)})
        result["first_parity_mismatch_uint32_coordinates"] = np.argwhere(legacy["values"].view("<u4") != retained["values"].view("<u4"))[:16].tolist()
        require(all(comparison.values()) and all(result["baseline_local_prefix_comparison"].values()), "strict baseline raw/NaN/bits/completeness mismatch; preserve evidence, no tolerance relaxation")
        cases = []
        for name in FUTURE_CASES:
            require(time.monotonic() - started <= 300, "numeric runtime budget exceeded during counterfactuals")
            changed, injection = mutate_future(frames["2024-06-18"], name, int(times[-1]))
            cloned = dict(frames)
            cloned["2024-06-18"] = changed
            old_result = reconstruct(cloned, days, names, local_prefix=False)
            new_result = reconstruct(cloned, days, names, local_prefix=True)
            old_comparison = same_outputs(old_result, legacy)
            new_comparison = same_outputs(new_result, local)
            old_artifact = save_array(output, name + "_legacy_H24.npy", old_result["values"], name + "_legacy_values", artifacts)
            new_artifact = save_array(output, name + "_local_prefix_H24.npy", new_result["values"], name + "_local_prefix_values", artifacts)
            cases.append({**injection, "legacy_comparison_to_unmodified_real_baseline": old_comparison,
                          "local_prefix_comparison_to_unmodified_real_baseline": new_comparison,
                          "legacy_last_day_quality": old_result["source_quality"][-1],
                          "legacy_values": old_artifact, "local_prefix_values": new_artifact,
                          "legacy_current_hour_complete": bool(old_result["hour_complete"][-1]),
                          "legacy_current80_eligible": bool(old_result["window80_complete"][-1]),
                          "local_current80_eligible": bool(new_result["window80_complete"][-1])})
            result["counterfactual_results"] = cases
            require(all(new_comparison.values()), "local per-hour prefix changed past H24/quality under registered future perturbation")
            require(all(old_comparison.values()) if name == "future_valid_price_volume" else not all(old_comparison.values()),
                    "registered legacy positive-invariance or future-quality-dependence evidence absent")
        result["legacy_all_day_gate_future_dependence_actually_observed"] = True
        result["all_registered_local_prefix_future_invariance_checks_passed"] = True
        result["raw_retained_identity_limit"] = contract["retained_large_raw_whole_sha_identity"]
        result["not_proven"] = contract["qualifications"]
        result["status"] = PASS_STATUS
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
        result["status"] = "FAIL_FIXED000009_H24_SOURCE_OR_PARITY_OR_COUNTERFACTUAL_NO_RETRY"
        result["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
        artifacts.append({"role": "failure_traceback", **write_json(output / "actual_failure_traceback_v1.json", {"traceback": traceback.format_exc()[:65536]})})
    finally:
        result["artifacts"] = artifacts
        result["small_identity_before"] = small_before
        result["original_payload_STAT_before"] = payload_before
        result["retained_large_raw_STAT_before"] = dense_before
        try:
            small_after = {p: identity(p) for p in small_before}
            payload_after = {p: stat(p) for p in payload_before}
            dense_after = {k: stat(v["path"]) for k, v in dense_before.items()}
            result.update({"small_identity_after": small_after, "original_payload_STAT_after": payload_after, "retained_large_raw_STAT_after": dense_after})
            require(small_before and payload_before and dense_before and small_before == small_after and payload_before == payload_after and dense_before == dense_after and identity(Path(__file__).resolve()) == source_id,
                    "input/source guard changed or preflight incomplete")
            require(all(identity(v["path"]) == {k: v[k] for k in ("path", "bytes", "sha256")} for v in artifacts), "saved output byte guards changed")
            require(time.monotonic() - started <= 300, "numeric elapsed budget exceeded")
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_bytes = int(rss if sys.platform == "darwin" else rss * 1024)
            result["peak_RSS_bytes"] = rss_bytes
            require(rss_bytes < 200 * 1024**2, "200MiB peak process RSS budget exceeded")
            result["input_and_output_guards_unchanged"] = True
        except (Exception, KeyboardInterrupt) as exc:
            failure = failure or exc
            result["status"] = "FAIL_FIXED000009_H24_SOURCE_OR_PARITY_OR_COUNTERFACTUAL_NO_RETRY"
            result["guard_failure"] = {"type": type(exc).__name__, "reason": str(exc)}
            result["input_and_output_guards_unchanged"] = False
        result["actual_elapsed_seconds"] = time.monotonic() - started
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        result["raw_archive_opened_or_whole_large_SHA_repeated"] = False
        result["legacy_source_module_or_old_entrypoint_called"] = False
        result["model_checkpoint_scores_labels_normalization_IC_network_or_server_called"] = False
        result["claimed_feed_arrival_or_qfq_vintage_clearance"] = False
        receipt = write_json(output / "actual_H24_original_source_reconstruction_receipt_v1.json", result)
        print(json.dumps({"status": result["status"], "receipt": receipt, "new_training_fits": 0, "full_PLAN_complete": False}, ensure_ascii=False), flush=True)
    return 1 if failure else 0


def main():
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source-sha")
    parser.add_argument("--contract-sha")
    parser.add_argument("--acquisition-receipt-sha")
    args = parser.parse_args()
    if not args.execute:
        require(not any((args.source_sha, args.contract_sha, args.acquisition_receipt_sha)), "actual pins require explicit --execute")
        print(json.dumps(pure_contract(), ensure_ascii=False, indent=2))
        return 0
    return actual_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
