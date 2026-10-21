"""Actual original stockday + retained real-hour prefix, not synthetic fixtures."""
from pathlib import Path
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import io
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
GRU = ROOT / "gru_research"
ART = GRU / "artifacts"
OUT = GRU / "trading_data/r2_causal_minute_check_20261002"
sys.path[:0] = [str(GRU / ".deps"), str(Path(__file__).parent)]
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from causal_minute_adapter import (original_clock_witness, aggregate_stock_day,
                                  add_causal_hour_features, H24, MINUTE)


def ident(path):
    p = Path(path)
    b = p.read_bytes()
    return {"path": str(p), "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def dump(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def numeric_digest(values):
    a = np.asarray(values, dtype=np.float64)
    mask = np.isfinite(a)
    return hashlib.sha256(np.where(mask, a, 0).tobytes() + mask.tobytes()).hexdigest()


def main():
    receipt_path = OUT / "real_prefix_invariance_receipt_v2.json"
    require(not receipt_path.exists(), "append-only output already exists")
    extraction_path = OUT / "original_sample_extraction_receipt_v1.json"
    extraction = json.loads(extraction_path.read_bytes())
    selected = extraction["selected"]
    sample_path = Path(selected["original_payload"]["path"])
    require(ident(sample_path) == selected["original_payload"], "original sample bytes changed")
    day, symbol = selected["day"], selected["symbol"]
    body = sample_path.read_bytes()
    table = pq.read_table(io.BytesIO(body), use_threads=False)
    witness = original_clock_witness(body, day, symbol)
    ordinals = np.arange(240, dtype=np.int64)
    cutoff = int(witness.start_ns[59] + MINUTE)
    calendar_path = ART / "minute_calendar.json"
    calendar = json.loads(calendar_path.read_bytes())
    target_position = calendar["dates"].index(day) * 4
    require(target_position >= 83, "no genuine preceding83 hour slots")
    exact_times = np.concatenate([pd.Timestamp(d, tz="Asia/Shanghai").tz_convert("UTC").value + np.array([630, 690, 840, 900]) * MINUTE for d in calendar["dates"]])
    context_times = exact_times[target_position-83:target_position]
    cached = ART / "minute_symbol_features" / (symbol + ".parquet")
    cached_before = ident(cached)
    columns = ["date", "symbol", "slot", "signal_time", "hour_complete", "minute_count", "open", "high", "low", "close", "volume", "raw_open", "raw_high", "raw_low", "raw_close", "adjustment_factor"] + H24[14:]
    context = pd.read_parquet(cached, columns=columns,
                filters=[("signal_time", ">=", int(context_times[0])), ("signal_time", "<", cutoff)]).sort_values("signal_time")
    require(np.array_equal(context.signal_time.to_numpy(), context_times), "retained real prefix is not83 exact hour slots")
    context_path = OUT / "retained_real_prior83_hour_context_v2.parquet"
    with context_path.open("xb") as stream:
        pq.write_table(pa.Table.from_pandas(context, preserve_index=False), stream, compression="zstd")
    member_path = ROOT / "中证500_历年成分股_日频_2010-2026_iFinD_后复权/memberships" / (day + ".json.gz")
    member = json.loads(gzip.decompress(member_path.read_bytes()))
    syms = member.get("thscode") or member["data"][0]["table"]["THSCODE"]
    require(member["errorcode"] == 0 and symbol in syms, "actual sample is not a current historical member")
    frozen_map = json.loads((GRU / "r2_research/protocol_v1.json").read_bytes())["original_dataset"]["original_source19_sha256"]
    source_before = {name: ident(ROOT / name) for name in frozen_map}
    require(all(item["sha256"] == frozen_map[name] for name,item in source_before.items()), "original source19 bytes differ")
    guarded_paths = [calendar_path, ART / "dataset/meta.json", ART / "launch_status.json", sample_path, member_path,
                     GRU / "r2_research/protocol_v1.json", Path(__file__).parent / "causal_minute_adapter.py", Path(__file__)]
    before = [ident(path) for path in guarded_paths]
    spec = importlib.util.spec_from_file_location("frozen_minute_prefix_witness", GRU / "minute_data.py")
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    original_old_rows, old_audit = old.aggregate_stock_day((day, symbol, body))
    baseline_rows = aggregate_stock_day(table, witness, ordinals)
    require(len(original_old_rows) == len(baseline_rows) == 4 and all(x["hour_complete"] for x in baseline_rows), "healthy original hour build differs")
    hour_fields = columns[6:]
    base_hour_values = pd.DataFrame(baseline_rows)[hour_fields].to_numpy(dtype=float)
    old_hour_values = pd.DataFrame(original_old_rows)[hour_fields].to_numpy(dtype=float)
    require(np.array_equal(base_hour_values, old_hour_values, equal_nan=True), "healthy raw-hour values differ from frozen aggregator")
    baseline_frame = add_causal_hour_features(pd.concat([context, pd.DataFrame(baseline_rows)], ignore_index=True))
    base_prefix = baseline_frame[baseline_frame.signal_time <= cutoff].copy()
    require(len(base_prefix) == 84 and bool(base_prefix.iloc[-1].window80_complete), "real sample did not produce an actually complete80-hour window")
    cache_current = pd.read_parquet(cached, columns=["signal_time"] + H24,
                filters=[("signal_time", "==", cutoff)])
    require(len(cache_current) == 1 and np.array_equal(base_prefix.iloc[-1][H24].to_numpy(dtype=np.float32), cache_current[H24].iloc[0].to_numpy(dtype=np.float32), equal_nan=True), "recomputed current24 fields differ from original retained feature source")
    original_data = table.to_pydict()
    cases = [("normal_future_append", table.slice(0,60), ordinals[:60], False),
             ("normal_future_prices", None, ordinals, False),
             ("future_conflicting_duplicate", None, np.r_[ordinals,210], True),
             ("future_datetime_mismatch", None, ordinals, True),
             ("future_dual_clock_wrong_past_stamp", None, ordinals, True),
             ("future_factor_change_consistent_qfq", None, ordinals, True)]
    results, files = [], []
    baseline_path = OUT / "real_h24_prefix_baseline.npz"
    with baseline_path.open("xb") as stream:
        np.savez(stream, times=base_prefix.signal_time.to_numpy(np.int64), X_hour24=base_prefix[H24].to_numpy(dtype=np.float64),
                 hour_complete=base_prefix.hour_complete.to_numpy(bool), window80_complete=base_prefix.window80_complete.to_numpy(bool))
    files.append(dict(ident(baseline_path), role="actual_hour_prefix_baseline"))
    for name, material, mapping, demonstrates_old in cases:
        data = {k:list(v) for k,v in original_data.items()}
        if material is None:
            if name == "normal_future_prices":
                for k in ("open", "high", "low", "close", "qfq_open", "qfq_high", "qfq_low", "qfq_close"):
                    for j in range(60,240):
                        data[k][j] *= 1.17
            elif name == "future_conflicting_duplicate":
                for k in data:
                    data[k].append(data[k][210])
                data["volume"][-1] += 1
            elif name in ("future_datetime_mismatch", "future_dual_clock_wrong_past_stamp"):
                data["datetime"][210] = data["datetime"][30]
                if name == "future_dual_clock_wrong_past_stamp":
                    data["datetime_nano"][210] = data["datetime_nano"][30]
            elif name == "future_factor_change_consistent_qfq":
                data["qfq_ratio"][210] *= 1.5
                for k in ("qfq_open", "qfq_high", "qfq_low", "qfq_close"):
                    data[k][210] *= 1.5
            material = pa.Table.from_pydict(data, schema=table.schema)
        material_path = OUT / (name + "_counterfactual_only.parquet")
        with material_path.open("xb") as stream:
            pq.write_table(material, stream, compression="zstd")
        mapping_path = OUT / (name + "_immutable_original_row_ordinals.npy")
        with mapping_path.open("xb") as stream:
            np.save(stream, mapping, allow_pickle=False)
        rows = aggregate_stock_day(material, witness, mapping)
        current = add_causal_hour_features(pd.concat([context, pd.DataFrame(rows)], ignore_index=True))
        prefix = current[current.signal_time <= cutoff].copy()
        values_equal = np.array_equal(prefix[H24].to_numpy(dtype=float), base_prefix[H24].to_numpy(dtype=float), equal_nan=True)
        masks_equal = np.array_equal(np.isfinite(prefix[H24].to_numpy(dtype=float)), np.isfinite(base_prefix[H24].to_numpy(dtype=float)))
        completeness_equal = np.array_equal(prefix.hour_complete.to_numpy(), base_prefix.hour_complete.to_numpy())
        eligible_equal = np.array_equal(prefix.window80_complete.to_numpy(), base_prefix.window80_complete.to_numpy())
        require(values_equal and masks_equal and completeness_equal and eligible_equal and rows[0]["hour_quality"] == baseline_rows[0]["hour_quality"], "real prefix changed under " + name)
        require(bool(prefix.iloc[-1].window80_complete), "actual member80-hour eligibility lost")
        old_sink = io.BytesIO()
        pq.write_table(material, old_sink, compression="zstd")
        mutated_old, mutated_old_audit = old.aggregate_stock_day((day, symbol, old_sink.getvalue()))
        old_past_complete = bool(mutated_old and mutated_old[0]["hour_complete"])
        if demonstrates_old:
            require(not old_past_complete, "old whole-day defect not demonstrated by real counterfactual " + name)
        path = OUT / (name + "_actual_prefix_result.npz")
        with path.open("xb") as stream:
            np.savez(stream, times=prefix.signal_time.to_numpy(np.int64), X_hour24=prefix[H24].to_numpy(dtype=np.float64),
                     hour_complete=prefix.hour_complete.to_numpy(bool), window80_complete=prefix.window80_complete.to_numpy(bool))
        artifacts = [dict(ident(material_path), role="counterfactual_minute_material_not_market_data"),
                     dict(ident(mapping_path), role="immutable_original_row_clock_mapping"),
                     dict(ident(path), role="actual_hour_prefix_result")]
        files += artifacts
        results.append({"case": name, "original_genuine_stockday": True, "counterfactual_not_training_or_market_data": True,
                        "past84_hour24_values_equal_including_NaNs": bool(values_equal), "finite_masks_equal": bool(masks_equal),
                        "hour_complete_equal": bool(completeness_equal), "80hour_eligibility_equal": bool(eligible_equal),
                        "current_real_historical_member_and_80complete": True, "past_hour_quality_equal": True,
                        "prefix_value_and_mask_digest": numeric_digest(prefix[H24]), "artifacts": artifacts,
                        "old_frozen_aggregator_past_hour_complete": old_past_complete,
                        "old_quality_lookahead_counterexample_observed": demonstrates_old,
                        "old_counterfactual_audit": mutated_old_audit})
    require(ident(cached) == cached_before, "cached real hour source changed")
    require([ident(x["path"]) for x in before] == before, "small guarded source/launch changed")
    require({name:ident(ROOT/name) for name in frozen_map} == source_before, "frozen19 changed")
    result = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
              "status": "PASS_BOUNDED_REAL_HOUR_VALUES_QUALITY_AND_ACTUAL_80H_ELIGIBILITY_FUTURE_INVARIANCE",
              "original_extraction_receipt": ident(extraction_path), "genuine_original_minute": ident(sample_path),
              "genuine_retained_real_hour_source": cached_before, "source_read_scope": "Only explicit hour/input columns for83 preceding rows plus current24 feature fields; no labels/execution fields/scores.",
              "genuine_retained_prior83_hour_material": ident(context_path), "actual_membership": ident(member_path),
              "day": day, "symbol": symbol, "comparison_cutoff_utc_ns": cutoff,
              "clock_witness": {"original_payload_sha256": witness.original_payload_sha256, "basis": witness.clock_basis,
                                "dual_source_clocks_verified": True, "physical_original_row_order_verified": True,
                                "row_start_witness_sha256": hashlib.sha256(np.asarray(witness.start_ns,dtype=np.int64).tobytes()).hexdigest(),
                                "mutable_event_timestamps_cannot_change_source_ordinals_or_known_at": True,
                                "independent_feed_arrival_observed": False},
              "healthy_original4hours_bitwise_match_frozen_aggregator": True,
              "current_recomputed24_input_fields_match_retained_real_source": True,
              "current80hour_window_genuinely_complete_and_current_member": True,
              "past_rows_compared": 84, "hour_fields_compared_per_case": 2016,
              "real_case_results": results, "material_artifacts": files,
              "source19_before_after": source_before, "small_guards_before_after": before,
              "original_raw_minute_source_scan_rerun": False, "whole_panel_regenerated": False,
              "normal_future_append_baseline_has_only_first60_actual_rows": True,
              "old_defect_on_counterfactual_is_not_actual_frozen_dataset_contamination": True,
              "real_trained_model_scores_or_normalization_allmember_tensor_perturbation": "not_performed",
              "all122fields_or_provider_release_arrival_causality_proven": False,
              "Torch_old_tests_training_inference_or_network_used": False,
              "full_PLAN_complete": False, "verifier": ident(Path(__file__)), "adapter": ident(Path(__file__).parent/"causal_minute_adapter.py")}
    dump(receipt_path,result)
    print(json.dumps({"receipt":ident(receipt_path), "cases": len(results), "actual80complete": True,
                      "genuine_day":day,"symbol":symbol,"old_structural_real_counterexamples":sum(x["old_quality_lookahead_counterexample_observed"] for x in results)},ensure_ascii=False))


if __name__ == "__main__":
    main()
