"""One real fixed-time normalization audit; never write original panel/source.

This tests normalization from retained generated raw H24/slow98 only. It does
not establish original minute cleaning or issuer publication/feed arrival.
No Torch, scoring, IC, fitting, source download or job control is used.
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import resource
import sys
import traceback
import warnings

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "gru_research/artifacts"
SAMPLE = "2024-06-18T10:30:00+08:00"
META_SHA = "2e144ad60a9aba1e258ef7ff114bf538ed22f6307e368b378b425557793a2849"
META_FP = "6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4"
SOURCE_DOC_SHA = "5c1e9d8e147a0178737a2ea850163e26cbfa4d12f99cb7269ec2d100ee200400"
TOP5 = ART / "top5_actual_forward_causality_20261002T113238Z/audit_output"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def identity(path, limit=8 * 1024**2):
    p = Path(path)
    require(p.is_file() and p.stat().st_size <= limit, "small-hash budget/missing: " + str(p))
    raw = p.read_bytes()
    return {"path": str(p), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def stat_identity(path):
    p = Path(path)
    s = p.stat()
    return {"path": str(p), "bytes": s.st_size, "mtime_ns": s.st_mtime_ns,
            "whole_file_hashed_by_this_audit": False}


def json_exclusive(path, value):
    with Path(path).open("x") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def npz_exclusive(path, **arrays):
    with Path(path).open("xb") as stream:
        np.savez(stream, **arrays)
    return identity(path)


def exact_array(a, b):
    return a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()


def semantic_array(a, b):
    return a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b, equal_nan=True)


class RowProxy:
    """Bounded read-only mmap selection and real counterfactual row changes."""
    def __init__(self, base, current, role, mode="baseline", members=None):
        self.base, self.current, self.role, self.mode = base, current, role, mode
        self.members = members
        self.reads = []

    def row(self, index):
        require(index in (self.current, self.current + 1), "outside fixed two-row read budget")
        value = np.array(self.base[index], copy=True)
        self.reads.append({"index": int(index), "bytes": value.nbytes})
        if self.mode == "future" and index > self.current:
            value = np.logical_not(value) if self.role == "members" else np.full_like(value, 625000.0)
        if self.mode == "nonmember" and index == self.current:
            require(self.role != "members", "current historical membership cannot be changed")
            # Replace missing as well as finite non-member fields, so the test
            # cannot silently pass merely because absent slow rows remain NaN.
            value[~self.members] = np.float32(-875000.0)
        return value


def extract_normalizer(source):
    """Compile the exact frozen function, without executing assembler imports."""
    original = source.read_text()
    tree = ast.parse(original, filename=str(source))
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name == "normalize_cross_section"]
    require(len(matches) == 1, "original normalization function missing/ambiguous")
    function = matches[0]
    standalone = ast.Module(body=[function], type_ignores=[])
    namespace = {"np": np, "warnings": warnings}
    exec(compile(standalone, str(source), "exec"), namespace)
    return namespace[function.name], {
        "source": identity(source), "start_line": function.lineno,
        "end_line": function.end_lineno,
        "AST_sha256": hashlib.sha256(ast.dump(function, include_attributes=False).encode()).hexdigest(),
        "exact_function_text": ast.get_source_segment(original, function),
        "module_top_level_imports_or_assemble_executed": False}


def combine(normalize, hour, slow, members):
    hour_out, hour_stats = normalize(hour, members)
    slow_out, slow_stats = normalize(slow, members)
    normalized = np.concatenate([hour_out, slow_out], axis=1)
    statistics = {key: np.concatenate([hour_stats[key], slow_stats[key]])
                  for key in ("count", "lo", "hi", "mean", "std")}
    return normalized, statistics


def compare(a, b):
    require(a.dtype == b.dtype == np.float32 and a.shape == b.shape, "FP32 shape/dtype differs")
    finite_both = np.isfinite(a) & np.isfinite(b)
    same_nan = np.isnan(a) == np.isnan(b)
    return {"shape": list(a.shape), "cells": int(a.size),
            "NaN_mask_mismatches": int(np.count_nonzero(~same_nan)),
            "finite_reference_cells": int(np.isfinite(b).sum()),
            "finite_numeric_mismatches": int(np.count_nonzero(finite_both & (a != b))),
            "finite_FP32_bit_mismatches": int(np.count_nonzero(finite_both & (a.view(np.uint32) != b.view(np.uint32)))),
            "all_value_and_NaN_equivalent": semantic_array(a, b),
            "complete_row_bytes_identical_including_NaN_payloads": exact_array(a, b)}


def run(output):
    global np
    output = Path(output).resolve()
    namespace = ROOT / "gru_research/trading_data/fixed_normalization_audit_20261002"
    require(output.is_relative_to(namespace) and not output.exists(), "isolated append-only output required")
    output.mkdir(parents=True, exist_ok=False)
    result = {"schema_version": 1, "status": "RUNNING", "sample_local": SAMPLE,
              "scope": "One real fixed time, retained generated raw H24+slow98 to historical-full-member normalization only",
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "new_training_fits": 0, "full_PLAN_complete": False,
              "not_proven": ["Original240minute cleaning", "raw source-end PIT/release/feed arrival",
                             "all-time/all-source/full122field generation causality", "generalization or truefuture60"],
              "original_large_inputs_ever_written": False,
              "Torch_IC_model_scoring_training_network_or_server_actions": False}
    error = None
    guards_before, stats_before = {}, {}
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(ROOT / "gru_research/.deps"))
        import numpy as loaded_numpy
        np = loaded_numpy
        dataset = ART / "dataset"
        doc = ART / "prospective_features_addon_manifest.json"
        require(identity(doc)["sha256"] == SOURCE_DOC_SHA, "source19 original document changed")
        original = json.loads(doc.read_bytes())
        source19 = dict(original["source_guards_sha256"])
        source19["gru_research/prospective_features.py"] = original["files_sha256"]["gru_research/prospective_features.py"]
        require(len(source19) == 19, "source19 count differs")
        small_paths = {ROOT / rel: sha for rel, sha in source19.items()}
        small_paths[doc] = SOURCE_DOC_SHA
        small_paths[dataset / "meta.json"] = META_SHA
        meta = json.loads((dataset / "meta.json").read_bytes())
        require(hashlib.sha256(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == META_FP,
                "frozen canonical metadata fingerprint changed")
        for name in ("times.npy", "symbols.json", "feature_names.json", "minute_feature_metadata.json", "slow_manifest.json", "slow_factor_selection.json"):
            small_paths[dataset / name] = meta["files_sha256"][name]
        for p in small_paths:
            guards_before[str(p)] = identity(p)
            require(guards_before[str(p)]["sha256"] == small_paths[p], "actual frozen small identity differs: " + str(p))
        minute = json.loads((dataset / "minute_feature_metadata.json").read_bytes())
        slow_manifest = json.loads((dataset / "slow_manifest.json").read_bytes())
        features = list(minute["hour_features"])
        slow_fields = list(slow_manifest["daily16"]) + list(slow_manifest["factors"]) + list(slow_manifest["fund16"]) + list(slow_manifest["ages2"])
        features += slow_fields
        require(features == meta["features"] == json.loads((dataset / "feature_names.json").read_bytes()), "explicit feature order differs")
        require(len(features) == 122 and len(minute["hour_features"]) == 24 and len(slow_fields) == 98, "frozen feature groups differ")
        cursor = 0
        for name, fields in (("hour", minute["hour_features"]), ("daily", slow_manifest["daily16"]),
                             ("factors", slow_manifest["factors"]), ("fund", slow_manifest["fund16"]), ("ages", slow_manifest["ages2"])):
            require(meta["groups"][name] == list(range(cursor, cursor + len(fields))), "declared field indices differ: " + name)
            cursor += len(fields)
        symbols = json.loads((dataset / "symbols.json").read_bytes())
        require(symbols == minute["union_symbols"] and len(symbols) == 1178, "historical union order differs")
        paths = {"hour": ART / "minute_hour_features.npy", "slow": dataset / "slow_raw_by_day.npy",
                 "members": ART / "minute_members.npy", "dataset_members": dataset / "members.npy", "X": dataset / "X.npy"}
        for role, path in paths.items():
            stats_before[role] = stat_identity(path)
        arrays = {role: np.load(path, mmap_mode="r", allow_pickle=False) for role, path in paths.items()}
        times = np.load(dataset / "times.npy", mmap_mode="r", allow_pickle=False)
        require(all(not array.flags.writeable for array in arrays.values()), "original mmap unexpectedly writable")
        require(arrays["hour"].shape == (8476,1178,24) and arrays["slow"].shape == (2119,1178,98)
                and arrays["X"].shape == (8476,1178,122), "retained raw/panel shapes differ")
        require(all(arrays[role].dtype == np.float32 for role in ("hour", "slow", "X")), "raw serialization is not frozenFP32")
        sample_ns = int(datetime.fromisoformat(SAMPLE).timestamp()) * 10**9
        matches = np.flatnonzero(times == sample_ns)
        require(len(matches) == 1, "fixed timestamp missing or ambiguous; no alternate date")
        t = int(matches[0]); d = t // 4
        require(t % 4 == 0 and minute["calendar_dates"][d] == "2024-06-18", "actual date/slot mapping disagrees")
        members = np.array(arrays["members"][t], copy=True)
        panel_members = np.array(arrays["dataset_members"][t], copy=True)
        require(members.dtype == np.bool_ and members.shape == (1178,) and exact_array(members, panel_members), "actual minute/panel member row differs")
        require(498 <= int(members.sum()) <= 502, "unverified full historical member pool")
        top5_receipt_path = TOP5 / "actual_forward_causality_receipt_v1.json"
        top5 = json.loads(top5_receipt_path.read_bytes())
        top5_input = TOP5 / "actual_input_tensor_equality.npz"
        small_paths[top5_receipt_path] = identity(top5_receipt_path)["sha256"]
        small_paths[top5_input] = top5["actual_input_equality_artifact"]["sha256"]
        for path in (top5_receipt_path, top5_input):
            guards_before[str(path)] = identity(path)
            require(guards_before[str(path)]["sha256"] == small_paths[path], "inherited actual eligible-mask artifact differs")
        with np.load(top5_input, allow_pickle=False) as inherited:
            window_mask = inherited["mask_before"].copy()
            require(int(inherited["time_index"][0]) == t and int(inherited["signal_time"][0]) == sample_ns,
                    "independently observed trained-forward window differs")
        require(window_mask.shape == members.shape and not (window_mask & ~members).any(), "complete80 subset not actual members")
        require(int(window_mask.sum()) == top5["current_real_eligible_members"] == 449, "inherited complete80 membership differs")
        normalize, function_proof = extract_normalizer(ROOT / "gru_research/assemble.py")
        with (output / "original_normalize_function_exact_text.txt").open("x") as stream:
            stream.write(function_proof.pop("exact_function_text") + "\n")
        proxies = {"hour": RowProxy(arrays["hour"], t, "hour"), "slow": RowProxy(arrays["slow"], d, "slow"),
                   "members": RowProxy(arrays["members"], t, "members")}
        hour = proxies["hour"].row(t); slow = proxies["slow"].row(d); pool = proxies["members"].row(t)
        rebuilt, fitted = combine(normalize, hour, slow, pool)
        actual_X = np.array(arrays["X"][t], copy=True)
        full_comparison = compare(rebuilt, actual_X)
        proof = npz_exclusive(output / "actual_full1178_raw_rebuilt_original_and_stats.npz",
                              raw_hour24=hour, raw_slow98=slow, members=pool, complete80_subset=window_mask,
                              rebuilt122=rebuilt, original122=actual_X, **{"fit_"+k:v for k,v in fitted.items()})
        result.update({"actual_time_index": t, "actual_day_index": d, "actual_signal_time_utc_ns": sample_ns,
                       "actual_historical_full_member_count": int(pool.sum()), "actual_complete80_eligible_subset_count": int(window_mask.sum()),
                       "normalization_reference_is_all_historical_members_not_eligible_subset": True,
                       "exact_feature_names": features, "groups": meta["groups"], "normalizer_exact_frozen_function": function_proof,
                       "actual_baseline_full1178_by122_comparison": full_comparison, "baseline_real_evidence": proof,
                       "case_results": [], "baseline_actual_row_read_traces": {k:v.reads for k,v in proxies.items()},
                       "raw_hour_wholeSHA_preexisting_in_selected_metadata": None,
                       "raw_slow_by_day_wholeSHA_preexisting_in_selected_metadata": None,
                       "raw_source_identity_limitation": "Retained generated raw arrays have actual stat guards and saved real slice SHA, without an independent prior whole-file SHA in these metadata. They are not recertified raw source-end data.",
                       "inherited_large_original_SHA_not_rehashed": {"dataset_X": meta["files_sha256"]["X.npy"],
                                                                    "members": meta["files_sha256"]["members.npy"],
                                                                    "slow_source_parquet_not_read": slow_manifest["raw_sha256"]}})
        # Re-extract original current rows on every case; no baseline cache is
        # reused to fabricate invariance. Probe actual changed future rows too.
        future_cases = (("future_generated_raw_rows", True, False), ("future_historical_member_row", False, True),
                        ("future_raw_and_member_rows", True, True))
        for name, mutate_raw, mutate_members in future_cases:
            case_proxy = {"hour": RowProxy(arrays["hour"], t, "hour", "future" if mutate_raw else "baseline"),
                          "slow": RowProxy(arrays["slow"], d, "slow", "future" if mutate_raw else "baseline"),
                          "members": RowProxy(arrays["members"], t, "members", "future" if mutate_members else "baseline")}
            witness = {}; future_values = {}
            for role in ("hour", "slow", "members"):
                current = d if role == "slow" else t
                original_future = np.array(arrays[role][current+1], copy=True)
                changed = case_proxy[role].row(current+1)
                changed_cells = int(np.count_nonzero(~((original_future == changed) | (np.isnan(original_future) & np.isnan(changed)))))
                expected_changed = mutate_members if role == "members" else mutate_raw
                require((changed_cells > 0) == expected_changed, "future counterfactual is vacuous or changes an unintended role")
                witness[role] = {"changed_future_cells": changed_cells, "future_index": current+1}
                future_values[role] = changed
                case_proxy[role].reads.clear()
            case_hour = case_proxy["hour"].row(t); case_slow = case_proxy["slow"].row(d); case_members = case_proxy["members"].row(t)
            case_out, case_stats = combine(normalize, case_hour, case_slow, case_members)
            stats_equal = {k: exact_array(v, fitted[k]) for k,v in case_stats.items()}
            passed = exact_array(case_out, rebuilt) and all(stats_equal.values()) and exact_array(case_members, pool)
            case_file = npz_exclusive(output / (name+"_actual_case.npz"),
                                      normalized_current=case_out, current_members=case_members,
                                      changed_future_hour=future_values["hour"], changed_future_slow=future_values["slow"],
                                      changed_future_members=future_values["members"], **{"fit_"+k:v for k,v in case_stats.items()})
            result["case_results"].append({"case": name, "PASS": passed, "all1178_current_outputs_exact": exact_array(case_out, rebuilt),
                                            "fit_statistics_exact": stats_equal, "nonvacuity_witness": witness,
                                            "current_row_reextraction_traces": {k:v.reads for k,v in case_proxy.items()},
                                            "evidence": case_file, "counterfactual_input_generated_from_real_retained_rows": True})
            require(passed, "future-only retained-row mutation changed fixed-time statistics/output")
        current_hour = RowProxy(arrays["hour"], t, "hour", "nonmember", pool).row(t)
        current_slow = RowProxy(arrays["slow"], d, "slow", "nonmember", pool).row(d)
        nonmember_out, nonmember_stats = combine(normalize, current_hour, current_slow, pool.copy())
        changed_input_cells = int(np.count_nonzero(~((np.concatenate([hour,slow],axis=1) == np.concatenate([current_hour,current_slow],axis=1))
                                                    | (np.isnan(np.concatenate([hour,slow],axis=1)) & np.isnan(np.concatenate([current_hour,current_slow],axis=1))))))
        require(changed_input_cells > 0 and exact_array(current_hour[pool],hour[pool]) and exact_array(current_slow[pool],slow[pool]),
                "current nonmember perturbation vacuous or changed members")
        nonmember_statistics_equal = {k: exact_array(v, fitted[k]) for k,v in nonmember_stats.items()}
        actual_member_exact = exact_array(nonmember_out[pool], rebuilt[pool])
        nonmember_file = npz_exclusive(output / "current_nonmember_raw_rows_actual_case.npz", raw_hour=current_hour, raw_slow=current_slow,
                                       members=pool, normalized122=nonmember_out, **{"fit_"+k:v for k,v in nonmember_stats.items()})
        result["case_results"].append({"case":"current_nonmember_raw_rows", "PASS": actual_member_exact and all(nonmember_statistics_equal.values()),
                                        "changed_raw_nonmember_input_cells": changed_input_cells,
                                        "all_actual_members_current_outputs_exact": actual_member_exact,
                                        "fit_statistics_exact": nonmember_statistics_equal,
                                        "nonmember_own_output_equality_required": False,
                                        "nonmember_own_output_value_and_NaN_equivalent": semantic_array(nonmember_out[~pool], rebuilt[~pool]),
                                        "evidence": nonmember_file})
        require(actual_member_exact and all(nonmember_statistics_equal.values()), "nonmember values leaked into normalization fit/member output")
        require(full_comparison["all_value_and_NaN_equivalent"] and full_comparison["complete_row_bytes_identical_including_NaN_payloads"],
                "real baseline differs from originalX; no tolerance adjustment permitted")
        result["status"] = "PASS_ONE_REAL_TIME_ALL122_FULL_HISTORICAL_MEMBER_NORMALIZATION_EXACT_AND_LOCAL_CAUSAL_CASES"
        result["row_payload_budget_bound"] = {"hour_two_rows_bytes": int(hour.nbytes*2), "slow_two_rows_bytes": int(slow.nbytes*2),
                                            "original_X_one_row_bytes": int(actual_X.nbytes), "no_whole_large_table_or_array_read": True,
                                            "read_mechanism": "read-only mmap two bounded rows per raw role; one originalX row; no raw slow parquet body decoded"}
    except Exception as exc:
        error = exc
        result["status"] = "FAIL_OR_REJECT_NO_LOCAL_NORMALIZATION_CLEARANCE"
        result["failure"] = {"type":type(exc).__name__, "reason":str(exc)}
        with (output / "actual_failure_traceback.txt").open("x") as stream:
            stream.write(traceback.format_exc())
    finally:
        try:
            guards_after = {path:identity(path) for path in guards_before}
            stats_after = {role:stat_identity(v["path"]) for role,v in stats_before.items()}
            require(guards_after == guards_before and stats_after == stats_before, "original smallSHA/largeSTAT changed")
            result["original_small_actual_SHA_before"] = guards_before
            result["original_small_actual_SHA_after"] = guards_after
            result["large_original_stat_before"] = stats_before
            result["large_original_stat_after"] = stats_after
            result["original_guards_unchanged"] = True
        except Exception as exc:
            error = error or exc
            result["status"] = "FAIL_OR_REJECT_ORIGINAL_GUARD_CHANGED"
            result["guard_failure"] = {"type":type(exc).__name__, "reason":str(exc)}
        result["peak_process_RSS_bytes"] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        result["peak_RSS_units"] = "macOS ru_maxrss bytes"
        if result["peak_process_RSS_bytes"] >= 200 * 1024**2:
            error = error or ValueError("200MiB process memory budget exceeded")
            result["status"] = "FAIL_OR_REJECT_MEMORY_BUDGET"
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        result["script"] = identity(__file__)
        json_exclusive(output / "actual_normalization_causality_receipt_v1.json", result)
    print(json.dumps({"status":result["status"], "receipt":identity(output / "actual_normalization_causality_receipt_v1.json"),
                      "time_index":result.get("actual_time_index"), "members":result.get("actual_historical_full_member_count"),
                      "peak_RSS_bytes":result["peak_process_RSS_bytes"], "full_PLAN_complete":False}, ensure_ascii=False))
    if error:
        raise RuntimeError("local fixed-time audit failed; preserve original differences and do not automatically retry") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status":"PREPARED_ONLY", "fixed_sample":SAMPLE, "actual_audit_executed":False}))
        return
    require(args.output is not None, "explicit append-only output required")
    run(args.output)


if __name__ == "__main__":
    main()
