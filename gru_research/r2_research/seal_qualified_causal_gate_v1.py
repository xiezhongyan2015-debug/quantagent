"""Bind actual bounded causal evidence to the limited preregistered R2 scope."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
GRU = ROOT / "gru_research"
ART = GRU / "artifacts"
OUT = GRU / "trading_data/r2_causal_minute_check_20261002"
PROTOCOL_SHA = "e8d5f6a15bba4496ab1895c9d9b781340acc1ef90782440a0b6c23822384205f"
PREFIX_SHA = "a0a9dc8b0f71a4b917fc283ced6cfed44b14e51c9b23b93284ad1c00a426a775"


def identity(path):
    p = Path(path)
    if p.stat().st_size > 2_000_000:
        raise ValueError("large body outside small evidence seal")
    body = p.read_bytes()
    return {"path": str(p), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def load(path):
    return json.loads(Path(path).read_bytes())


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def main():
    target = OUT / "qualified_r2_causal_gate_receipt_v1.json"
    require(not target.exists(), "append-only gate already exists")
    protocol_path = GRU / "r2_research/protocol_v1.json"
    require(identity(protocol_path)["sha256"] == PROTOCOL_SHA, "research protocol changed")
    protocol = load(protocol_path)
    declared = protocol["original_dataset"]
    meta_path = ART / "dataset/meta.json"
    meta = load(meta_path)
    require(identity(meta_path)["sha256"] == declared["meta_sha256"], "original metadata bytes differ")
    fp = hashlib.sha256(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(fp == declared["canonical_metadata_fingerprint"], "canonical metadata fingerprint differs")
    source19 = declared["original_source19_sha256"]
    original_before = {name: identity(ROOT / name) for name in source19}
    require(all(item["sha256"] == source19[name] for name,item in original_before.items()), "source19 hash mismatch")
    manifest_path = ART / "prospective_features_addon_manifest.json"
    require(identity(manifest_path)["sha256"] == declared["original_source19_guard_receipt_sha256"], "original19 manifest receipt changed")
    manifest = load(manifest_path)
    manifest19 = dict(manifest["source_guards_sha256"])
    manifest19["gru_research/prospective_features.py"] = manifest["files_sha256"]["gru_research/prospective_features.py"]
    require(manifest19 == source19, "original19 manifest map differs")
    quality_path = ART / "minute_quality_report.json"
    quality = load(quality_path)
    trigger_count = sum(quality[k] for k in ("duplicate_rows", "timestamp_mismatch_rows", "qfq_mismatch_rows", "stockday_errors"))
    require(trigger_count == 0, "frozen panel zero-known-trigger qualification failed")
    prefix_path = OUT / "real_prefix_invariance_receipt_v2.json"
    require(identity(prefix_path)["sha256"] == PREFIX_SHA, "actual prefix receipt changed")
    prefix = load(prefix_path)
    require(prefix["status"] == "PASS_BOUNDED_REAL_HOUR_VALUES_QUALITY_AND_ACTUAL_80H_ELIGIBILITY_FUTURE_INVARIANCE", "real prefix did not pass")
    expected_cases = {"normal_future_append", "normal_future_prices", "future_conflicting_duplicate", "future_datetime_mismatch", "future_dual_clock_wrong_past_stamp", "future_factor_change_consistent_qfq"}
    require({x["case"] for x in prefix["real_case_results"]} == expected_cases and len(prefix["real_case_results"]) == 6, "case roster differs")
    for case in prefix["real_case_results"]:
        require(all(case[k] is True for k in ("original_genuine_stockday", "past84_hour24_values_equal_including_NaNs", "finite_masks_equal", "hour_complete_equal", "80hour_eligibility_equal", "current_real_historical_member_and_80complete", "past_hour_quality_equal")), "case invariance evidence incomplete")
        for original in case["artifacts"]:
            require(identity(original["path"])["sha256"] == original["sha256"], "actual case material bytes changed")
    artifacts = {}
    def add(path, role):
        item = dict(identity(path), role=role)
        previous = artifacts.get(item["path"])
        if previous and previous != item:
            raise ValueError("duplicate identity with conflicting role")
        artifacts[item["path"]] = item
    add(quality_path, "whole_source_trigger_report")
    clock_dir = GRU / "trading_data/causality_targeted_audit_20261002"
    add(clock_dir / "bounded_actual_time_membership_receipt_v1.json", "clock_membership")
    add(clock_dir / "bounded_time_membership_check_v1.py", "clock_membership_verifier_source")
    add(clock_dir / "targeted_data_causality_review_v1.json", "strict_review_with_limits")
    add(clock_dir / "source_identity_inventory_receipt_v1.json", "original_small_source_evidence_inventory")
    add(prefix_path, "real_prefix_invariance")
    add(OUT / "original_sample_extraction_receipt_v1.json", "bounded_original_sample_extraction")
    add(prefix["genuine_original_minute"]["path"], "real_original_240minute_sample")
    add(prefix["genuine_retained_prior83_hour_material"]["path"], "real_prior83_hour_context_without_labels")
    add(prefix["actual_membership"]["path"], "real_actual_current_membership_source")
    for original in prefix["material_artifacts"]:
        add(original["path"], original["role"])
    add(OUT / "real_prefix_actual_stdout_v2.log", "actual_prefix_stdout")
    add(OUT / "real_prefix_actual_stderr_v2.log", "actual_prefix_stderr")
    failure_dir = OUT / "first_attempt_precision_failure_v1"
    add(failure_dir / "failure_receipt_v1.json", "initial_failed_precision_attempt_receipt")
    for original in load(failure_dir / "failure_receipt_v1.json")["files"]:
        add(original["archive_path"], "initial_failed_precision_attempt_original")
    for name,role in (("causal_minute_adapter.py", "causal_adapter_source"), ("verify_real_minute_prefix_v2.py", "real_prefix_verifier_source"), ("extract_bounded_real_minute_v1.py", "bounded_extractor_source"), ("seal_qualified_causal_gate_v1.py", "causal_gate_sealer_source")):
        add(GRU / "r2_research" / name, role)
    add(protocol_path, "fixed_research_protocol")
    add(meta_path, "fixed_original_metadata")
    add(manifest_path, "original_source19_manifest")
    add(ART / "minute_source_fingerprints.json", "inherited_large_minute_source_fingerprints_not_rehashed")
    clock = load(clock_dir / "bounded_actual_time_membership_receipt_v1.json")
    for entry in clock["small_actual_time_arrays"].values():
        add(entry["path"], "real_serialized_small_time_array")
    for entry in clock["dated_membership_checks"]:
        add(entry["original_source"]["path"], "real_clock_check_dated_membership_source")
    add(clock["next_session_date_source_only_not_a_future_price"]["path"], "real_clock_tail_date_source_only")
    add(ART / "minute_calendar.json", "original_calendar")
    add(ART / "dataset/symbols.json", "original_symbol_universe")
    launch_before = identity(ART / "launch_status.json")
    result = {"schema_version": 1,
        "status": "PASS_QUALIFIED_R2_ORIGINAL_PANEL_WITH_BOUNDED_CAUSAL_REPAIR_CHECK",
        "scope": "PV_ONLY_LABEL3_2LR_3CHRONO_FOLDS_5SEEDS_35CAP",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": PROTOCOL_SHA,
        "evidence_is_synthetic": False,
        "actual_small_evidence_verified": True,
        "fixed_panel_rebuilt": False,
        "whole_scan_trigger_count": trigger_count,
        "whole_scan_trigger_count_basis": "Existing original full scan reports zero duplicates/string-vs-ns mismatches/qfq mismatches/stockday errors, so no recorded whole_bad trigger.2077 nonpositive-price bad rows remain row-local. Full compressed volume SHA is inherited, not recomputed.",
        "full_end_to_end_causality_proven": False,
        "trained_model_score_perturbation": "not_performed",
        "original_meta_sha256": declared["meta_sha256"],
        "original_metadata_fingerprint": fp,
        "original_source19_sha256": source19,
        "adapter": {"relative_path": "gru_research/r2_research/causal_minute_adapter.py", "sha256": identity(GRU / "r2_research/causal_minute_adapter.py")["sha256"]},
        "evidence_artifacts": list(artifacts.values()),
        "real_prefix_case_names": sorted(expected_cases),
        "real_source_scope": {"stockday": prefix["day"], "symbol": prefix["symbol"], "original_regular_minute_rows": 240,
              "real_prior_hour_rows": 83, "actual_comparison_hour_rows": 84, "per_case_hour_field_values": 2016,
              "current_historical_member_and_actual80complete": True,
              "healthy4hour_raw_values_bitwise_match_original_aggregator": True,
              "current24float32_features_exact_match_original_retained_feature_source": True,
              "later_mutable_clock_does_not_overwrite_byte_bound_original_ordinal_clock": True,
              "source_clock_is_historical_bar_completion_not_feed_arrival": True},
        "qualification_limits": [
              "Only this fixed research protocol may use the unchanged original frozen panel under its zero-observed-trigger qualification; the new adapter does not regenerate or silently repair originalX.",
              "The independently bound dual-clock/ordinal witness proves historical availability isolation for the genuine sampled source and counterfactual tests. It does not certify unobserved new provider/live arrival semantics or arbitrary unauthenticated ordinal mappings.",
              "The context uses actual retained hours; the counterfactual minute material is explicitly testing-only and must never be admitted as genuine training/market observations.",
              "All-member normalization, all122field real perturbation, issuer precise PIT publication clocks, arbitrary adjustment revisions and trained-model score invariance remain unperformed/unproved.",
              "PV discards factors and fundamental/age/industry branch effects in original model.py37-43; lagged daily16 including historical market-cap state still uses the original vendor asof/next-session convention.",
              "This is qualified engineering/research evidence, not completePLAN acceptance, verified net performance, a virgin historical test or60-session prospective certification."],
        "required_root_independent_review_before_execute": True,
        "runner_must_supply_out_of_band_expected_receipt_sha_and_closed_transport_manifest": True,
        "transport_rule": "evidence_artifacts.path is the exact original local identity key, not a remote runtime path. The closed manifest must map every listed original identity to an explicit safe relative path with identical bytes/SHA. No basename guessing and no fresh whole943MB verification claim.",
        "original19_actual_small_source_identities_before_after": original_before,
        "launch_status_readonly_before": launch_before,
        "new_training_started_by_this_subtask": False,
        "full_PLAN_complete": False,
        "initial_failed_precision_attempt_preserved": True,
        "initial_failure_diagnosis": "First helper omitted frozen float32 H24 serialization; fixed only the new adapter then executed independent v2 once. No frozen source/data change or market-result adjustment.",
        "sealer": identity(Path(__file__)),
    }
    require({name:identity(ROOT/name) for name in source19} == original_before, "source19 changed during seal")
    require(identity(ART/"launch_status.json") == launch_before, "launch changed during seal")
    for artifact in artifacts.values():
        require(identity(artifact["path"])["sha256"] == artifact["sha256"], "small proof changed during seal")
    result["launch_status_readonly_after"] = identity(ART / "launch_status.json")
    result["all_named_small_artifact_identities_unchanged_before_after"] = True
    with target.open("x") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"receipt": identity(target), "artifacts": len(artifacts), "total_evidence_bytes":sum(x["bytes"] for x in artifacts.values()),
                      "adapter": result["adapter"], "actual_cases":len(expected_cases), "no_training_started":True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
