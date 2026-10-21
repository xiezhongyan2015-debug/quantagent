"""Bind fixed35 metadata into disabled root-review candidates; stdlib only.

No native file, checkpoint, array, runtime binary, or model body is opened.
Every candidate remains disabled. A separate root decision and new immutable
contracts are required before either the CPU auditor or its owned capture runs.
Default reports the contract without reading inputs or creating directories.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time

AUDITOR_SHA = "70c7b53c2b7f1c69507d63156fb326f1ad3cd5cdb5dfbe1ce0d2c6ced8aa46a6"
CAPTURE_SHA = "939431f14a528eaedf3a6852e1871865675369b2f23d10f3a7785e39edec428f"
AUDIT_TEMPLATE_SHA = "9e2f73298df695261ab4188bb6a40469652a89ca2ced339245ab9855c11bb76b"
OWNED_TEMPLATE_SHA = "840a7e07da3044b36fee5f45b8ecb0194bd1dd372dd824bad364bf927853ec85"
PROTOCOL_SHA = "e8d5f6a15bba4496ab1895c9d9b781340acc1ef90782440a0b6c23822384205f"
MANIFEST_SHA = "8a7c855374b7237044639b1b0610b25a0843f73b6393ec3555270dfa1d9eb809"
META_SHA = "2e144ad60a9aba1e258ef7ff114bf538ed22f6307e368b378b425557793a2849"
META_FP = "6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4"
RUNTIME_PATH = "/opt/network/anaconda3/envs/pytorch/bin/python3.11"
RUNTIME_BYTES = 25508680
RUNTIME_SHA = "20796b36a60bf97771fb16379100b2c0acc7e27d234250f67259aac9e515d7f1"
SEEDS = (11, 22, 33, 44, 55)
FOLDS = ("2021", "2022", "2023")
ARMS = ("control", "lower_lr")
SMALL_CAP = 128 * 1024**2
CP_CAP = 160 * 1024**2
FILE_CAP = 4 * 1024**2
READ_CAP = 24 * 1024**2
READ_CHUNK = 65536
COMPLETION_STATUS = "COMPLETE_R2_30_DIAGNOSTICS_5_FINAL_FREEZE_NATIVE_EVIDENCE_BOUND"
ROOT_ACCEPTANCE_STATUS = "ROOT_ACCEPTED_ACTUAL_FIXED35_NATIVE_COMPLETION_FOR_DISABLED_CONTRACT_BINDING"
INPUT_ROLES = {"native_capture", "root_independent_completion_acceptance", "runtime_acceptance",
               "audit_template", "owned_capture_template"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def encoded(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def identity(path, body):
    return {"path": str(path), "bytes": len(body), "sha256": digest(body)}


def absolute(value):
    require(type(value) is str and value.startswith("/"), "explicit absolute identity path required")
    p = PurePosixPath(value)
    require(str(p) == value and ".." not in p.parts, "noncanonical or unsafe identity path")
    return p


def ref_identity(ref, cap=FILE_CAP):
    require(type(ref) is dict, "closed reference object required")
    absolute(ref["path"])
    require(type(ref["bytes"]) is int and 0 < ref["bytes"] <= cap, "positive bounded reference size required")
    require(type(ref["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", ref["sha256"]), "actual SHA required")
    return {k: ref[k] for k in ("path", "bytes", "sha256")}


def stat(path):
    s = Path(path).stat()
    return {"path": str(Path(path).resolve()), "bytes": s.st_size, "mtime_ns": s.st_mtime_ns,
            "inode": s.st_ino, "device": s.st_dev}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate metadata JSON key")
        result[key] = value
    return result


def read_json(ref, observations, deadline):
    ref = ref_identity(ref)
    require(sum(o["bytes_read"] for o in observations) + ref["bytes"] <= READ_CAP,
            "binder metadata body budget exceeded")
    require(time.monotonic() < deadline, "binder cooperative metadata deadline exceeded")
    p = Path(ref["path"])
    before = stat(p)
    require(before["bytes"] == ref["bytes"], "pinned metadata size differs")
    row = {**ref, "STAT_before": before, "bytes_read": 0, "EOF_verified": False}
    observations.append(row)
    parts, h = [], hashlib.sha256()
    try:
        with p.open("rb") as handle:
            remaining = ref["bytes"]
            while remaining:
                require(time.monotonic() < deadline, "binder cooperative metadata deadline exceeded")
                b = handle.read(min(READ_CHUNK, remaining))
                require(b, "truncated metadata input")
                parts.append(b)
                h.update(b)
                row["bytes_read"] += len(b)
                remaining -= len(b)
            require(handle.read(1) == b"", "metadata input grew beyond pinned size")
        row.update(actual_sha256=h.hexdigest(), STAT_after=stat(p), EOF_verified=True)
        require(row["STAT_after"] == before and h.hexdigest() == ref["sha256"], "metadata SHA/STAT changed")
        return json.loads(b"".join(parts), object_pairs_hook=unique_object)
    finally:
        row["partial_or_complete_sha256"] = h.hexdigest()


def rosters():
    native = {"run_identity.json", "freeze.json", "selection.json", "budget.json"}
    cp = set()
    for fold in FOLDS:
        for arm in ARMS:
            for seed in SEEDS:
                d = f"diagnostic/{fold}/{arm}/seed_{seed}"
                native.update(d + "/" + n for n in ("identity.json", "result.json", "epochs.jsonl",
                              "outer_attempt.json", "outer_result.json", "best_inner_summary.json", "best_inner_sections.json"))
                cp.add(d + "/best_inner.pt")
    for seed in SEEDS:
        d = f"final/seed_{seed}"
        native.update(d + "/" + n for n in ("identity.json", "result.json", "epochs.jsonl"))
        cp.add(d + "/last.pt")
    require(len(native) == 229 and len(cp) == 35, "fixed roster construction differs")
    return native, cp


def bind_materials(captured, template):
    native_names, cp_names = rosters()
    require(set(captured["native_files"]) == native_names and set(captured["checkpoints"]) == cp_names,
            "capture is not the exact229 native and35 checkpoint roster")
    require(captured["original_source_root"] == template["original_source_root"]
            and captured["original_run_directory"] == template["original_run_directory"], "original roots differ")
    original = absolute(template["original_run_directory"])
    common_roots, outputs = set(), []
    for key, names, cap in (("native_files", native_names, FILE_CAP), ("checkpoints", cp_names, 8 * 1024**2)):
        mapped = {}
        for relative in sorted(names):
            ref = captured[key][relative]
            rid = ref_identity(ref, cap)
            require(ref["identity_path"] == str(original / relative), "original native relative identity differs")
            suffix = "/" + relative
            require(rid["path"].endswith(suffix), "copy relative path differs")
            root = rid["path"][:-len(suffix)]
            absolute(root)
            require(root == str(original) and rid["path"] == ref["identity_path"],
                    "same-server capture must retain the exact original native path; no copies")
            common_roots.add(root)
            require(ref["source_STAT_before"] == ref["source_STAT_after"]
                    and ref["source_STAT_before"]["bytes"] == rid["bytes"]
                    and ref["source_STAT_before"]["path"] == ref["identity_path"]
                    and ref["EOF_verified"] is True and ref["single_physical_source_pass"] is True,
                    "capture did not attest one complete unchanged original source pass")
            mapped[relative] = {**rid, "identity_path": ref["identity_path"]}
        outputs.append(mapped)
    require(len(common_roots) == 1, "multiple actual capture roots")
    require(sum(v["bytes"] for v in outputs[1].values()) <= CP_CAP, "35CP total body cap exceeded")
    require(sum(v["bytes"] for v in outputs[0].values()) <= SMALL_CAP, "229 native body cap exceeded")
    return common_roots.pop(), outputs[0], outputs[1]


def check_sources(captured, template):
    require(set(template["source_refs"]) == {"protocol", "runtime_manifest", "meta", "config"}
            and len(template["source27"]) == 27, "prepared source roster differs")
    for key in ("source_refs", "source27"):
        require(set(captured[key]) == set(template[key]), "captured source roster differs")
        require({k: ref_identity(v) for k, v in captured[key].items()}
                == {k: ref_identity(v) for k, v in template[key].items()}, "source bytes/SHA/path binding differs")
    require(template["source_refs"]["protocol"]["sha256"] == PROTOCOL_SHA
            and template["source_refs"]["runtime_manifest"]["sha256"] == MANIFEST_SHA
            and template["source_refs"]["meta"]["sha256"] == META_SHA, "protocol/manifest/meta pins differ")
    root = absolute(template["original_source_root"])
    for relative, ref in template["source27"].items():
        require(str(root / relative) == ref["path"] and ".." not in PurePosixPath(relative).parts,
                "source27 root/relative identity differs")


def audit_budget(audit, contract_bytes):
    # Physical source-body reads, deliberately preserving duplicate identities.
    sums = {"auditor_source": audit["source"]["bytes"],
            "source_refs4": sum(x["bytes"] for x in audit["source_refs"].values()),
            "source27": sum(x["bytes"] for x in audit["source27"].values()),
            "root_completion": audit["root_completion_receipt"]["bytes"],
            "native229": sum(x["bytes"] for x in audit["native_files"].values())}
    repeated = sums["auditor_source"] + sums["source_refs4"] + sums["source27"] + sums["root_completion"]
    pre = contract_bytes + repeated + sums["native229"]
    complete = contract_bytes + 2 * repeated + sums["native229"]
    cp = sum(x["bytes"] for x in audit["checkpoints"].values())
    require(complete <= SMALL_CAP and cp <= CP_CAP, "complete audit physical-read budget exceeded")
    return {"scope": "auditor source-body bytes; not unique bytes, not OS IO, not wrapper or Torch library IO",
            "audit_contract_reads": 1, "audit_contract_bytes": contract_bytes,
            "auditor_source_reads": 2, "source_refs_each_reads": 2, "source27_each_reads": 2,
            "root_completion_reads": 2, "native_each_reads": 1, "checkpoint_each_reads": 1,
            "per_role_single_pass_bytes": sums, "gate_small_body_bytes": pre,
            "postguard_small_body_bytes": repeated, "complete_small_body_bytes": complete,
            "complete_small_body_cap": SMALL_CAP, "checkpoint_body_bytes_separate": cp,
            "checkpoint_per_file_cap": 8 * 1024**2, "checkpoint_total_cap": CP_CAP,
            "protocol_config_overlap_deliberately_counted_twice_within_each_small_guard": True,
            "native_and_checkpoint_postguard": "STAT only; no second body read",
            "candidate_bytes_only_recompute_after_any_root_resigning": True, "permission_granted": False}


def settle_budget(audit):
    # The encoded size includes this budget; a bounded decimal-width fixed point.
    audit["planned_auditor_body_read_budget"] = {}
    for _ in range(12):
        size = len(encoded(audit))
        audit["planned_auditor_body_read_budget"] = audit_budget(audit, size)
        if len(encoded(audit)) == size:
            return encoded(audit)
    raise ValueError("candidate contract serialized-byte budget failed to converge")


def prepared():
    return {"schema_version": "R2_FIXED35_METADATA_BINDER_V1", "status": "PREPARED_ONLY_ACTUAL35_INPUTS_PENDING",
            "candidate_generation_allowed": False, "execution_allowed": False,
            "required_input_roles": sorted(INPUT_ROLES), "native_count": 229, "checkpoint_count": 35,
            "checkpoint_native_array_runtime_body_reads": 0, "Torch_imports": 0,
            "all35_finite_parameters_verified": False, "root_completion_granted": False,
            "full_PLAN_complete": False, "economic_acceptance": False, "automatic_retry": False}


def write_exclusive(path, body):
    with path.open("xb") as handle:
        handle.write(body)


def generate(args):
    require(args.input_contract and args.input_contract_sha256 and args.source_sha256 and args.output,
            "five explicit disabled-candidate generation arguments required")
    require(re.fullmatch("[0-9a-f]{64}", args.input_contract_sha256)
            and re.fullmatch("[0-9a-f]{64}", args.source_sha256), "explicit out-of-band SHA pins required")
    observations = []
    started = time.monotonic()
    deadline = started + 60
    input_ref = {"path": str(args.input_contract.resolve()), "bytes": args.input_contract.stat().st_size,
                 "sha256": args.input_contract_sha256}
    control = read_json(input_ref, observations, deadline)
    require(control["schema_version"] == "ROOT_FIXED35_DISABLED_BINDER_INPUT_V1"
            and control["candidate_generation_allowed"] is True and control["execution_allowed"] is False,
            "only explicit root-authorized disabled-candidate generation allowed")
    own = Path(__file__).resolve()
    own_ref = ref_identity(control["binder_source"], 1024**2)
    require(own_ref["path"] == str(own) and own_ref["sha256"] == args.source_sha256,
            "binder source out-of-band identity differs")
    # Source is bytes-only metadata; never imports this or any other module.
    before = stat(own)
    require(before["bytes"] == own_ref["bytes"] and 0 < before["bytes"] <= 1024**2,
            "binder source pre-read size differs or exceeds bounded cap")
    with own.open("rb") as handle:
        body = handle.read(own_ref["bytes"])
        require(len(body) == own_ref["bytes"] and handle.read(1) == b"",
                "binder source truncated or grew beyond declared bounded bytes")
    require(digest(body) == own_ref["sha256"] and stat(own) == before,
            "binder source identity changed")
    require(set(control["inputs"]) == INPUT_ROLES, "closed input-role roster required")
    require(control["exact_new_output_directory"] == str(args.output.resolve())
            and args.output.is_absolute() and not args.output.exists(), "exclusive append-only output required")
    args.output.mkdir(parents=True, exist_ok=False)
    write_exclusive(args.output / "CANDIDATE_GENERATION_STARTED_v1.json", encoded({
        "status": "METADATA_ONLY_STARTED_NO_AUDIT_EXECUTION", "PID": os.getpid(),
        "input_contract_sha256": args.input_contract_sha256, "source_sha256": args.source_sha256,
        "execution_allowed": False, "automatic_retry": False}))
    result, artifacts, failure = prepared(), [], None
    try:
        docs = {k: read_json(v, observations, deadline) for k, v in control["inputs"].items()}
        cap = docs["native_capture"]
        root = docs["root_independent_completion_acceptance"]
        runtime = docs["runtime_acceptance"]
        audit = copy.deepcopy(docs["audit_template"])
        launch = copy.deepcopy(docs["owned_capture_template"])
        require(control["inputs"]["audit_template"]["sha256"] == AUDIT_TEMPLATE_SHA
                and control["inputs"]["owned_capture_template"]["sha256"] == OWNED_TEMPLATE_SHA,
                "old immutable prepared template identity differs")
        require(str(args.output) == str(absolute(audit["original_source_root"]) / "outputs/r2_fixed35_disabled_binding_v1/attempt1"),
                "fixed independent candidate output namespace required")
        require(audit["execution_allowed"] is False and launch["execution_allowed"] is False
                and audit["source"]["sha256"] == AUDITOR_SHA and audit["source"]["bytes"] == 29622
                and launch["capture_source_sha256"] == CAPTURE_SHA, "auditor/capture fixed-source contract differs")
        require(cap["schema_version"] == "ACTUAL_R2_NATIVE35_CAPTURE_V1"
                and cap["status"] == "COMPLETE_FIXED35_NATIVE_CAPTURE_NOT_ROOT_ACCEPTANCE"
                and cap["generation_complete"] is True and cap["root_accepted"] is False
                and cap["all35_finite_parameters_verified"] is False, "capture completion is not root/finite acceptance")
        require(all(type(control[k]) is str and re.fullmatch("[0-9a-f]{64}", control[k])
                    for k in ("expected_native_capture_source_sha256", "expected_native_capture_contract_sha256")),
                "actual native capture source/contract out-of-band SHA required")
        require(cap["source"]["sha256"] == control["expected_native_capture_source_sha256"]
                and cap["contract"]["sha256"] == control["expected_native_capture_contract_sha256"],
                "native capture source or original acquisition contract not independently pinned")
        require(root["schema_version"] == "ROOT_FIXED35_NATIVE_CAPTURE_COMPLETION_ACCEPTANCE_V1"
                and root["status"] == ROOT_ACCEPTANCE_STATUS
                and root["capture_receipt_sha256"] == control["inputs"]["native_capture"]["sha256"]
                and root["native_materials_independently_verified"] is True
                and root["all35_finite_parameters_verified"] is False
                and root["audit_execution_allowed"] is False, "separate actual root acceptance missing or wrong scope")
        require(all(type(root[k]) is int for k in ("diagnostic_complete_count", "outer_once_complete_count", "final_complete_count"))
                and (root["diagnostic_complete_count"], root["outer_once_complete_count"], root["final_complete_count"]) == (30, 30, 5),
                "root independently accepted exact30/30/5 required")
        check_sources(cap, audit)
        actual_root, native, checkpoints = bind_materials(cap, audit)
        native_hashes = {k: v["sha256"] for k, v in native.items()}
        cp_hashes = {k: v["sha256"] for k, v in checkpoints.items()}
        require(root["native_files_sha256"] == native_hashes and root["checkpoint_hashes"] == cp_hashes
                and root["freeze_sha256"] == cap["freeze_sha256"] == native_hashes["freeze.json"],
                "root/capture exact229/35/freeze identity closure differs")
        require(cap["metadata_completed"] == {"diagnostic": 30, "outer_once": 30, "final": 5},
                "captured lexical completion counts differ")
        expected_binding = {"protocol_sha256": PROTOCOL_SHA, "new_source_manifest_sha256": MANIFEST_SHA,
                            "causal_receipt_sha256": "95370519b5974c6724cef641506eeb113b4d33fc76fdb575182f7783fe2ca3cc",
                            "causal_transport_manifest_sha256": "685c131667613b4a6d7fc2ff49713d889faadd3590ebaf986e624bd1b718dc9c",
                            "dataset_fingerprint": META_FP}
        require(cap["run_identity"] == expected_binding, "actual run signature metadata differs")
        require(runtime["status"] == "ROOT_ACCEPTED_ACTUAL_CPU_RUNTIME_API_PROBE_ONLY_NOT_CHECKPOINT_FORMAT_OR_AUDIT"
                and runtime["runtime"]["path"] == RUNTIME_PATH and runtime["runtime"]["bytes"] == RUNTIME_BYTES
                and runtime["runtime"]["sha256"] == RUNTIME_SHA
                and runtime["scope"]["python_runtime"] == runtime["runtime"]
                and runtime["scope"]["python_version"].startswith("3.11.0")
                and runtime["scope"]["actual_checkpoint_format_compatibility_proven"] is False,
                "actual accepted3.11 runtime identity/API scope differs")
        completion = {"schema_version": "DISABLED_ROOT_FIXED35_COMPLETION_CANDIDATE_V1",
                      "status": "CANDIDATE_NOT_ROOT_COMPLETION", "intended_auditor_status": COMPLETION_STATUS,
                      "execution_allowed": False, "diagnostic_complete_count": 30, "outer_once_complete_count": 30,
                      "final_complete_count": 5, "native_files_sha256": native_hashes,
                      "checkpoint_hashes": cp_hashes, "freeze_sha256": native_hashes["freeze.json"],
                      "external_actual_root_acceptance": control["inputs"]["root_independent_completion_acceptance"],
                      "external_actual_capture": control["inputs"]["native_capture"],
                      "all35_finite_parameters_verified": False, "full_PLAN_complete": False,
                      "scope": "metadata closure only; root must independently issue a new completion receipt; this status fails auditor gate"}
        cp_path = args.output / "root_completion_candidate_disabled_v1.json"
        cb = encoded(completion)
        write_exclusive(cp_path, cb)
        completion_ref = identity(cp_path, cb)
        artifacts.append({"role": "completion_candidate", **completion_ref})
        audit.update(status="CANDIDATE_DISABLED_ACTUAL_NATIVE_METADATA_BOUND_NOT_ROOT_PERMISSION",
                     execution_allowed=False, actual_native_run_directory=actual_root,
                     native_files=native, checkpoints=checkpoints, root_completion_receipt=completion_ref,
                     external_root_actual_acceptance=control["inputs"]["root_independent_completion_acceptance"],
                     metadata_only_binding=True, actual_checkpoint_count=None, actual_Torch_load_count=0,
                     full_PLAN_complete=False)
        ab = settle_budget(audit)
        audit_path = args.output / "checkpoint_audit_contract_candidate_disabled_v1.json"
        write_exclusive(audit_path, ab)
        audit_ref = identity(audit_path, ab)
        artifacts.append({"role": "audit_contract_candidate", **audit_ref})
        launch.update(status="CANDIDATE_DISABLED_ACTUAL_METADATA_BOUND_NOT_ROOT_PERMISSION", execution_allowed=False,
                      audit_contract=audit_ref, python_runtime=runtime["runtime"]["path"],
                      python_runtime_actual_identity=runtime["runtime"],
                      python_runtime_actual_current_binary_SHA_verified_by_binder=False,
                      required_actual_root_completion_receipt=completion_ref,
                      root_runtime_acceptance=control["inputs"]["runtime_acceptance"],
                      required_root_launch_environment={"CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1"},
                      launch_environment_actually_applied=False,
                      full_PLAN_complete=False, model_reuse_or_training_permission=False, economic_acceptance=False)
        require(launch["fixed_scope"] == {"diagnostic_best_inner": 30, "final_fixed_last": 5,
                    "model_forward_calls": 0, "additional_fits": 0}
                and launch["child_hard_deadline_seconds"] == 660
                and launch["expected_audit_output_directory"] == audit["exact_new_output_directory"],
                "owned capture unchanged fixed scope/deadline/namespace differs")
        launch_path = args.output / "owned_capture_launch_candidate_disabled_v1.json"
        lb = encoded(launch)
        write_exclusive(launch_path, lb)
        artifacts.append({"role": "owned_capture_candidate", **identity(launch_path, lb)})
        for ref, observed in zip([input_ref, *control["inputs"].values()], observations):
            require(stat(ref["path"]) == observed["STAT_before"], "metadata input changed before final sealing")
        require(stat(own) == before, "binder source STAT changed before final sealing")
        result.update(status="DISABLED_CANDIDATES_GENERATED_METADATA_ONLY_NOT_ROOT_PERMISSION",
                      candidate_generation_allowed=True, actual_native_file_bodies_read=0,
                      actual_checkpoint_bodies_read=0, actual_runtime_binary_bodies_read=0,
                      actual_array_bodies_read=0, source_sha256=args.source_sha256,
                      auditor_budget=audit["planned_auditor_body_read_budget"],
                      candidate_gate_blocks=["audit execution_allowed=false", "owned capture execution_allowed=false",
                                             "completion candidate status is not auditor COMPLETE status"],
                      actual35_independent_completion_acceptance_inherited_not_recomputed=True)
    except BaseException as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}
        result.update(status="FAILED_DISABLED_BINDING_PRESERVED_NO_RETRY", failure=failure)
    result.update(input_contract=input_ref, artifacts=artifacts, observations=observations,
                  binder_self_bytes_read=len(body), binder_metadata_read_cap=READ_CAP,
                  elapsed_seconds=time.monotonic()-started, finished_utc=datetime.now(timezone.utc).isoformat(),
                  execution_allowed=False, checkpoint_audit_executed=False, owned_capture_executed=False,
                  automatic_retry=False, full_PLAN_complete=False)
    write_exclusive(args.output / "disabled_candidate_binding_receipt_v1.json", encoded(result))
    print(json.dumps({"status": result["status"], "candidate_count": len(artifacts), "execution_allowed": False}))
    return 0 if failure is None else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-disabled-candidates", action="store_true")
    parser.add_argument("--input-contract", type=Path)
    parser.add_argument("--input-contract-sha256")
    parser.add_argument("--source-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.bind_disabled_candidates:
        print(json.dumps(prepared(), indent=2))
        return 0
    return generate(args)


if __name__ == "__main__":
    raise SystemExit(main())
