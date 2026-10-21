"""Capture one fixed R2 CPU export or comparison and bound its owned group.

Only a newly created child session may be terminated. This never contacts the
GPU job/process groups, reuses an attempt, removes outputs, or retries an action.
Invocation requires explicit pinned launch arguments; preparation is disabled.
Exit0/receipt presence does not certify reproduction, comparison or acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def require(value, reason):
    if not value:
        raise ValueError(reason)


def read_small(path, expected):
    p = Path(path)
    require(p.stat().st_size <= 4 * 1024**2, "closed capture input exceeds budget")
    b = p.read_bytes()
    require(hashlib.sha256(b).hexdigest() == expected, "capture input SHA differs")
    return b


def write_json(path, value):
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temporary, path)


def group_present(group):
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False


def terminate_owned(proc, group):
    require(group == proc.pid and group != os.getpgrp(), "refuse to signal a non-owned process group")
    if group_present(group):
        os.killpg(group, signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    if group_present(group):
        os.killpg(group, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return {"parent_reaped": False, "remaining_group_state": "UNKNOWN_AFTER_SIGKILL"}
    return {"parent_reaped": True, "remaining_group_state": "PRESENT_OR_ZOMBIE" if group_present(group) else "ABSENT"}


def execute(args):
    contract = json.loads(read_small(args.launch_contract, args.launch_contract_sha256))
    require(contract["schema_version"] == "R2_COMPARISON_OWNED_CAPTURE_LAUNCH_V1", "wrong launch schema")
    require(contract["execution_allowed"] is True, "prepared launch cannot execute")
    action = contract["action"]
    require(action in ("export", "compare"), "fixed export or compare action only")
    require(contract["fixed_scope"] == {"action": action, "final_fixed_last_models": 5,
            "additional_fits": 0, "model_selection": False, "wrapper_model_forward_calls": 0},
            "fixed frozen five-model research scope differs")
    read_small(Path(__file__).resolve(), contract["capture_source_sha256"])
    tool_sha = "91812ab4e2a804041675c77e1d9b62e99c2b1b3dd8ce333e583b84960c52a7de"
    manifest_sha = "98c031432478b57460fb39f859f7c6271d74855291f2b47020540dab262c4eca"
    runtime_seal_sha = "8a7c855374b7237044639b1b0610b25a0843f73b6393ec3555270dfa1d9eb809"
    protocol_sha = "b87a29808a478303472809968a59a07a4d409e00dca32de9ab1a8b58ff2b254b"
    init_sha = "68557239023bf3341c5355902f5d75d839a594eedfefb523c95a2661b89e49f5"
    auditor_sha = "70c7b53c2b7f1c69507d63156fb326f1ad3cd5cdb5dfbe1ce0d2c6ced8aa46a6"
    arguments = contract["closed_arguments"]
    keys = {"root", "dataset", "r2_run", "source_manifest", "r2_source_manifest", "output",
            "expected_source_manifest_sha256", "expected_r2_freeze_sha256", "r2_original_run_path"}
    if action == "compare":
        keys |= {"r1_formal", "r1_export_manifest", "export_directory", "expected_export_manifest_sha256"}
    require(type(arguments) is dict and set(arguments) == keys, "exact closed tool argument roster required")
    for key, value in arguments.items():
        require(type(value) is str, "actual explicit argument required; null placeholders cannot execute")
        if key.endswith("sha256"):
            require(len(value) == 64 and all(c in "0123456789abcdef" for c in value), "actual SHA argument required")
        else:
            p = Path(value)
            require(p.is_absolute() and str(p.resolve()) == value, "actual canonical absolute path argument required")
    root = Path(arguments["root"])
    require(root.is_dir() and contract["cwd"] == str(root), "exact existing source cwd required for -m imports")
    require(arguments["r2_run"] == arguments["r2_original_run_path"], "same-server original R2 run required")
    tool = contract["tool"]
    require(tool == {"path": str(root / "gru_research/r2_comparison/tool.py"), "bytes": 42976, "sha256": tool_sha},
            "frozen comparison tool descriptor differs")
    require(Path(tool["path"]).stat().st_size == tool["bytes"], "tool size differs")
    read_small(tool["path"], tool["sha256"])
    sealed = contract["source_manifest"]
    require(sealed == {"path": str(root / "gru_research/r2_comparison/source_manifest_v2.json"),
            "bytes": 552, "sha256": manifest_sha}
            and arguments["source_manifest"] == sealed["path"]
            and arguments["expected_source_manifest_sha256"] == manifest_sha, "exact comparison manifest binding differs")
    require(Path(sealed["path"]).stat().st_size == sealed["bytes"], "comparison manifest size differs")
    manifest = json.loads(read_small(sealed["path"], sealed["sha256"]))
    expected_sources = {"gru_research/r2_comparison/__init__.py": init_sha,
                        "gru_research/r2_comparison/tool.py": tool_sha,
                        "gru_research/r2_comparison/protocol_v1.json": protocol_sha}
    require(manifest["protocol_sha256"] == protocol_sha and manifest["files_sha256"] == expected_sources,
            "exact three-source comparison roster differs")
    for relative in ("gru_research/r2_comparison/__init__.py", "gru_research/r2_comparison/protocol_v1.json"):
        read_small(root / relative, expected_sources[relative])
    runtime_seal = contract["r2_source_manifest"]
    require(runtime_seal == {"path": str(root / "validation_receipts/r2_source_manifest_v1.json"),
            "bytes": 1137, "sha256": runtime_seal_sha}
            and arguments["r2_source_manifest"] == runtime_seal["path"], "submitted runtime v1 seal role differs")
    require(Path(runtime_seal["path"]).stat().st_size == runtime_seal["bytes"], "runtime manifest size differs")
    read_small(runtime_seal["path"], runtime_seal["sha256"])
    ref = contract["root_actual35_admission"]
    require(type(ref) is dict and type(ref["bytes"]) is int and 0 < ref["bytes"] <= 4 * 1024**2
            and Path(ref["path"]).is_absolute() and Path(ref["path"]).stat().st_size == ref["bytes"],
            "actual small root finite/freeze admission descriptor required")
    admitted = json.loads(read_small(ref["path"], ref["sha256"]))
    require(admitted["schema_version"] == "ROOT_ACTUAL_R2_FIXED35_FINITE_FREEZE_ADMISSION_V1"
            and admitted["root_accepted"] is True and admitted["all35finite"] is True
            and type(admitted["diagnostic_complete_count"]) is int and admitted["diagnostic_complete_count"] == 30
            and type(admitted["final_complete_count"]) is int and admitted["final_complete_count"] == 5
            and admitted["actual_auditor_source_sha256"] == auditor_sha
            and admitted["admitted_actions"] == [action], "root action-specific actual35 finite gate absent")
    require(admitted["original_source_root"] == str(root)
            and admitted["original_run_directory"] == arguments["r2_original_run_path"]
            and admitted["freeze_sha256"] == arguments["expected_r2_freeze_sha256"], "root original run/freeze binding differs")
    require(admitted["research_reports_complete"] is True
            and admitted["research_periods"] == ["validation_2024", "historical_2025", "recent_2026"],
            "root completion of the exact three research periods required; child must still verify their actual bodies")
    native_ref = admitted["actual_native_capture_receipt"]
    require(type(native_ref) is dict and Path(native_ref["path"]).is_absolute()
            and type(native_ref["bytes"]) is int and 0 < native_ref["bytes"] <= 4 * 1024**2
            and type(native_ref["sha256"]) is str and len(native_ref["sha256"]) == 64
            and all(c in "0123456789abcdef" for c in native_ref["sha256"]), "actual root-bound native capture identity required")
    freeze_id = admitted["freeze_id"]
    require(type(freeze_id) is str and len(freeze_id) == 24 and all(c in "0123456789abcdef" for c in freeze_id),
            "actual content-bound freeze ID required")
    audit_ref = admitted["actual_audit_receipt"]
    require(type(audit_ref) is dict and Path(audit_ref["path"]).is_absolute()
            and type(audit_ref["bytes"]) is int and 0 < audit_ref["bytes"] <= 4 * 1024**2
            and type(audit_ref["sha256"]) is str and len(audit_ref["sha256"]) == 64
            and all(c in "0123456789abcdef" for c in audit_ref["sha256"]), "root actual finite audit identity required")
    # The actual audit body is root-reviewed; this wrapper does not re-audit it,
    # checkpoints, arrays, the25MB runtime binary or any score CSV.
    runtime = contract["runtime_current_identity"]
    require(type(runtime) is dict and runtime == admitted["runtime_current_identity"]
            and admitted["runtime_current_identity_verified"] is True
            and type(runtime["bytes"]) is int and runtime["bytes"] > 0
            and type(runtime["sha256"]) is str and len(runtime["sha256"]) == 64
            and all(c in "0123456789abcdef" for c in runtime["sha256"]), "fresh root runtime identity required")
    interpreter = Path(contract["python_runtime"])
    require(str(interpreter) == runtime["path"] and interpreter.is_absolute() and interpreter.is_file()
            and str(interpreter.resolve()) == runtime["path"]
            and interpreter.stat().st_size == runtime["bytes"], "actual binary path/size differs from root admission")
    projection = root / "outputs/r2_comparison_projection_v1"
    output = projection if action == "export" else root / "outputs/r2_comparison_common_v1"
    capture_directory = root / f"outputs/r2_comparison_{action}_capture_v1/attempt1"
    require(arguments["output"] == str(output) and contract["expected_action_output_directory"] == str(output)
            and not output.exists(), "fixed first-action output must be absent; no retries or alternate output")
    require(contract["capture_directory"] == str(capture_directory)
            and str(args.capture_directory.resolve()) == str(capture_directory)
            and args.capture_directory.is_absolute() and not args.capture_directory.exists(), "fixed exclusive capture namespace differs")
    if action == "compare":
        require(arguments["export_directory"] == str(projection), "compare must use the fixed original projection output")
        export_ref = admitted["actual_export_manifest"]
        require(type(export_ref) is dict and export_ref["path"] == str(projection / "manifest.json")
                and export_ref["sha256"] == arguments["expected_export_manifest_sha256"]
                and type(export_ref["bytes"]) is int and 0 < export_ref["bytes"] <= 4 * 1024**2
                and admitted["projection_export_independently_accepted"] is True,
                "actual root-accepted first projection manifest required before compare")
    require(type(contract["child_hard_deadline_seconds"]) is int
            and contract["child_hard_deadline_seconds"] == 21600, "fixed21600 second owned wait required")
    require(contract["required_child_environment"] == {"CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1"},
            "fixed CPU environment required; no caller-provided arbitrary environment")
    argv = [str(interpreter), "-B", "-m", "gru_research.r2_comparison.tool", "--execute", "--action", action,
            "--device", "cpu", "--timeout-seconds", "21600"]
    ordered = ("root", "dataset", "r2_run", "source_manifest", "r2_source_manifest", "output",
               "expected_source_manifest_sha256", "expected_r2_freeze_sha256", "r2_original_run_path")
    if action == "compare":
        ordered += ("r1_formal", "r1_export_manifest", "export_directory", "expected_export_manifest_sha256")
    for key in ordered:
        argv.extend(["--" + key.replace("_", "-"), arguments[key]])
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["OMP_NUM_THREADS"] = "1"
    args.capture_directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    capture = {"status": "STARTING_ONCE", "started_utc": now(), "PID": os.getpid(), "argv": argv, "cwd": str(root),
               "launch_contract_sha256": args.launch_contract_sha256, "automatic_retry": False,
               "GPU_server_or_existing_process_groups_touched": False, "full_PLAN_complete": False,
               "action": action, "fixed_scope": contract["fixed_scope"], "tool_source_sha256": tool_sha,
               "source_manifest_sha256": manifest_sha, "runtime_manifest_sha256": runtime_seal_sha,
               "root_actual35_admission": ref, "actual_audit_receipt_root_bound_not_reread": audit_ref,
               "actual_native_capture_receipt_root_bound_not_reread": native_ref,
               "research_reports_root_admitted_child_actual_gate_still_required": admitted["research_periods"],
               "freeze_sha256": admitted["freeze_sha256"], "freeze_id": freeze_id,
               "runtime_current_identity_root_bound_not_body_read": runtime,
               "child_environment_requested": {"CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1"},
               "child_environment_applied_at_spawn": False,
               "wrapper_model_forward_calls": 0, "additional_fits": 0, "model_selection": False,
               "projection_or_comparison_acceptance_inferred": False}
    receipt_path = args.capture_directory / "root_once_execution_capture_v1.json"
    write_json(receipt_path, capture)
    proc, group, failure = None, None, None
    try:
        with (args.capture_directory / "stdout.log").open("xb") as stdout:
            with (args.capture_directory / "stderr.log").open("xb") as stderr:
                proc = subprocess.Popen(argv, stdout=stdout, stderr=stderr, env=env, cwd=str(root), start_new_session=True)
                # Preserve ownership immediately after the successful spawn.
                group = proc.pid
                capture["child_environment_applied_at_spawn"] = True
                observed_group = os.getpgid(proc.pid)
                require(observed_group == group, "child session not independently owned")
                capture.update({"status": "RUNNING", "child_PID": proc.pid, "owned_PGID": group,
                                "child_hard_deadline_seconds": contract["child_hard_deadline_seconds"]})
                write_json(receipt_path, capture)
                remaining = max(0.01, contract["child_hard_deadline_seconds"] - (time.monotonic() - started))
                capture["child_exit_code"] = proc.wait(timeout=remaining)
                capture["status"] = "CHILD_EXITED"
    except BaseException as exc:
        failure = {"type": type(exc).__name__, "message": str(exc)}
        capture["status"] = "FAILED_OR_HARD_DEADLINE_WITH_OWNED_CHILD_CLEANUP"
        if proc is not None and group == proc.pid:
            try:
                capture["owned_group_cleanup"] = terminate_owned(proc, group)
                capture["child_exit_code"] = proc.poll()
            except BaseException as cleanup_exc:
                capture["owned_group_cleanup"] = {
                    "cleanup_failure": {"type": type(cleanup_exc).__name__, "message": str(cleanup_exc)},
                    "remaining_group_state": "UNKNOWN_CLEANUP_EXCEPTION"}
                capture["child_exit_code"] = proc.poll()
    finally:
        capture["finished_utc"] = now()
        capture["elapsed_seconds"] = time.monotonic() - started
        capture["failure"] = failure
        capture["logs"] = {}
        for name in ("stdout.log", "stderr.log"):
            path = args.capture_directory / name
            if path.exists():
                size = path.stat().st_size
                item = {"path": str(path), "bytes": size}
                if size <= 4 * 1024**2:
                    item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                else:
                    item["sha256"] = "NOT_READ_LOG_BUDGET_EXCEEDED"
                capture["logs"][name] = item
        child_receipt = output / ("manifest.json" if action == "export" else "comparison.json")
        capture["child_main_receipt_path"] = str(child_receipt)
        capture["child_main_receipt_present"] = child_receipt.is_file()
        capture["child_main_receipt_success_is_not_inferred_from_exit_or_presence"] = True
        capture["deadline_scope"] = "21600 seconds to owned child wait; bounded TERM10s/KILL5s cleanup and local receipt/log sealing may extend capture elapsed; preflight filesystem reads are not claimed OS interruptible"
        capture["small_read_scope"] = "Inherited read_small helper:4MiB preSTAT check then ordinary SHA-bound read of controlled small sources; not an absolute memory bound for adversarial growth during read"
        capture["checkpoint_array_CSV_or_runtime_binary_body_reads_by_wrapper"] = 0
        capture["other_existing_process_groups_signalled"] = False
        write_json(receipt_path, capture)
    print(json.dumps({"status": capture["status"], "child_exit_code": capture.get("child_exit_code"),
                      "receipt": str(receipt_path)}), flush=True)
    return 1 if failure or capture.get("child_exit_code") != 0 else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-contract", type=Path, required=True)
    parser.add_argument("--launch-contract-sha256", required=True)
    parser.add_argument("--capture-directory", type=Path, required=True)
    raise SystemExit(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
