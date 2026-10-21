"""Read-only evidence pack for an existing selector, BLO final or ablation.

Never trains, infers, changes selection, loads panel arrays or reads test scores.
Source guards and exact checkpoint bytes are checked before and after collection.
Missing output prints pending; an invalid existing output fails without a bundle.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from zoneinfo import ZoneInfo
from .completed_stage_contract import (SEEDS, VARIANTS, FROZEN_SOURCE_NAMES, PendingEvidence, require,
    signature, validate_blo_trace, validate_blo_checkpoint, validate_weights,
    validate_selection, validate_fixed_trace, validation_records)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def target(stage, seed, label=None, variant=None):
    require(seed in SEEDS, "seed outside frozen roster")
    if stage == "selector":
        require(label is None and variant is None, "selector cannot have label/variant")
        return Path("blo") / f"seed_{seed}"
    require(type(label) is int and label in (1, 2, 3, 4), "existing decision must provide a valid label")
    if stage == "blo_final":
        require(variant is None, "BLO final is full input")
        return Path("blo_final") / f"label_{label}" / f"seed_{seed}"
    require(stage == "ablation" and variant in VARIANTS, "invalid stage/ablation variant")
    return Path("ablations") / variant / f"seed_{seed}"


def cpu_checkpoint(blob, expected, groups, n_industries, result, selector_trace=None):
    # Exact hash/source guards are established by collect() before this load.
    import torch
    from .model import PlanGRU
    torch.set_num_threads(1)
    state = torch.load(io.BytesIO(blob), map_location="cpu", weights_only=False)
    require(state["signature"] == expected, "checkpoint signature mismatch")
    require(state["groups"] == groups and state["n_industries"] == n_industries, "checkpoint group/industry mismatch")
    variant = "full" if selector_trace else expected["variant"]
    model = PlanGRU(groups, n_industries, variant)
    model.load_state_dict(state["model"], strict=True)
    require(all(torch.is_tensor(v) and v.dtype == torch.float32 and v.device.type == "cpu" and bool(torch.isfinite(v).all()) for v in state["model"].values()), "model tensors must be finite FP32 CPU")
    for value in model.parameters():
        require(bool(torch.isfinite(value).all()), "nonfinite parameter after strict load")
    extra = {}
    if selector_trace:
        logits = state["logits"]
        require(torch.is_tensor(logits) and logits.dtype == torch.float32 and tuple(logits.shape) == (4,) and bool(torch.isfinite(logits).all()), "invalid selector logits tensor")
        opt = state["lambda_optimizer"]
        require(len(opt["state"]) == 1 and len(opt["param_groups"]) == 1 and len(opt["param_groups"][0]["params"]) == 1, "lambda Adam parameter identity mismatch")
        parameter_id = opt["param_groups"][0]["params"][0]
        require(type(parameter_id) is int and list(opt["state"]) == [parameter_id]
                and type(next(iter(opt["state"]))) is int, "lambda Adam state key differs from unique parameter ID")
        slot = opt["state"][parameter_id]
        require({"step", "exp_avg", "exp_avg_sq"} <= set(slot), "lambda Adam required moments or step missing")
        for name in ("exp_avg", "exp_avg_sq"):
            moment = slot[name]
            require(torch.is_tensor(moment) and moment.dtype == logits.dtype == torch.float32
                    and tuple(moment.shape) == tuple(logits.shape) == (4,)
                    and moment.device.type == "cpu" and bool(torch.isfinite(moment).all()),
                    "lambda Adam moments must be finite FP32(4,) matching logits")
        step = slot["step"]
        require(torch.is_tensor(step) and step.ndim == 0 and bool(torch.isfinite(step))
                and float(step) == 2000, "lambda Adam step must be scalar2000")
        require(all(bool(torch.isfinite(v).all()) for v in slot.values() if torch.is_tensor(v)), "nonfinite lambda Adam state")
        steps = [float(step)]
        extra = validate_blo_checkpoint({**state, "logits": logits.tolist(), "lambda_optimizer_steps": steps}, selector_trace, expected, groups, n_industries)
        extra["lambda_optimizer_state_evidence"] = {
            "parameter_id": parameter_id, "state_key_exact_parameter_id": True,
            "moments_dtype": str(logits.dtype), "moments_shape": list(logits.shape),
            "step_shape": list(step.shape), "step": float(step),
            "all_state_keys": list(slot),
            "extra_keys_preserved_in_original_checkpoint": [k for k in slot if k not in ("step", "exp_avg", "exp_avg_sq")],
            "original_checkpoint_bytes_preserved_without_optimizer_sanitization": True}
    else:
        require(state["epoch"] + 1 == state["best_epoch"] + 1 == result["best_epoch"], "best checkpoint epoch mismatch")
        require(state["best_ic"] == result["best_validation_ic"], "checkpoint best IC mismatch")
    return {"signature_matches_result": True, "strict_state_dict_load_passed": True,
        "all_model_tensors_finite_fp32_cpu": True, "model_tensor_count": len(state["model"]),
        "parameter_count": sum(p.numel() for p in model.parameters()), "variant": variant,
        "tensor_shapes": {k: list(v.shape) for k, v in state["model"].items()},
        "groups_match_dataset_meta": True, "n_industries": n_industries,
        "checkpoint_sha256": sha(blob), "torch_version": torch.__version__,
        "inference_performed": False, "selector_checkpoint_trace": extra}


def audit_boundaries(audit, fingerprint):
    require(audit["dataset_fingerprint"] == fingerprint, "dataset_audit fingerprint mismatch")
    ranges = {"inner": (2019, 2022), "outer": (2023, 2023), "train": (2019, 2023), "validation": (2024, 2024)}
    observed = {}
    for name, (start, end) in ranges.items():
        item = audit["splits"][name]
        require(item["eligible_sections"] > 0, "no eligible audited split")
        dates = [datetime.fromtimestamp(item[k] / 1e9, ZoneInfo("Asia/Shanghai")).date().isoformat() for k in ("first_time_ns", "last_time_ns")]
        require(start <= int(dates[0][:4]) <= int(dates[1][:4]) <= end, "dataset audit temporal boundary mismatch")
        observed[name] = {"first_date": dates[0], "last_date": dates[1], "eligible_sections": item["eligible_sections"]}
    return {"existing_dataset_audit_boundaries": observed,
            "step_sample_times_directly_observed": False,
            "scope": "Aggregate audited split bounds plus frozen source execution path, not individual BLO sample-time provenance."}


def collect(args):
    root, formal = args.root.resolve(), args.formal.resolve()
    helper = Path(__file__).resolve(); contract = helper.with_name("completed_stage_contract.py")
    require(root == helper.parent.parent, "guarded root must be the actual imported package checkout")
    helper_sha, contract_sha = sha(helper.read_bytes()), sha(contract.read_bytes())
    require(helper_sha == args.expected_collector_sha256 and contract_sha == args.expected_contract_sha256, "new helper source identity mismatch")
    guard_bytes = args.guard_receipt.read_bytes()
    require(sha(guard_bytes) == args.expected_guard_receipt_sha256, "guard receipt identity mismatch")
    document = json.loads(guard_bytes); expected = dict(document["source_guards_sha256"])
    if len(expected) == 18:
        expected["gru_research/prospective_features.py"] = document["files_sha256"]["gru_research/prospective_features.py"]
    require(set(expected) == FROZEN_SOURCE_NAMES, "exact19 frozen source identities required")
    for name in expected:
        p = Path(name); require(not p.is_absolute() and ".." not in p.parts, "unsafe guard path")
    def guards():
        actual = {p: sha((root / p).read_bytes()) for p in expected}
        require(actual == expected, "frozen19 source changed")
        return actual
    before = guards()
    meta_bytes = (args.dataset / "meta.json").read_bytes()
    require(sha(meta_bytes) == args.expected_meta_sha256, "metadata byte identity mismatch")
    meta = json.loads(meta_bytes)
    require(meta.get("production_ready") is True and meta.get("causality_audit", {}).get("passed") is True and meta.get("raw_market_data_is_synthetic") is False, "production audited real panel required")
    require(len(meta["files_sha256"]) == 26, "26 declared data identities required")
    fingerprint = sha(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode())
    config = json.loads((root / "gru_research/config.json").read_bytes())
    require(config["seeds"] == SEEDS and config["dtype"] == "float32", "frozen seed/dtype contract changed")
    payload, source_bytes = {}, {}
    def read(relative):
        relative = Path(relative)
        require(not relative.is_absolute() and ".." not in relative.parts, "unsafe output path")
        p = formal / relative
        if not p.is_file():
            raise PendingEvidence("required output missing: " + str(relative))
        blob = p.read_bytes(); payload["formal/" + relative.as_posix()] = blob; source_bytes[p] = blob
        return blob
    def json_read(relative):
        return json.loads(read(relative))
    require(json_read("run_config.json") == config, "actual run config differs from frozen source config")
    temporal = audit_boundaries(json_read("dataset_audit.json"), fingerprint)
    dependency_checks = {}
    label = None
    if args.stage != "selector":
        weights = json_read("blo_weights.json")
        selectors = {seed: json_read(Path("blo") / f"seed_{seed}" / "result.json") for seed in SEEDS}
        dependency_checks["five_seed_blo_weights"] = validate_weights(weights, selectors, config, fingerprint)
        label = weights["selected_label"]
        if args.stage == "ablation":
            selection = json_read("selection.json")
            candidates = {}
            for method in ("fixed_1", "fixed_2", "fixed_3", "fixed_4", "blo"):
                current_label = label if method == "blo" else int(method[-1])
                stem = Path("blo_final" if method == "blo" else "fixed") / f"label_{current_label}"
                candidates[method] = {s: json_read(stem / f"seed_{s}" / "result.json") for s in SEEDS}
            dependency_checks["existing2024_selection"] = validate_selection(selection, weights, candidates, config, fingerprint, formal)
            label = selection["selected_label"]
    relative = target(args.stage, args.seed, label, args.variant)
    result = json_read(relative / "result.json")
    if result.get("complete") is not True:
        raise PendingEvidence("target is not completed: " + str(relative))
    aliases = {"hour": "hour24", "daily": "daily16", "factors": "factors", "fund": "fund16", "ages": "ages2"}
    groups = {k: list(meta["groups"][k] if k in meta["groups"] else meta["groups"][a]) for k, a in aliases.items()}
    if args.stage == "selector":
        trace = validate_blo_trace(result, read(relative / "epochs.jsonl"), read(relative / "steps.jsonl"), args.seed, config, fingerprint)
        expected_signature = {"dataset_fingerprint": fingerprint, "config": config, "seed": args.seed, "mode": "blo"}
        checkpoint_bytes = read(relative / "last.pt")
        loaded = cpu_checkpoint(checkpoint_bytes, expected_signature, groups, meta["n_industries"], result, trace)
        target_events = trace["warm_events"] + trace["step_events"]
        trace_report = {k: v for k, v in trace.items() if k not in ("warm_events", "step_events")}
    else:
        variant = args.variant if args.stage == "ablation" else "full"
        trace = validate_fixed_trace(result, read(relative / "epochs.jsonl"), args.seed, label, variant, config, fingerprint, before["gru_research/train.py"])
        require(args.expected_validation_population_sha256 is not None, "accepted2024 population hash required for final model")
        require(args.expected_validation_scored_population_sha256 is not None, "accepted2024 scored population hash required for final model")
        validation = validation_records(json_read(relative / "best_validation_sections.json"), json_read(relative / "best_validation.json"), result["best_validation_ic"], args.expected_validation_population_sha256, args.expected_validation_scored_population_sha256)
        checkpoint_bytes = read(relative / "best.pt")
        require(sha(checkpoint_bytes) == result["best_checkpoint_sha256"], "best checkpoint bytes changed")
        loaded = cpu_checkpoint(checkpoint_bytes, result["signature"], groups, meta["n_industries"], result)
        target_events = trace["epoch_events"]
        trace_report = {k: v for k, v in trace.items() if k != "epoch_events"}
        trace_report["validation"] = validation
    raw_bytes = args.raw_log.read_bytes(); payload["remote_formal_progress.log"] = raw_bytes
    raw_rows = []
    lines = raw_bytes.splitlines(); partial = bool(raw_bytes and not raw_bytes.endswith(b"\n"))
    for line in lines[:-1] if partial else lines:
        if line.startswith(b"{"):
            row = json.loads(line)
            require(row.get("event") in ("remote_launch_verified", "gpu_preflight_passed", "epoch", "blo_warmup", "blo_step"), "raw has an unrecognized research event; do not collect evaluation output")
            raw_rows.append(row)
    if args.stage == "selector":
        seen = [r for r in raw_rows if r.get("event") in ("blo_warmup", "blo_step") and r.get("seed") == args.seed]
    else:
        seen = [r for r in raw_rows if r.get("event") == "epoch" and (r.get("label"), r.get("seed"), r.get("variant")) == (label, args.seed, variant)]
        # Fixed and full BLO final can share tuple identities. Target's last
        # occurrence is the final-model run; retain the raw ambiguity explicitly.
        if args.stage == "blo_final" and len(seen) > len(target_events):
            seen = seen[-len(target_events):]
    alignment = {"target_per_model_events": len(target_events), "raw_target_events_observed": len(seen),
        "target_events_equal_native_raw": seen == target_events, "raw_partial_tail": partial,
        "raw_copied_once_not_asserted_transactionally_simultaneous": True,
        "raw_tuple_can_repeat_fixed_and_blo_final": args.stage == "blo_final"}
    for p, blob in source_bytes.items():
        require(p.read_bytes() == blob, "completed/dependency output changed while collecting")
    require(guards() == before and (args.dataset / "meta.json").read_bytes() == meta_bytes, "frozen data metadata/source changed")
    require(args.guard_receipt.read_bytes() == guard_bytes and sha(helper.read_bytes()) == helper_sha and sha(contract.read_bytes()) == contract_sha, "collector/guard document changed")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        squeue = subprocess.check_output(["squeue", "-j", "405900,405950", "-h", "-o", "%i %T %M %R"], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        squeue = "unavailable: " + type(exc).__name__
    receipt = {"status": "COMPLETE_TARGET_EVIDENCE_COLLECTED_WITH_ACTUAL_CPU_STRICT_LOAD", "observed_utc": stamp,
        "stage": args.stage, "new_completed_model": str(relative), "new_completed_result": result,
        "actual_server_load": loaded, "actual_trace_contract": trace_report, "dependency_checks": dependency_checks,
        "frozen_temporal_execution_path_evidence": temporal, "source_guards_sha256": before,
        "source_guards_verified_before_and_after": 19, "guard_document_sha256": sha(guard_bytes),
        "new_helper_source_sha256": {helper.name: helper_sha, contract.name: contract_sha},
        "dataset_meta_sha256": sha(meta_bytes), "dataset_fingerprint": fingerprint,
        "dataset_files_declared_sha256": meta["files_sha256"], "dataset_bytes_rehashed_this_check": False,
        "raw_trace_alignment": alignment, "squeue_at_collection_only": squeue,
        "artifact_sha256": {name: sha(blob) for name, blob in payload.items()},
        "stepwise_sample_times_directly_observed": False, "old_checkpoints_loaded": False,
        "test_or_forward_scores_labels_holdings_read": False, "training_or_inference_performed": False,
        "existing_selection_written_or_tuned": False, "full_PLAN_complete": False}
    name = f"completed_stage_receipt_{stamp}.json"
    payload[name] = json.dumps(receipt, indent=2, allow_nan=False).encode()
    output = args.output_dir / f"gru_completed_{args.stage}_{args.seed}_{stamp}.tar.gz"
    with tarfile.open(output, "x:gz") as tar:
        for name, blob in payload.items():
            member = tarfile.TarInfo(name); member.size = len(blob); tar.addfile(member, io.BytesIO(blob))
    return {"status": receipt["status"], "archive": str(output), "archive_bytes": output.stat().st_size,
        "archive_sha256": sha(output.read_bytes()), "stage": args.stage, "target": str(relative),
        "actual_cpu_strict_load": True, "raw_trace_alignment": alignment, "full_PLAN_complete": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", choices=("selector", "blo_final", "ablation"), required=True)
    p.add_argument("--seed", type=int, choices=SEEDS, required=True)
    p.add_argument("--variant", choices=VARIANTS)
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    p.add_argument("--formal", type=Path, required=True)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--raw-log", type=Path, required=True)
    p.add_argument("--guard-receipt", type=Path, required=True)
    p.add_argument("--expected-guard-receipt-sha256", required=True)
    p.add_argument("--expected-meta-sha256", required=True)
    p.add_argument("--expected-collector-sha256", required=True)
    p.add_argument("--expected-contract-sha256", required=True)
    p.add_argument("--expected-validation-population-sha256")
    p.add_argument("--expected-validation-scored-population-sha256")
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    require(args.variant is None if args.stage != "ablation" else args.variant in VARIANTS, "variant allowed only for ablation")
    try:
        report = collect(args)
    except PendingEvidence as exc:
        report = {"status": "pending_actual_completed_output", "reason": str(exc),
                  "actual_cpu_strict_load": False, "archive_created": False, "full_PLAN_complete": False}
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
