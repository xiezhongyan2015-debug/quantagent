"""Require qualified real evidence; portable small-material mapping, never broad causal certification."""
from __future__ import annotations

import json
from pathlib import Path
from .protocol import PROTOCOL_SHA256, require, sha

MAX_SMALL_BYTES = 64 * 1024 * 1024
RUNTIME_SOURCE_FILES = (
    "gru_research/r2_research/__init__.py", "gru_research/r2_research/protocol.py",
    "gru_research/r2_research/protocol_v1.json", "gru_research/r2_research/folds.py",
    "gru_research/r2_research/causal_gate.py", "gru_research/r2_research/runner.py",
    "gru_research/r2_research/transport.py", "gru_research/r2_research/causal_minute_adapter.py",
)


def safe_child(directory, relative):
    p = Path(relative)
    require(isinstance(relative, str) and not p.is_absolute() and ".." not in p.parts and p.parts,
            "material path is not a closed safe relative path")
    root = Path(directory).resolve()
    child = (root / p).resolve()
    require(child != root and root in child.parents, "material escaped closed evidence directory")
    return child


def measured_small(path, expected, size=None):
    p = Path(path)
    require(p.is_file() and p.stat().st_size <= MAX_SMALL_BYTES, "required small evidence missing/too large")
    blob = p.read_bytes()
    require(sha(blob) == expected, "actual small material SHA differs: " + str(p))
    if size is not None:
        require(type(size) is int and len(blob) == size, "actual small material size differs")
    return blob


def source_seal(root, source_manifest, expected_manifest_sha256):
    require(expected_manifest_sha256, "root-reviewed R2 source-manifest SHA required")
    blob = measured_small(source_manifest, expected_manifest_sha256)
    doc = json.loads(blob)
    require(doc.get("schema_version") == 1 and doc.get("protocol_sha256") == PROTOCOL_SHA256,
            "new R2 source seal protocol differs")
    sources = doc.get("files_sha256")
    require(isinstance(sources, dict) and set(sources) == set(RUNTIME_SOURCE_FILES), "exact R2 runtime source set required")
    actual = {name: sha(safe_child(root, name).read_bytes()) for name in sources}
    require(actual == sources, "reviewed R2 runtime source changed")
    return {"manifest_sha256": sha(blob), "files_sha256": actual}


def verify_gate(root, dataset, directory, expected_receipt_sha256, expected_manifest_sha256, protocol):
    """All checks precede Torch import and memory-mapping original panel arrays."""
    require(expected_receipt_sha256 and expected_manifest_sha256,
            "root-reviewed causal receipt and closed-transport-manifest hashes required")
    directory = Path(directory)
    manifest = json.loads(measured_small(directory / "evidence_manifest.json", expected_manifest_sha256))
    require(manifest.get("schema_version") == 1 and manifest.get("closed_material_mapping") is True,
            "portable evidence requires an explicit closed mapping")
    entry = manifest["receipt"]
    require(entry["sha256"] == expected_receipt_sha256, "transport receipt differs from root-reviewed identity")
    receipt_path = safe_child(directory, entry["relative_path"])
    receipt = json.loads(measured_small(receipt_path, expected_receipt_sha256, entry["bytes"]))
    gate = protocol["causal_gate"]
    require(receipt.get("schema_version") == 1 and receipt.get("status") == gate["required_status"]
            and receipt.get("scope") == gate["required_scope"], "missing qualified real R2 permission scope")
    require(receipt.get("protocol_sha256") == PROTOCOL_SHA256,
            "causal proof was not reviewed for this exact R2 protocol")
    require(receipt.get("evidence_is_synthetic") is False and receipt.get("actual_small_evidence_verified") is True,
            "synthetic/unverified causal evidence cannot enable execution")
    require(receipt.get("fixed_panel_rebuilt") is False and receipt.get("full_end_to_end_causality_proven") is False
            and receipt.get("trained_model_score_perturbation") == "not_performed",
            "bounded repair must not be upgraded to rebuilt data or full score causality")
    require(type(receipt.get("whole_scan_trigger_count")) is int and receipt["whole_scan_trigger_count"] == 0,
            "original whole-source global-bad trigger was not actually zero")
    original = protocol["original_dataset"]
    require(receipt.get("original_meta_sha256") == original["meta_sha256"]
            and receipt.get("original_metadata_fingerprint") == original["canonical_metadata_fingerprint"]
            and receipt.get("original_source19_sha256") == original["original_source19_sha256"],
            "qualified proof original source/data identities differ")
    meta_bytes = measured_small(Path(dataset) / "meta.json", original["meta_sha256"])
    meta = json.loads(meta_bytes)
    require(sha(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode())
            == original["canonical_metadata_fingerprint"], "actual original metadata fingerprint changed")
    actual19 = {name: sha(safe_child(root, name).read_bytes()) for name in original["original_source19_sha256"]}
    require(actual19 == original["original_source19_sha256"], "original frozen19 source changed")
    adapter = receipt["adapter"]
    require(adapter["relative_path"] == gate["required_adapter_relative_path"], "wrong causal repair adapter")
    measured_small(safe_child(root, adapter["relative_path"]), adapter["sha256"])
    references = receipt.get("evidence_artifacts")
    require(isinstance(references, list) and references, "actual causal small materials absent")
    identities = [r["path"] for r in references]
    require(len(identities) == len(set(identities)), "duplicate original evidence identity")
    files = manifest.get("files")
    require(isinstance(files, dict) and set(files) == set(identities), "closed evidence mapping has missing/extra materials")
    roles = {r.get("role") for r in references}
    require({"whole_source_trigger_report", "clock_membership", "real_prefix_invariance"} <= roles,
            "whole-trigger, clock/membership or real prefix evidence missing")
    relative_names = [entry["relative_path"]] + [v["relative_path"] for v in files.values()]
    require(len(relative_names) == len(set(relative_names)), "transport aliases distinct original identities")
    observations, material_docs = [], {}
    for ref in references:
        transported = files[ref["path"]]
        require(transported["sha256"] == ref["sha256"] and transported["bytes"] == ref["bytes"],
                "mapping changed original artifact identity")
        path = safe_child(directory, transported["relative_path"])
        raw = measured_small(path, ref["sha256"], ref["bytes"])
        if ref["role"] == "real_prefix_invariance":
            require(ref["role"] not in material_docs, "duplicate real prefix receipt")
            material_docs[ref["role"]] = json.loads(raw)
        observations.append({"original_identity_path": ref["path"], "actual_transport_path": str(path),
                             "sha256": ref["sha256"], "bytes": ref["bytes"], "role": ref["role"]})
    # Interpret the actual verifier's sealed schema; do not rewrite its original
    # receipt into our draft field names or invent a four-case summary.
    real = material_docs["real_prefix_invariance"]
    require(real.get("status") == "PASS_BOUNDED_REAL_HOUR_VALUES_QUALITY_AND_ACTUAL_80H_ELIGIBILITY_FUTURE_INVARIANCE",
            "real prefix verifier did not pass")
    require(type(real.get("past_rows_compared")) is int and real["past_rows_compared"] >= 80
            and real.get("current80hour_window_genuinely_complete_and_current_member") is True,
            "no actual complete eighty-hour member prefix")
    require(real.get("adapter", {}).get("sha256") == adapter["sha256"], "real verifier used a different adapter")
    wanted = {"normal_future_append", "normal_future_prices", "future_conflicting_duplicate",
              "future_datetime_mismatch", "future_dual_clock_wrong_past_stamp", "future_factor_change_consistent_qfq"}
    cases = real.get("real_case_results")
    require(isinstance(cases, list) and len(cases) == 6 and {c.get("case") for c in cases} == wanted
            and set(receipt.get("real_prefix_case_names", [])) == wanted, "six actual prefix observations missing/duplicated")
    baseline = [x for x in real["material_artifacts"] if x["role"] == "actual_hour_prefix_baseline"]
    require(len(baseline) == 1, "actual prefix baseline missing")
    for name, ref in [("baseline", baseline[0])] + [(c["case"], r) for c in cases for r in c["artifacts"]]:
        mapped = files.get(ref["path"])
        require(mapped is not None and mapped["sha256"] == ref["sha256"] and mapped["bytes"] == ref["bytes"],
                "real prefix material not exactly transported: " + name)
    for observed in cases:
        require(all(observed.get(k) is True for k in ("original_genuine_stockday",
            "counterfactual_not_training_or_market_data", "past84_hour24_values_equal_including_NaNs",
            "finite_masks_equal", "hour_complete_equal", "80hour_eligibility_equal",
            "current_real_historical_member_and_80complete", "past_hour_quality_equal")),
                "real prefix values/missingness/completeness invariance failed: " + observed["case"])
        output = [r for r in observed["artifacts"] if r["role"] == "actual_hour_prefix_result"]
        require(len(output) == 1 and output[0]["sha256"] == baseline[0]["sha256"],
                "actual saved prefix bytes differ from baseline")
    return {"causal_receipt_sha256": expected_receipt_sha256,
            "causal_transport_manifest_sha256": expected_manifest_sha256,
            "actual_small_transport_observations": observations,
            "adapter_sha256": adapter["sha256"], "actual19_source_sha256": actual19,
            "original_meta_sha256": sha(meta_bytes), "protocol_sha256": PROTOCOL_SHA256,
            "runtime_mode": "original_frozen_panel_no_regeneration", "fixed_panel_rebuilt": False,
            "large_original_source_hashes_inherited_not_remotely_remeasured": True,
            "whole_score_causality_certified": False, "permission_scope": gate["required_scope"]}
